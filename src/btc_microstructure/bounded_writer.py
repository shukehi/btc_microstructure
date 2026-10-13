"""Isolated, acknowledged R2-04 writer. Timeouts never cancel an fsync thread."""

from __future__ import annotations

import base64
from copy import deepcopy
from decimal import Decimal
import hashlib
import io
import json
import multiprocessing as mp
from multiprocessing.connection import Connection
import os
import resource
import re
from pathlib import Path
import select
import shutil
import stat
import struct
import sys
import threading
import time
from typing import Any, Callable, NamedTuple

import pyarrow.parquet as pq

from . import offline_storage
from .offline_storage import (
    MANIFEST_SCHEMA_VERSION,
    Store,
    StorageError,
    _arrow_table,
    _fsync_dir,
    _json_line,
    _json_object_hook,
)

MIB = 1_048_576
GIB = 1024 * MIB
STARTUP_FREE_BYTES = 2_560 * MIB
WARN_FREE_BYTES = 2_304 * MIB
STOP_FREE_BYTES = 2 * GIB
MAX_TERMINAL_AUDIT_RECORDS = 256
TERMINAL_AUDIT_RECORD_BYTES = 4096
MAX_MANIFEST_BYTES = 8192
MAX_PARQUET_TEMP_BYTES = 64 * MIB
MAX_COMMAND_BYTES = 128 * MIB
MAX_TABLE_COLUMNS = 128
_JSON_ASCII_ESCAPE = re.compile(r'["\\\x00-\x1f\x7f]')
MAX_AUDIT_FILES = 16_384
MAX_AUDIT_DEPTH = 16
MAX_RAW_AUDIT_LINE_BYTES = 98_304
MAX_JSON_BOUND_DEPTH = 32
PARTITIONS = {
    "rest_metadata": 8 * MIB,
    "ws_raw_envelopes": 192 * MIB,
    "control": 16 * MIB,
    "manifests_indexes": 32 * MIB,
    "normalized_and_quarantine": 96 * MIB,
    "feature_parquet": 16 * MIB,
    "temporary_orphan_quarantine_files": 112 * MIB,
    "diagnostics_and_receipts": 40 * MIB,
}


class WriterFailure(StorageError):
    """No further writes allowed; last acknowledged cursor remains conservative."""


class _PhysicalAudit(NamedTuple):
    charges: dict[str, int]
    bytes: int
    files: int


def _empty_charges() -> dict[str, int]:
    return dict.fromkeys(PARTITIONS, 0)


def _rss_bytes() -> int:
    observed = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return observed if sys.platform == "darwin" else observed * 1024


def _shared_int(value: Any) -> int:
    return int(value.value)


def _set_shared_max(value: Any, candidate: int) -> None:
    if candidate > int(value.value):
        value.value = candidate


def _inside(root: Path, candidate: Path) -> bool:
    resolved_root = root.resolve(strict=True)
    resolved_candidate = candidate.resolve(strict=False)
    return resolved_candidate == resolved_root or resolved_root in resolved_candidate.parents


def _row_segment_class(row: dict[str, Any]) -> str:
    kind = row.get("kind")
    if not isinstance(kind, str) or not kind:
        raise WriterFailure("INVALID_SEGMENT_KIND")
    return "RAW" if kind == "RAW" else "CONTROL"


def _batch_segment_class(rows: list[dict[str, Any]]) -> str:
    if not rows:
        raise WriterFailure("EMPTY_SEGMENT")
    classes = {_row_segment_class(row) for row in rows}
    if len(classes) != 1:
        raise WriterFailure("MIXED_SEGMENT_CLASS")
    return classes.pop()


def _raw_file_segment_class(path: Path) -> str | None:
    classes: set[str] = set()
    try:
        with path.open("rb") as handle:
            for line in handle:
                if len(line) > MAX_RAW_AUDIT_LINE_BYTES:
                    return None
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    return None
                if not isinstance(row, dict):
                    return None
                try:
                    classes.add(_row_segment_class(row))
                except WriterFailure:
                    return None
                if len(classes) > 1:
                    return None
    except OSError:
        return None
    return classes.pop() if len(classes) == 1 else None


def _charge_raw_file(path: Path, size: int) -> str:
    _ = size
    segment_class = _raw_file_segment_class(path)
    if segment_class == "RAW":
        return "ws_raw_envelopes"
    if segment_class == "CONTROL":
        return "control"
    return "temporary_orphan_quarantine_files"


def _charge_table_file(path: Path) -> str:
    table_name = path.parent.name
    return "feature_parquet" if table_name.startswith("features") else "normalized_and_quarantine"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trusted_manifest_charges(root: Path) -> dict[str, str]:
    trusted: dict[str, str] = {}
    manifest_dir = root / "manifests"
    if not manifest_dir.exists() or manifest_dir.is_symlink() or not manifest_dir.is_dir():
        return trusted
    for manifest_path in sorted(manifest_dir.glob("*.json")):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8"), object_hook=_json_object_hook
            )
            if not isinstance(manifest, dict):
                continue
            if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
                continue
            path_value = manifest.get("path")
            expected_sha = manifest.get("sha256")
            if not isinstance(path_value, str) or not isinstance(expected_sha, str):
                continue
            data_path = root / path_value
            if not _inside(root, data_path) or data_path.is_symlink() or not data_path.is_file():
                continue
            if _sha256_file(data_path) != expected_sha:
                continue
            kind = manifest.get("kind")
            if (
                kind == "raw_control_segment"
                and data_path.parent == root / "raw"
                and data_path.name.startswith("raw-control-")
                and data_path.name.endswith(".jsonl")
            ):
                actual_class = _raw_file_segment_class(data_path)
                declared_class = manifest.get("segment_class")
                if declared_class is not None and declared_class != actual_class:
                    continue
                if actual_class not in {"RAW", "CONTROL"}:
                    continue
                trusted[data_path.relative_to(root).as_posix()] = (
                    "ws_raw_envelopes" if actual_class == "RAW" else "control"
                )
            elif (
                kind == "derived_table"
                and data_path.name.endswith(".parquet")
                and len(data_path.relative_to(root).parts) >= 3
                and data_path.relative_to(root).parts[0] == "tables"
            ):
                trusted[data_path.relative_to(root).as_posix()] = _charge_table_file(data_path)
        except (OSError, ValueError, TypeError, StorageError):
            continue
    return trusted


def _charge_file(root: Path, path: Path, size: int, trusted_charges: dict[str, str]) -> str:
    relative = path.relative_to(root)
    relative_key = relative.as_posix()
    parts = relative.parts
    name = path.name
    if name.endswith(".tmp"):
        return "temporary_orphan_quarantine_files"
    if name in {"writer-open", "writer-closed"}:
        return "diagnostics_and_receipts"
    if parts[0] == "errors":
        return "diagnostics_and_receipts"
    if parts[0] == "snapshots":
        return "rest_metadata"
    if parts[0] == "manifests" and name.endswith(".json"):
        return "manifests_indexes"
    if parts[0] == "raw" and name.startswith("raw-control-") and name.endswith(".jsonl"):
        return trusted_charges.get(relative_key, "temporary_orphan_quarantine_files")
    if parts[0] == "tables" and len(parts) >= 3 and name.endswith(".parquet"):
        return trusted_charges.get(relative_key, "temporary_orphan_quarantine_files")
    if parts[0] == "quarantine":
        return "temporary_orphan_quarantine_files"
    return "temporary_orphan_quarantine_files"


def _audit_physical_root(root: Path) -> _PhysicalAudit:
    root_stat = root.lstat()
    if stat.S_ISLNK(root_stat.st_mode):
        raise WriterFailure("PHYSICAL_AUDIT_SYMLINK")
    root = root.resolve(strict=True)
    trusted_charges = _trusted_manifest_charges(root)
    charges = _empty_charges()
    entries_seen = 0
    files = 0
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        if depth > MAX_AUDIT_DEPTH:
            raise WriterFailure("PHYSICAL_AUDIT_DEPTH")
        with os.scandir(directory) as entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > MAX_AUDIT_FILES:
                    raise WriterFailure("PHYSICAL_AUDIT_FILE_COUNT")
                path = Path(entry.path)
                stat_result = entry.stat(follow_symlinks=False)
                mode = stat_result.st_mode
                if stat.S_ISLNK(mode):
                    raise WriterFailure("PHYSICAL_AUDIT_SYMLINK")
                if not _inside(root, path):
                    raise WriterFailure("PHYSICAL_AUDIT_ESCAPE")
                if stat.S_ISDIR(mode):
                    stack.append((path, depth + 1))
                    continue
                if not stat.S_ISREG(mode):
                    raise WriterFailure("PHYSICAL_AUDIT_SPECIAL")
                files += 1
                partition = _charge_file(root, path, stat_result.st_size, trusted_charges)
                charges[partition] += stat_result.st_size
    return _PhysicalAudit(charges=charges, bytes=sum(charges.values()), files=files)


def _check_root_path(root: Path) -> None:
    for parent in reversed(root.parents):
        if not parent.exists():
            continue
        stat_result = parent.lstat()
        if stat.S_ISLNK(stat_result.st_mode) or not stat.S_ISDIR(stat_result.st_mode):
            raise WriterFailure("PHYSICAL_AUDIT_SYMLINK")
    if root.exists():
        stat_result = root.lstat()
        if stat.S_ISLNK(stat_result.st_mode):
            raise WriterFailure("PHYSICAL_AUDIT_SYMLINK")
        if not stat.S_ISDIR(stat_result.st_mode):
            raise WriterFailure("PHYSICAL_AUDIT_SPECIAL")


def _json_bound(value: Any, *, depth: int = 0, seen: set[int] | None = None) -> int:
    if depth > MAX_JSON_BOUND_DEPTH:
        raise WriterFailure("WRITER_COMMAND_LIMIT")
    if seen is None:
        seen = set()
    if value is None or isinstance(value, bool):
        return 5
    if isinstance(value, int):
        return int(value.bit_length() * 0.302 + 4)
    if isinstance(value, float):
        return 32
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise WriterFailure("WRITER_COMMAND_LIMIT")
        if not -76 <= value.adjusted() < 76:
            raise WriterFailure("WRITER_COMMAND_LIMIT")
        _, digits, exponent = value.as_tuple()
        if not isinstance(exponent, int):
            raise WriterFailure("WRITER_COMMAND_LIMIT")
        scale = max(-exponent, 0)
        precision = max(len(digits) + max(exponent, 0), scale + 1)
        if precision > 76:
            raise WriterFailure("WRITER_COMMAND_LIMIT")
        return precision + 32
    if isinstance(value, str):
        return _json_string_bound(value)
    if isinstance(value, list):
        identity = id(value)
        if identity in seen:
            raise WriterFailure("WRITER_COMMAND_LIMIT")
        seen.add(identity)
        total = 2
        for item in value:
            total += _json_bound(item, depth=depth + 1, seen=seen) + 1
            if total > MAX_COMMAND_BYTES:
                return total
        seen.remove(identity)
        return total
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            raise WriterFailure("WRITER_COMMAND_LIMIT")
        seen.add(identity)
        total = 2
        for key, item in value.items():
            total += (
                _json_bound(str(key), depth=depth + 1, seen=seen)
                + _json_bound(item, depth=depth + 1, seen=seen)
                + 2
            )
            if total > MAX_COMMAND_BYTES:
                return total
        seen.remove(identity)
        return total
    return len(str(value)) * 12 + 32


def _json_string_bound(value: str) -> int:
    if len(value) > MAX_COMMAND_BYTES:
        return MAX_COMMAND_BYTES + 1
    if value.isascii() and _JSON_ASCII_ESCAPE.search(value) is None:
        return len(value) + 2
    total = 2
    for char in value:
        codepoint = ord(char)
        if char in {'"', "\\"}:
            total += 2
        elif char in {"\b", "\f", "\n", "\r", "\t"}:
            total += 2
        elif codepoint < 0x20:
            total += 6
        elif codepoint < 0x7F:
            total += 1
        elif codepoint <= 0xFFFF:
            total += 6
        else:
            total += 12
        if total > MAX_COMMAND_BYTES:
            return total
    return total


def _rows_json_bound(rows: list[dict[str, Any]]) -> int:
    total = 0
    for row in rows:
        total += _json_bound(row) + 1
        if total > MAX_COMMAND_BYTES:
            return total
    return total


def _request_encoded_upper_bound(request: dict[str, Any]) -> int:
    operation = str(request.get("operation", ""))
    if operation == "batch":
        rows = request.get("rows", [])
        if isinstance(rows, list):
            return _rows_json_bound(rows) + 4096
    if operation == "table":
        rows = request.get("rows", [])
        name = str(request.get("name", ""))
        if isinstance(rows, list):
            return _rows_json_bound(rows) + len(name) + 4096
    if operation == "snapshot":
        path = str(request.get("path", ""))
        encoded = str(request.get("base64", ""))
        return len(path) + len(encoded) + 4096
    return 4096


def _parquet_cap(rows: list[dict[str, Any]]) -> int:
    encoded_rows = _rows_json_bound(rows)
    columns: set[str] = set()
    for row in rows:
        if len(row) > MAX_TABLE_COLUMNS:
            raise WriterFailure("TABLE_COLUMN_LIMIT")
        for column in row:
            if not isinstance(column, str):
                raise WriterFailure("TABLE_COLUMN_LIMIT")
            columns.add(column)
            if len(columns) > MAX_TABLE_COLUMNS:
                raise WriterFailure("TABLE_COLUMN_LIMIT")
    # Parquet stores per-column pages, statistics and footer metadata even for
    # one row. Reserve this fixed cost before constructing any Arrow objects.
    requested = encoded_rows * 4 + MAX_MANIFEST_BYTES + len(columns) * 4096
    if requested > MAX_PARQUET_TEMP_BYTES:
        raise WriterFailure("PARQUET_TEMP_LIMIT")
    return max(MAX_MANIFEST_BYTES, requested)


class Budget:
    def __init__(
        self,
        total: int = 512 * MIB,
        free: int = 0,
        terminal_audit_reserve_bytes: int = 0,
    ) -> None:
        if terminal_audit_reserve_bytes < 0:
            raise WriterFailure("INVALID_TERMINAL_AUDIT_RESERVE")
        if terminal_audit_reserve_bytes > PARTITIONS["diagnostics_and_receipts"]:
            raise WriterFailure("INVALID_TERMINAL_AUDIT_RESERVE")
        self.total = total
        self.free = free
        self.terminal_audit_reserve_bytes = terminal_audit_reserve_bytes
        self.used = dict.fromkeys(PARTITIONS, 0)
        self.terminal_audit_records = 0
        self.terminal_audit_bytes = 0

    def set_physical_used(self, charges: dict[str, int]) -> None:
        if set(charges) != set(self.used) or any(value < 0 for value in charges.values()):
            raise WriterFailure("INVALID_PHYSICAL_AUDIT")
        if sum(charges.values()) > self.total:
            raise WriterFailure("RUN_BYTE_LIMIT")
        for key, value in charges.items():
            if value > PARTITIONS[key]:
                raise WriterFailure(f"PARTITION_LIMIT:{key}")
        self.used = dict(charges)

    def reserve(self, charges: dict[str, int], disk_free: int) -> None:
        if any(value < 0 or key not in self.used for key, value in charges.items()):
            raise WriterFailure("INVALID_RESERVATION")
        normal_total = self.total - self.terminal_audit_reserve_bytes
        audit_charge = charges.get("diagnostics_and_receipts", 0)
        if (
            audit_charge
            and self.terminal_audit_reserve_bytes
            and self.used["diagnostics_and_receipts"] + audit_charge
            > PARTITIONS["diagnostics_and_receipts"] - self.terminal_audit_reserve_bytes
        ):
            raise WriterFailure("TERMINAL_AUDIT_RESERVE")
        required = sum(charges.values())
        if sum(self.used.values()) + required > normal_total:
            raise WriterFailure("RUN_BYTE_LIMIT")
        if disk_free - required < self.free:
            raise WriterFailure("DISK_FREE_LIMIT")
        for key, amount in charges.items():
            if self.used[key] + amount > PARTITIONS[key]:
                raise WriterFailure(f"PARTITION_LIMIT:{key}")
        for key, amount in charges.items():
            self.used[key] += amount

    def reserve_terminal_audit(self, record_bytes: int, disk_free: int) -> None:
        if record_bytes < 0 or record_bytes > TERMINAL_AUDIT_RECORD_BYTES:
            raise WriterFailure("TERMINAL_AUDIT_RECORD_LIMIT")
        if self.terminal_audit_records >= MAX_TERMINAL_AUDIT_RECORDS:
            raise WriterFailure("TERMINAL_AUDIT_RECORDS")
        if self.terminal_audit_bytes + record_bytes > self.terminal_audit_reserve_bytes:
            raise WriterFailure("TERMINAL_AUDIT_RESERVE")
        if self.terminal_audit_reserve_bytes <= 0:
            raise WriterFailure("TERMINAL_AUDIT_RESERVE")
        if disk_free - record_bytes < self.free:
            raise WriterFailure("DISK_FREE_LIMIT")
        if (
            self.used["diagnostics_and_receipts"] + record_bytes
            > PARTITIONS["diagnostics_and_receipts"]
        ):
            raise WriterFailure("PARTITION_LIMIT:diagnostics_and_receipts")
        if sum(self.used.values()) + record_bytes > self.total:
            raise WriterFailure("RUN_BYTE_LIMIT")
        self.used["diagnostics_and_receipts"] += record_bytes
        self.terminal_audit_records += 1
        self.terminal_audit_bytes += record_bytes

    def release(self, charges: dict[str, int]) -> None:
        for key, amount in charges.items():
            self.used[key] -= amount


class _CappedBuffer(io.BytesIO):
    def __init__(self, cap: int) -> None:
        super().__init__()
        self.cap = cap

    def write(self, value: Any) -> int:
        if self.tell() + len(value) > self.cap:
            raise WriterFailure("PARQUET_TEMP_LIMIT")
        return super().write(value)


def _worker(
    connection: Connection,
    acknowledgement: Connection,
    root: str,
    metadata: dict[str, Any],
    total: int,
    free: int,
    fault: str | None,
    rss_stop_bytes: int,
    rss_peak_value: Any,
    rss_sample_count: Any,
    rss_exceeded: Any,
) -> None:
    store: Store | None = None
    budget: Budget | None = None
    metrics: dict[str, Any] = {}
    monitor: threading.Thread | None = None
    stop_monitor = threading.Event()
    original_manifest: Any | None = None
    try:
        _check_root_path(Path(root))
        store = Store(Path(root), run_metadata=metadata)
        budget = Budget(total, free, terminal_audit_reserve_bytes=MIB)
        metrics = {
            "audited_bytes": 0,
            "audited_files": 0,
            "last_free_bytes": shutil.disk_usage(root).free,
            "storage_warning": False,
            "storage_warning_receipt": False,
            "terminal_audit_records": 0,
            "terminal_audit_bytes": 0,
            "queue_peak_records": 0,
            "queue_peak_bytes": 0,
        }
        pending_storage_warning: int | None = None
        wrote_storage_warning = False

        def current_free() -> int:
            nonlocal pending_storage_warning
            disk_free = int(shutil.disk_usage(root).free)
            metrics["last_free_bytes"] = disk_free
            if disk_free < WARN_FREE_BYTES:
                metrics["storage_warning"] = True
                pending_storage_warning = disk_free
            if disk_free < free:
                raise WriterFailure("DISK_FREE_LIMIT")
            return disk_free

        def audit_root() -> None:
            audit = _audit_physical_root(Path(root))
            budget.set_physical_used(audit.charges)
            metrics["audited_bytes"] = audit.bytes
            metrics["audited_files"] = audit.files

        def reserve(charges: dict[str, int]) -> None:
            audit_root()
            budget.reserve(charges, current_free())

        def after_write_audit() -> None:
            nonlocal pending_storage_warning, wrote_storage_warning
            audit_root()
            current_free()
            if pending_storage_warning is None or wrote_storage_warning:
                return
            record = _json_line(
                {
                    "event_type": "DISK_FREE_WARNING",
                    "ts_receive_ns": time.time_ns(),
                    "ingest_sequence": store.cursor if store else None,
                    "free_bytes": pending_storage_warning,
                    "threshold_bytes": WARN_FREE_BYTES,
                    "trusted_cursor": store.cursor if store else None,
                }
            )
            if len(record) > TERMINAL_AUDIT_RECORD_BYTES:
                raise WriterFailure("TERMINAL_AUDIT_RECORD_LIMIT")
            budget.reserve_terminal_audit(len(record), pending_storage_warning)
            error_log = Path(root) / "errors" / "storage_events.jsonl"
            error_log.parent.mkdir(parents=True, exist_ok=True)
            with error_log.open("ab") as handle:
                handle.write(record)
                handle.flush()
                os.fsync(handle.fileno())
            metrics["terminal_audit_records"] = budget.terminal_audit_records
            metrics["terminal_audit_bytes"] = budget.terminal_audit_bytes
            metrics["storage_warning_receipt"] = True
            wrote_storage_warning = True
            pending_storage_warning = None
            audit_root()

        audit_root()
        if free >= STOP_FREE_BYTES and current_free() < STARTUP_FREE_BYTES:
            raise WriterFailure("STARTUP_DISK_FREE_LIMIT")

        def audit(event_type: str, payload: dict[str, Any]) -> None:
            event = {
                "event_type": event_type,
                "ts_receive_ns": time.time_ns(),
                "ingest_sequence": store.cursor if store else None,
                **payload,
            }
            record = _json_line(event)
            record_bytes = len(record)
            if record_bytes > 3072:
                raise WriterFailure("AUDIT_RECORD_LIMIT")
            audit_root()
            try:
                budget.reserve(
                    {"diagnostics_and_receipts": TERMINAL_AUDIT_RECORD_BYTES}, current_free()
                )
            except WriterFailure:
                budget.reserve_terminal_audit(record_bytes, current_free())
                metrics["terminal_audit_records"] = budget.terminal_audit_records
                metrics["terminal_audit_bytes"] = budget.terminal_audit_bytes
            error_log = Path(root) / "errors" / "storage_events.jsonl"
            error_log.parent.mkdir(parents=True, exist_ok=True)
            with error_log.open("ab") as handle:
                handle.write(record)
                handle.flush()
                os.fsync(handle.fileno())
            after_write_audit()

        store._record_error = audit  # type: ignore[method-assign]
        original_manifest = offline_storage._write_json_atomic

        def write_manifest(path: Path, payload: dict[str, Any]) -> None:
            if payload.get("kind") == "raw_control_segment":
                data_path = Path(root) / str(payload.get("path", ""))
                segment_class = _raw_file_segment_class(data_path)
                if segment_class not in {"RAW", "CONTROL"}:
                    raise WriterFailure("MIXED_SEGMENT_CLASS")
                payload["segment_class"] = segment_class
            encoded = json.dumps(
                payload, indent=2, sort_keys=True, default=offline_storage._json_default
            ).encode()
            if len(encoded) + 1 > 8192:
                raise WriterFailure("MANIFEST_BYTE_LIMIT")
            original_manifest(path, payload)

        offline_storage._write_json_atomic = write_manifest
        budget.reserve_terminal_audit(5, current_free())
        metrics["terminal_audit_records"] = budget.terminal_audit_records
        metrics["terminal_audit_bytes"] = budget.terminal_audit_bytes
        with (Path(root) / "writer-open").open("xb") as handle:
            handle.write(b"open\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(Path(root))
        after_write_audit()
        manifest_count = 0

        def rss_peak() -> int:
            value = _rss_bytes()
            _set_shared_max(rss_peak_value, value)
            rss_sample_count.value = int(rss_sample_count.value) + 1
            if value > rss_stop_bytes:
                rss_exceeded.value = 1
                raise WriterFailure("WORKER_RSS_LIMIT")
            return value

        def monitor_rss() -> None:
            while not stop_monitor.wait(0.1):
                try:
                    rss_peak()
                except WriterFailure:
                    return

        monitor = threading.Thread(target=monitor_rss, daemon=True)
        monitor.start()

        acknowledgement.send_bytes(
            json.dumps({"ok": True, "cursor": None, "worker_rss_peak": rss_peak()}).encode()
        )
        while True:
            request = json.loads(connection.recv_bytes(), object_hook=_json_object_hook)
            rss_peak()
            fault = request.get("fault", fault)
            operation = request["operation"]
            if fault == "hang":
                while True:
                    time.sleep(0.1)

            def inject(stage: str) -> None:
                if fault == stage:
                    raise OSError(f"injected {stage}")

            if operation == "batch":
                if manifest_count >= 4096:
                    raise WriterFailure("MANIFEST_COUNT_LIMIT")
                rows = request["rows"]
                segment_class = _batch_segment_class(rows)
                size = sum(len(_json_line(row)) for row in rows)
                metrics["queue_peak_records"] = max(metrics["queue_peak_records"], len(rows))
                metrics["queue_peak_bytes"] = max(metrics["queue_peak_bytes"], size)
                category = "ws_raw_envelopes" if segment_class == "RAW" else "control"
                charges = {
                    category: size,
                    "temporary_orphan_quarantine_files": size + 8192,
                    "manifests_indexes": MAX_MANIFEST_BYTES,
                }
                reserve(charges)
                inject("append")
                for row in rows:
                    store.append(row)
                inject("flush")
                original_fsync, original_replace = os.fsync, os.replace

                def fsync(fd: Any) -> None:
                    inject("fsync")
                    original_fsync(fd)

                def replace(
                    src: Any,
                    dst: Any,
                    *,
                    src_dir_fd: int | None = None,
                    dst_dir_fd: int | None = None,
                ) -> None:
                    inject("manifest" if "manifest" in str(dst) else "rename")
                    original_replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

                os.fsync, os.replace = fsync, replace
                try:
                    manifest = store.flush()
                finally:
                    os.fsync, os.replace = original_fsync, original_replace
                budget.release({"temporary_orphan_quarantine_files": size + 8192})
                after_write_audit()
                manifest_count += 1
            elif operation == "table":
                if manifest_count >= 4096:
                    raise WriterFailure("MANIFEST_COUNT_LIMIT")
                name = request["name"]
                category = (
                    "feature_parquet"
                    if name.startswith("features")
                    else "normalized_and_quarantine"
                )
                parquet_cap = _parquet_cap(request["rows"])
                temp_reservation = parquet_cap
                charges = {
                    category: parquet_cap,
                    "temporary_orphan_quarantine_files": temp_reservation,
                }
                reserve(charges)
                budget.reserve({"manifests_indexes": MAX_MANIFEST_BYTES}, current_free())
                table_memory_cap = _rows_json_bound(request["rows"]) * 4 + parquet_cap * 3
                if _rss_bytes() + table_memory_cap > rss_stop_bytes:
                    rss_exceeded.value = 1
                    raise WriterFailure("WORKER_RSS_LIMIT")
                table = _arrow_table(request["rows"])
                buffer = _CappedBuffer(parquet_cap)
                pq.write_table(table, buffer, compression="zstd")
                encoded = buffer.getvalue()
                original_write = pq.write_table
                original_arrow_table = offline_storage._arrow_table

                def write(table: Any, where: Any, **options: Any) -> None:
                    _ = table, options
                    with Path(where).open("xb") as handle:
                        handle.write(encoded)

                def arrow_table(rows: list[dict[str, Any]]) -> Any:
                    _ = rows
                    return table

                pq.write_table = write
                offline_storage._arrow_table = arrow_table
                try:
                    manifest = store.write_table(name, request["rows"])
                finally:
                    pq.write_table = original_write
                    offline_storage._arrow_table = original_arrow_table
                del buffer, encoded, table
                budget.release({"temporary_orphan_quarantine_files": temp_reservation})
                after_write_audit()
                manifest_count += 1
            elif operation == "snapshot":
                relative = Path(request["path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise WriterFailure("INVALID_SNAPSHOT_PATH")
                encoded_snapshot = request["base64"]
                if not isinstance(encoded_snapshot, str) or len(encoded_snapshot) % 4:
                    raise WriterFailure("INVALID_SNAPSHOT_ENCODING")
                padding = int(encoded_snapshot.endswith("=")) + int(encoded_snapshot.endswith("=="))
                if encoded_snapshot.endswith("==="):
                    raise WriterFailure("INVALID_SNAPSHOT_ENCODING")
                decoded_size = len(encoded_snapshot) // 4 * 3 - padding
                if decoded_size > PARTITIONS["rest_metadata"]:
                    raise WriterFailure("PARTITION_LIMIT:rest_metadata")
                reserve(
                    {
                        "rest_metadata": decoded_size,
                        "temporary_orphan_quarantine_files": decoded_size,
                    },
                )
                if _rss_bytes() + decoded_size * 2 > rss_stop_bytes:
                    raise WriterFailure("WORKER_RSS_ALLOCATION_LIMIT")
                data = base64.b64decode(encoded_snapshot, validate=True)
                if len(data) != decoded_size:
                    raise WriterFailure("INVALID_SNAPSHOT_ENCODING")
                final = Path(root) / relative
                root_path = Path(root).resolve(strict=True)
                parent = final.parent
                if parent.exists() and parent.is_symlink():
                    raise WriterFailure("SNAPSHOT_PATH_ESCAPE")
                resolved_parent = parent.resolve(strict=False)
                if root_path != resolved_parent and root_path not in resolved_parent.parents:
                    raise WriterFailure("SNAPSHOT_PATH_ESCAPE")
                resolved_final = final.resolve(strict=False)
                if root_path != resolved_final and root_path not in resolved_final.parents:
                    raise WriterFailure("SNAPSHOT_PATH_ESCAPE")
                if final.exists():
                    raise WriterFailure("SNAPSHOT_ALREADY_EXISTS")
                final.parent.mkdir(parents=True, exist_ok=True)
                temp = final.with_suffix(final.suffix + ".tmp")
                resolved_temp = temp.resolve(strict=False)
                if root_path != resolved_temp and root_path not in resolved_temp.parents:
                    raise WriterFailure("SNAPSHOT_PATH_ESCAPE")
                with temp.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, final)
                _fsync_dir(final.parent)
                budget.release({"temporary_orphan_quarantine_files": len(data)})
                after_write_audit()
                manifest = None
            elif operation == "close":
                store.close()
                audit_root()
                budget.reserve_terminal_audit(7, current_free())
                metrics["terminal_audit_records"] = budget.terminal_audit_records
                metrics["terminal_audit_bytes"] = budget.terminal_audit_bytes
                with (Path(root) / "writer-closed").open("xb") as handle:
                    handle.write(b"closed\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                _fsync_dir(Path(root))
                after_write_audit()
                acknowledgement.send_bytes(
                    json.dumps(
                        {
                            "ok": True,
                            "cursor": store.cursor,
                            "budget_used": budget.used,
                            "worker_rss_peak": rss_peak(),
                            "metrics": metrics,
                        }
                    ).encode()
                )
                break
            else:
                raise WriterFailure("INVALID_OPERATION")
            # Ack contains only a cursor/receipt; never sends unbounded output rows.
            acknowledgement.send_bytes(
                json.dumps(
                    {
                        "ok": True,
                        "cursor": store.cursor,
                        "manifest": manifest,
                        "budget_used": budget.used,
                        "worker_rss_peak": rss_peak(),
                        "metrics": metrics,
                    }
                ).encode()
            )
    except Exception as exc:
        receipt_status = "NOT_ATTEMPTED"
        if budget is not None:
            try:
                record = _json_line(
                    {
                        "event_type": "WRITER_FAILURE",
                        "ts_receive_ns": time.time_ns(),
                        "ingest_sequence": store.cursor if store else None,
                        "trusted_cursor": store.cursor if store else None,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:256],
                    }
                )
                if len(record) > TERMINAL_AUDIT_RECORD_BYTES:
                    raise WriterFailure("TERMINAL_AUDIT_RECORD_LIMIT")
                budget.reserve_terminal_audit(len(record), int(shutil.disk_usage(root).free))
                error_log = Path(root) / "errors" / "storage_events.jsonl"
                error_log.parent.mkdir(parents=True, exist_ok=True)
                with error_log.open("ab") as handle:
                    handle.write(record)
                    handle.flush()
                    os.fsync(handle.fileno())
                _fsync_dir(error_log.parent)
                metrics["terminal_audit_records"] = budget.terminal_audit_records
                metrics["terminal_audit_bytes"] = budget.terminal_audit_bytes
                metrics["failure_receipt_status"] = "DURABLE"
                receipt_status = "DURABLE"
            except Exception as receipt_exc:
                metrics["failure_receipt_status"] = "FAILED"
                metrics["failure_receipt_error_type"] = type(receipt_exc).__name__
                receipt_status = "FAILED"
        try:
            acknowledgement.send_bytes(
                json.dumps(
                    {
                        "ok": False,
                        "cursor": store.cursor if store else None,
                        "error": f"{str(exc)[:192]}; receipt_status={receipt_status}",
                        "metrics": metrics,
                    }
                ).encode()
            )
        except (OSError, EOFError):
            pass
    finally:
        connection.close()
        acknowledgement.close()
        if monitor is not None:
            stop_monitor.set()
            monitor.join(timeout=0.2)
        if original_manifest is not None:
            offline_storage._write_json_atomic = original_manifest


class ProcessWriter:
    def __init__(
        self,
        root: Path,
        *,
        run_metadata: dict[str, Any] | None = None,
        max_run_bytes: int = 512 * MIB,
        min_free_bytes: int = 2 * 1024 * MIB,
        ack_seconds: float = 2.0,
        fault: str | None = None,
        max_records: int = 1000,
        max_bytes: int = MIB,
        rss_stop_bytes: int = 512 * MIB,
        parent_rss_sampler: Callable[[], int] | None = None,
    ) -> None:
        if ack_seconds <= 0 or ack_seconds > 2 or max_run_bytes <= 0:
            raise ValueError("writer limits must be positive and bounded")
        if root.exists() and any(root.iterdir()):
            raise WriterFailure("RUN_DIRECTORY_ALREADY_EXISTS")
        self.root = root
        self.ack_seconds = ack_seconds
        self.cursor: int | None = None
        self.failed = False
        self.closed = False
        self.stop_status = "OPEN"
        self.max_records, self.max_bytes = max_records, max_bytes
        self.rows: list[dict[str, Any]] = []
        self.encoded_bytes = 0
        self.budget_used: dict[str, int] = {}
        self.worker_rss_peak = 0
        self.rss_samples = 0
        self.parent_rss_peak = 0
        self.combined_rss_peak_conservative = 0
        self.resource_failed = False
        self.resource_failure_reason: str | None = None
        self.rss_sample_interval_ns = 100_000_000
        self._last_rss_sample = 0
        self._last_monitor_sample_ns = 0
        self._max_monitor_gap_ns = 0
        self._last_parent_thread_sample_ns = 0
        self._max_parent_thread_gap_ns = 0
        self._parent_monitor_samples = 0
        self._rss_stop_bytes = rss_stop_bytes
        self._parent_rss_sampler = parent_rss_sampler
        self._monitor_stop = threading.Event()
        self._monitor_lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None
        self._metrics: dict[str, Any] = {
            "rss_samples": 0,
            "rss_sample_interval_ns": self.rss_sample_interval_ns,
            "rss_max_sample_gap_ns": 0,
            "parent_monitor_samples": 0,
            "parent_thread_samples": 0,
            "parent_monitor_max_sample_gap_ns": 0,
            "parent_thread_max_sample_gap_ns": 0,
            "parent_rss_peak_bytes": 0,
            "child_rss_peak_bytes": 0,
            "combined_ru_maxrss_peak_upper_bound_bytes": 0,
            "rss_warning": False,
            "storage_warning": False,
            "last_free_bytes": None,
            "audited_bytes": 0,
            "audited_files": 0,
            "terminal_audit_records": 0,
            "terminal_audit_bytes": 0,
            "queue_peak_records": 0,
            "queue_peak_bytes": 0,
        }
        self.deadline: float | None = None
        self.fault = fault
        context = mp.get_context("spawn")
        self._worker_rss_peak_value = context.RawValue("Q", 0)
        self._worker_rss_sample_count = context.RawValue("Q", 0)
        self._worker_rss_exceeded = context.RawValue("b", 0)
        child, self.send_connection = context.Pipe(duplex=False)
        self.connection, child_ack = context.Pipe(duplex=False)
        self.process = context.Process(
            target=_worker,
            args=(
                child,
                child_ack,
                str(root),
                run_metadata or {},
                max_run_bytes,
                min_free_bytes,
                fault,
                rss_stop_bytes,
                self._worker_rss_peak_value,
                self._worker_rss_sample_count,
                self._worker_rss_exceeded,
            ),
            daemon=True,
        )
        try:
            self.process.start()
            os.set_blocking(self.connection.fileno(), False)
            os.set_blocking(self.send_connection.fileno(), False)
            child.close()
            child_ack.close()
            self._exchange(None)
            self._start_parent_monitor()
        except Exception:
            if self.process.pid is not None:
                self._stop()
            else:
                self.connection.close()
                self.send_connection.close()
            raise
        finally:
            child.close()
            child_ack.close()

    def _exchange(self, request: dict[str, Any] | None) -> dict[str, Any]:
        deadline = time.monotonic() + self.ack_seconds
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        fd = self.connection.fileno()
        if request is not None:
            send_fd = self.send_connection.fileno()
            if _request_encoded_upper_bound(request) > MAX_COMMAND_BYTES:
                raise WriterFailure("WRITER_COMMAND_LIMIT")
            payload = _json_line(request)
            if len(payload) > MAX_COMMAND_BYTES:
                raise WriterFailure("WRITER_COMMAND_LIMIT")
            wire = struct.pack("!i", len(payload)) + payload
            offset = 0
            while offset < len(wire):
                self._sample_worker_rss()
                self._raise_if_resource_failed()
                remaining = deadline - time.monotonic()
                wait = min(remaining, 0.1)
                if wait <= 0 or not select.select([], [send_fd], [], wait)[1]:
                    if remaining > 0:
                        continue
                    raise WriterFailure("WRITER_ACK_TIMEOUT")
                try:
                    offset += os.write(send_fd, wire[offset:])
                except BlockingIOError:
                    continue
        received = bytearray()
        expected: int | None = None
        while expected is None or len(received) < expected + 4:
            self._sample_worker_rss()
            if request is not None:
                self._raise_if_resource_failed()
            remaining = deadline - time.monotonic()
            wait = min(remaining, 0.1)
            if wait <= 0 or not select.select([fd], [], [], wait)[0]:
                if remaining > 0:
                    continue
                raise WriterFailure("WRITER_ACK_TIMEOUT")
            try:
                data = os.read(fd, 16384)
            except BlockingIOError:
                continue
            if not data:
                raise WriterFailure("WRITER_EOF")
            received.extend(data)
            if len(received) >= 4 and expected is None:
                expected = struct.unpack("!i", received[:4])[0]
                if expected < 0 or expected > 16384:
                    raise WriterFailure("INVALID_ACK_LENGTH")
        reply: dict[str, Any] = json.loads(received[4:], object_hook=_json_object_hook)
        if reply.get("ok") is not True:
            self.budget_used = reply.get("budget_used", self.budget_used)
            self._merge_worker_metrics(reply.get("metrics", {}))
            raise WriterFailure(str(reply.get("error", "WRITER_ERROR")))
        self.cursor = reply["cursor"]
        self.budget_used = reply.get("budget_used", self.budget_used)
        self.worker_rss_peak = max(self.worker_rss_peak, reply.get("worker_rss_peak", 0))
        self.rss_samples = max(self.rss_samples, _shared_int(self._worker_rss_sample_count))
        self._merge_worker_metrics(reply.get("metrics", {}))
        return reply

    @property
    def metrics(self) -> dict[str, Any]:
        self._sample_worker_rss()
        return dict(self._metrics)

    def _start_parent_monitor(self) -> None:
        def monitor() -> None:
            while not self._monitor_stop.wait(0.1):
                try:
                    self._sample_worker_rss_inner(force=True, parent_thread=True)
                    self._raise_if_resource_failed(stop_worker=True)
                except WriterFailure:
                    return

        self._monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._monitor_thread.start()

    def _sample_worker_rss(self) -> None:
        self._sample_worker_rss_inner(force=False, parent_thread=False)

    def _sample_worker_rss_inner(self, *, force: bool, parent_thread: bool) -> None:
        now = time.monotonic_ns()
        if not force and now - self._last_rss_sample < self.rss_sample_interval_ns:
            return
        if self._last_monitor_sample_ns:
            self._max_monitor_gap_ns = max(
                self._max_monitor_gap_ns, now - self._last_monitor_sample_ns
            )
        self._last_monitor_sample_ns = now
        self._last_rss_sample = now
        if parent_thread:
            if self._last_parent_thread_sample_ns:
                self._max_parent_thread_gap_ns = max(
                    self._max_parent_thread_gap_ns, now - self._last_parent_thread_sample_ns
                )
            self._last_parent_thread_sample_ns = now
            self._parent_monitor_samples += 1
        parent_peak = self._parent_rss_sampler() if self._parent_rss_sampler else _rss_bytes()
        child_peak = _shared_int(self._worker_rss_peak_value)
        samples = _shared_int(self._worker_rss_sample_count)
        exceeded = bool(_shared_int(self._worker_rss_exceeded))
        combined_upper = parent_peak + child_peak
        with self._monitor_lock:
            self.parent_rss_peak = max(self.parent_rss_peak, parent_peak)
            self.worker_rss_peak = max(self.worker_rss_peak, child_peak)
            self.combined_rss_peak_conservative = max(
                self.combined_rss_peak_conservative, combined_upper
            )
            self.rss_samples = max(self.rss_samples, samples)
            self._metrics["rss_samples"] = self.rss_samples
            self._metrics["rss_max_sample_gap_ns"] = self._max_monitor_gap_ns
            self._metrics["parent_monitor_samples"] = self._parent_monitor_samples
            self._metrics["parent_thread_samples"] = self._parent_monitor_samples
            self._metrics["parent_monitor_max_sample_gap_ns"] = self._max_parent_thread_gap_ns
            self._metrics["parent_thread_max_sample_gap_ns"] = self._max_parent_thread_gap_ns
            self._metrics["parent_rss_peak_bytes"] = self.parent_rss_peak
            self._metrics["child_rss_peak_bytes"] = self.worker_rss_peak
            self._metrics["combined_ru_maxrss_peak_upper_bound_bytes"] = (
                self.combined_rss_peak_conservative
            )
            if parent_peak > self._rss_stop_bytes or combined_upper > self._rss_stop_bytes:
                self.resource_failed = True
                self.resource_failure_reason = "RSS_LIMIT"
            if exceeded or child_peak > self._rss_stop_bytes:
                self.resource_failed = True
                self.resource_failure_reason = "WORKER_RSS_LIMIT"
            if (
                parent_peak > int(self._rss_stop_bytes * 0.75)
                or child_peak > int(self._rss_stop_bytes * 0.75)
                or combined_upper > int(self._rss_stop_bytes * 0.75)
            ):
                self._metrics["rss_warning"] = True

    def _raise_if_resource_failed(self, *, stop_worker: bool = False) -> None:
        if not getattr(self, "resource_failed", False):
            return
        if stop_worker and self.process.pid is not None and self.process.is_alive():
            self.process.terminate()
        raise WriterFailure(self.resource_failure_reason or "RSS_LIMIT")

    def _merge_worker_metrics(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        for key in (
            "storage_warning",
            "storage_warning_receipt",
            "failure_receipt_status",
            "failure_receipt_error_type",
            "last_free_bytes",
            "audited_bytes",
            "audited_files",
            "terminal_audit_records",
            "terminal_audit_bytes",
            "queue_peak_records",
            "queue_peak_bytes",
        ):
            if key not in value:
                continue
            if key.startswith("queue_peak"):
                self._metrics[key] = max(int(self._metrics[key]), int(value[key]))
            else:
                self._metrics[key] = value[key]

    def _command(self, value: dict[str, Any]) -> dict[str, Any]:
        if self.failed or self.closed:
            raise WriterFailure("WRITER_NOT_OPEN")
        try:
            self._sample_worker_rss()
            self._raise_if_resource_failed(stop_worker=True)
            value["fault"] = self.fault
            return self._exchange(value)
        except Exception as exc:
            self.failed = True
            self._stop()
            raise WriterFailure(
                f"{exc}; {self.stop_status}; UNCERTAIN_GAP cursor={self.cursor}"
            ) from exc

    def append(self, row: dict[str, Any]) -> None:
        single_cap = 98304 if row.get("kind") == "RAW" else 4096
        upper = _json_bound(row) + 1
        if upper > single_cap or upper > self.max_bytes:
            raise WriterFailure("QUEUE_LIMIT")
        size = len(_json_line(row))
        if self.failed or self.closed:
            raise WriterFailure("WRITER_NOT_OPEN")
        try:
            self._sample_worker_rss()
            self._raise_if_resource_failed(stop_worker=True)
        except Exception as exc:
            self.failed = True
            self._stop()
            raise WriterFailure(
                f"{exc}; {self.stop_status}; UNCERTAIN_GAP cursor={self.cursor}"
            ) from exc
        if self.rows and _row_segment_class(self.rows[0]) != _row_segment_class(row):
            self.flush()
        if (
            size > 98304
            or (row.get("kind") != "RAW" and size > 4096)
            or len(self.rows) + 1 > self.max_records
            or self.encoded_bytes + size > self.max_bytes
        ):
            raise WriterFailure("QUEUE_LIMIT")
        previous = self.rows[-1]["ingest_sequence"] if self.rows else self.cursor
        if previous is not None and row["ingest_sequence"] <= previous:
            raise WriterFailure("INPUT_SEQUENCE_LIMIT")
        self.rows.append(deepcopy(row))
        self.encoded_bytes += size

    def flush(self) -> dict[str, Any] | None:
        if not self.rows:
            return None
        result = self._command({"operation": "batch", "rows": self.rows})
        self.rows.clear()
        self.encoded_bytes = 0
        return result.get("manifest")

    def append_durable(self, row: dict[str, Any]) -> None:
        self.append(row)
        self.flush()

    def write_table(self, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return self._command({"operation": "table", "name": name, "rows": rows})

    def write_snapshot(self, path: str, data: bytes) -> None:
        if len(path) + ((len(data) + 2) // 3) * 4 + 4096 > MAX_COMMAND_BYTES:
            raise WriterFailure("WRITER_COMMAND_LIMIT")
        self._command(
            {
                "operation": "snapshot",
                "path": path,
                "base64": base64.b64encode(data).decode("ascii"),
            }
        )

    def write_run_context(self, context: dict[str, Any]) -> None:
        self.write_snapshot("snapshots/run-context.json", _json_line(context))

    def _stop(self) -> None:
        monitor_stop = getattr(self, "_monitor_stop", None)
        if monitor_stop is not None:
            monitor_stop.set()
        self.process.join(timeout=0.05)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=0.2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=0.2)
        self.stop_status = "STOP_FAILED" if self.process.is_alive() else "STOPPED_UNCERTAIN"
        monitor_thread = getattr(self, "_monitor_thread", None)
        if monitor_thread is not None and monitor_thread is not threading.current_thread():
            monitor_thread.join(timeout=0.2)
        self.connection.close()
        self.send_connection.close()

    def close(self) -> None:
        if self.closed:
            return
        if self.failed:
            if self.process.is_alive():
                raise WriterFailure("STOP_FAILED/BLOCKED")
            raise WriterFailure("STOPPED_UNCERTAIN/BLOCKED")
        self.flush()
        self._command({"operation": "close"})
        self.process.join(timeout=0.2)
        if self.process.is_alive():
            self.failed = True
            self._stop()
            raise WriterFailure(f"{self.stop_status}/BLOCKED")
        self.closed = True
        self.stop_status = "CLOSED_ACKNOWLEDGED"
        self._monitor_stop.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=0.2)
        self.connection.close()
        self.send_connection.close()
