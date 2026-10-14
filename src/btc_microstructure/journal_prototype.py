"""Isolated TEST_ONLY append-journal/checkpoint prototype for R2-04 L04.

Nothing imports this module from a runtime entry point.  It deliberately keeps
the existing DurableWriter surface so the bounded controller can be exercised
without replacing Store or enabling REAL collection.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import multiprocessing as mp
from multiprocessing.connection import Connection
import os
from pathlib import Path
import resource
import re
import select
import shutil
import stat
import struct
import sys
import threading
import time
from typing import Any, Callable, cast, Literal, NamedTuple
import zlib

from .bounded_replay import (
    _core_row,
    _decode_snapshot,
    _tables_digest,
    replay_bounded,
)
from .bounded_writer import (
    MAX_AUDIT_DEPTH,
    MAX_AUDIT_FILES,
    MAX_COMMAND_BYTES,
    MIB,
    PARTITIONS,
    Budget,
    WriterFailure,
    _request_encoded_upper_bound,
    STARTUP_FREE_BYTES,
    WARN_FREE_BYTES,
    STOP_FREE_BYTES,
)
from .offline_core import Core
from .offline_storage import _arrow_table, _json_line, _json_object_hook
from .probe import object_json
from .unit_binding import STREAM, load_unit_evidence, resolve_test_only_unit_binding

JsonRow = dict[str, Any]
Partition = Literal["raw", "control", "feature"]

SCHEMA_VERSION = "journal-prototype-1"
FILE_MAGIC = b"BTCJNL1\0"
FRAME_MAGIC = b"BTCFRM1\0"
COMMIT_MAGIC = b"BTCCMT1\0"
FORMAT_VERSION = 1
FILE_HEADER = struct.Struct(">8sBB6x")
FRAME_HEADER = struct.Struct(">8sBBHQQQII32s32sI")
COMMIT_TRAILER = struct.Struct(">8s32sQ")
ZERO_HASH = b"\0" * 32
PARTITION_ID: dict[Partition, int] = {"raw": 1, "control": 2, "feature": 3}
ID_PARTITION = {value: key for key, value in PARTITION_ID.items()}
PARTITION_BUDGET = {
    "raw": "ws_raw_envelopes",
    "control": "control",
    "feature": "feature_parquet",
}
JOURNAL_NAME = {name: re.compile(rf"^{name}-[0-9]{{6}}\.jnl$") for name in PARTITION_ID}
DEFAULT_ROLL_BYTES = 4 * MIB
DEFAULT_AUDIT_INTERVAL = 32
MAX_FRAME_PAYLOAD_BYTES = 2 * MIB
MAX_TABLE_CHUNK_PAYLOAD_BYTES = MIB
MAX_RECOVERY_ROWS = 104_096
TERMINAL_RESERVE_BYTES = MIB
MAX_ACK_BYTES = 65_536
RESOURCE_ERRORS = {
    1: "RSS_LIMIT",
    2: "WORKER_RSS_LIMIT",
    3: "CHILD_MONITOR_FAILED",
    4: "PARENT_MONITOR_FAILED",
    5: "JOURNAL_WORKER_EXITED",
}


class _FileRecord(NamedTuple):
    path: Path
    size: int
    signature: tuple[int, ...]


class _Frame(NamedTuple):
    partition: Partition
    batch_id: int
    first_sequence: int
    last_sequence: int
    rows: list[JsonRow]
    file_index: int
    start_offset: int
    end_offset: int
    frame_sha256: str


class JournalAuditFailure(WriterFailure):
    def __init__(
        self,
        reason: str,
        charges: dict[str, int],
        files: dict[str, _FileRecord],
    ) -> None:
        self.charges = dict(charges)
        self.files = dict(files)
        super().__init__(reason)


def _rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _stat_signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _safe_tree_digest(root: Path) -> str:
    """Digest in-tree regular files without ever following a symlink."""

    digest = hashlib.sha256()
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return digest.hexdigest()
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        digest.update(b"INVALID_ROOT")
        return digest.hexdigest()
    root = root.resolve(strict=True)
    stack = [root]
    records: list[tuple[str, bytes]] = []
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                stat_value = entry.stat(follow_symlinks=False)
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if stat.S_ISLNK(stat_value.st_mode):
                    records.append((relative, b"SYMLINK"))
                elif stat.S_ISDIR(stat_value.st_mode):
                    stack.append(path)
                elif stat.S_ISREG(stat_value.st_mode):
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    try:
                        with os.fdopen(descriptor, "rb", closefd=False) as handle:
                            file_digest = hashlib.sha256()
                            for chunk in iter(lambda: handle.read(MIB), b""):
                                file_digest.update(chunk)
                        records.append((relative, file_digest.digest()))
                    finally:
                        os.close(descriptor)
                else:
                    records.append((relative, b"SPECIAL"))
    for relative, digest_value in sorted(records):
        digest.update(relative.encode())
        digest.update(digest_value)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda item: json.loads(_json_line({"value": item}))["value"],
    ).encode()


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


def _fsync(handle: Any, samples: list[float]) -> None:
    handle.flush()
    began = time.perf_counter()
    os.fsync(handle.fileno())
    samples.append(time.perf_counter() - began)


def _fsync_dir(path: Path, samples: list[float]) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        began = time.perf_counter()
        os.fsync(descriptor)
        samples.append(time.perf_counter() - began)
    finally:
        os.close(descriptor)


def _partition_for_row(row: JsonRow) -> Partition:
    return "raw" if row.get("kind") == "RAW" else "control"


def _partition_path(root: Path, partition: Partition, index: int) -> Path:
    return root / "journal" / partition / f"{partition}-{index:06d}.jnl"


def _charge_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    parts = relative.parts
    if path.name.endswith(".tmp"):
        return "temporary_orphan_quarantine_files"
    if path.name in {"writer-open", "writer-closed"}:
        return "diagnostics_and_receipts"
    if parts == ("journal-format.json",):
        return "manifests_indexes"
    if parts and parts[0] == "snapshots":
        return "rest_metadata"
    if parts and parts[0] == "checkpoints" and path.name in {"slot-a.json", "slot-b.json"}:
        return "manifests_indexes"
    if (
        len(parts) == 3
        and parts[:2] == ("journal", "raw")
        and JOURNAL_NAME["raw"].fullmatch(path.name)
    ):
        return "ws_raw_envelopes"
    if (
        len(parts) == 3
        and parts[:2] == ("journal", "control")
        and JOURNAL_NAME["control"].fullmatch(path.name)
    ):
        return "control"
    if (
        len(parts) == 3
        and parts[:2] == ("journal", "feature")
        and JOURNAL_NAME["feature"].fullmatch(path.name)
    ):
        return "feature_parquet"
    if parts and parts[0] == "errors":
        return "diagnostics_and_receipts"
    return "temporary_orphan_quarantine_files"


def _audit_tree(
    root: Path,
    *,
    allow_checkpoint_temp: bool = False,
) -> tuple[dict[str, int], dict[str, _FileRecord], list[JsonRow]]:
    root_stat = root.lstat()
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise WriterFailure("JOURNAL_AUDIT_ROOT")
    root = root.resolve(strict=True)
    charges = dict.fromkeys(PARTITIONS, 0)
    files: dict[str, _FileRecord] = {}
    events: list[JsonRow] = []
    blocked_orphans: list[str] = []
    entries_seen = 0
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        if depth > MAX_AUDIT_DEPTH:
            raise WriterFailure("JOURNAL_AUDIT_DEPTH")
        with os.scandir(directory) as entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > MAX_AUDIT_FILES:
                    raise WriterFailure("JOURNAL_AUDIT_FILE_COUNT")
                value = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(value.st_mode):
                    raise WriterFailure("JOURNAL_AUDIT_SYMLINK")
                path = Path(entry.path)
                if stat.S_ISDIR(value.st_mode):
                    stack.append((path, depth + 1))
                    continue
                if not stat.S_ISREG(value.st_mode):
                    raise WriterFailure("JOURNAL_AUDIT_SPECIAL")
                key = path.relative_to(root).as_posix()
                category = _charge_path(root, path)
                if category == "temporary_orphan_quarantine_files":
                    checkpoint_temp = (
                        allow_checkpoint_temp
                        and path.parent == root / "checkpoints"
                        and path.name in {"slot-a.json.tmp", "slot-b.json.tmp"}
                    )
                    if checkpoint_temp:
                        events.append({"event_type": "TORN_CHECKPOINT", "path": key})
                    else:
                        blocked_orphans.append(key)
                charges[category] += value.st_size
                files[key] = _FileRecord(path, value.st_size, _stat_signature(value))
    if blocked_orphans:
        raise JournalAuditFailure(
            f"JOURNAL_AUDIT_ORPHAN:{blocked_orphans[0]}",
            charges,
            files,
        )
    return charges, files, events


def _check_charges(charges: dict[str, int], total: int) -> None:
    if sum(charges.values()) > total:
        raise WriterFailure("RUN_BYTE_LIMIT")
    for name, value in charges.items():
        if value > PARTITIONS[name]:
            raise WriterFailure(f"PARTITION_LIMIT:{name}")


def _file_header(partition: Partition) -> bytes:
    return FILE_HEADER.pack(FILE_MAGIC, FORMAT_VERSION, PARTITION_ID[partition])


def _frame_bytes(
    *,
    partition: Partition,
    batch_id: int,
    rows: list[JsonRow],
    previous_hash: bytes,
    first_sequence: int,
    last_sequence: int,
) -> tuple[bytes, bytes, bytes, bytes]:
    payload = _canonical({"rows": rows})
    if len(payload) > MAX_FRAME_PAYLOAD_BYTES:
        raise WriterFailure("JOURNAL_FRAME_PAYLOAD_LIMIT")
    payload_hash = hashlib.sha256(payload).digest()
    without_crc = FRAME_HEADER.pack(
        FRAME_MAGIC,
        FORMAT_VERSION,
        PARTITION_ID[partition],
        0,
        batch_id,
        first_sequence,
        last_sequence,
        len(rows),
        len(payload),
        previous_hash,
        payload_hash,
        0,
    )
    crc = zlib.crc32(without_crc[:-4]) & 0xFFFFFFFF
    header = without_crc[:-4] + struct.pack(">I", crc)
    frame_hash = hashlib.sha256(header + payload).digest()
    total_length = len(header) + len(payload) + COMMIT_TRAILER.size
    trailer = COMMIT_TRAILER.pack(COMMIT_MAGIC, frame_hash, total_length)
    return header, payload, trailer, frame_hash


def _table_chunks(
    name: str,
    rows: list[JsonRow],
    table_id: str,
    payload_limit: int = MAX_TABLE_CHUNK_PAYLOAD_BYTES,
) -> list[list[JsonRow]]:
    if payload_limit <= 4096:
        raise WriterFailure("JOURNAL_TABLE_CHUNK_LIMIT")
    chunks: list[list[JsonRow]] = []
    current: list[JsonRow] = []
    current_bytes = 4096 + len(name) + len(table_id)
    for row in rows:
        row_bytes = len(_canonical(row)) + 1
        if row_bytes + 4096 > payload_limit:
            raise WriterFailure("JOURNAL_TABLE_ROW_LIMIT")
        if current and current_bytes + row_bytes > payload_limit:
            chunks.append(current)
            current = []
            current_bytes = 4096 + len(name) + len(table_id)
        if current_bytes + row_bytes <= payload_limit:
            current.append(row)
            current_bytes += row_bytes
            continue
        raise WriterFailure("JOURNAL_TABLE_ROW_LIMIT")
    if current or not rows:
        chunks.append(current)
    return chunks


def _format_descriptor() -> bytes:
    return (
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "test_only": True,
                "file_magic": FILE_MAGIC.decode("ascii", errors="strict").rstrip("\0"),
                "frame_magic": FRAME_MAGIC.decode("ascii", errors="strict").rstrip("\0"),
                "roll_bytes": DEFAULT_ROLL_BYTES,
                "partitions": PARTITION_BUDGET,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _atomic_file(
    path: Path,
    data: bytes,
    *,
    budget: Budget,
    category: str,
    fsync_samples: list[float],
    disk_free: int,
) -> None:
    if path.exists():
        raise WriterFailure("JOURNAL_FILE_ALREADY_EXISTS")
    temp = path.with_suffix(path.suffix + ".tmp")
    budget.reserve({category: len(data), "temporary_orphan_quarantine_files": len(data)}, disk_free)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            _fsync(handle, fsync_samples)
        os.replace(temp, path)
        _fsync_dir(path.parent, fsync_samples)
    except Exception:
        raise
    finally:
        if temp.exists():
            temp.unlink()
    budget.release({"temporary_orphan_quarantine_files": len(data)})


class _ActiveJournal:
    def __init__(self, partition: Partition) -> None:
        self.partition = partition
        self.index = 0
        self.path: Path | None = None
        self.handle: Any | None = None
        self.signature: tuple[int, ...] | None = None
        self.size = 0
        self.last_hash = ZERO_HASH
        self.last_offset = 0

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def _checkpoint_payload(
    generation: int,
    cursor: int | None,
    batch_id: int,
    journals: dict[Partition, _ActiveJournal],
    budget: Budget,
) -> JsonRow:
    return {
        "schema_version": SCHEMA_VERSION,
        "generation": generation,
        "trusted_cursor": cursor,
        "last_batch_id": batch_id,
        "journals": {
            name: {
                "file_index": journal.index,
                "committed_offset": journal.last_offset,
                "last_frame_sha256": journal.last_hash.hex(),
            }
            for name, journal in journals.items()
        },
        "partition_bytes": dict(budget.used),
    }


def _checkpoint_bytes(payload: JsonRow) -> bytes:
    encoded = _canonical(payload)
    return (
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "payload_base64": base64.b64encode(encoded).decode("ascii"),
                "payload_sha256": hashlib.sha256(encoded).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _worker(
    connection: Connection,
    acknowledgement: Connection,
    root_value: str,
    total: int,
    free: int,
    roll_bytes: int,
    audit_interval: int,
    rss_stop_bytes: int,
    fault_operation: str | None,
    fault_after: int,
    fault_stage: str | None,
    shared: dict[str, Any],
    rss_sampler: Callable[[], int] | None,
    disk_sampler: Callable[[], int] | None,
) -> None:
    root = Path(root_value)
    budget = Budget(total, free, terminal_audit_reserve_bytes=TERMINAL_RESERVE_BYTES)
    fsync_samples: list[float] = []
    audit_samples: list[float] = []
    frames = 0
    full_audits = 0
    checkpoints = 0
    generation = 0
    command_count = 0
    batch_id = 0
    cursor: int | None = None
    journals = {name: _ActiveJournal(name) for name in PARTITION_ID}
    last_audit_charges = dict.fromkeys(PARTITIONS, 0)
    last_audit_files = 0
    operation_counts: dict[str, int] = {}
    monitor_stop = threading.Event()
    monitor: threading.Thread | None = None
    storage_warning = storage_warning_receipt = False
    last_free_bytes = 0
    receipt_status = "NOT_ATTEMPTED"

    def monitor_rss() -> None:
        previous = 0
        try:
            shared["monitor_alive"].value = 1
            while not monitor_stop.is_set():
                if shared["monitor_exit"].value:
                    raise WriterFailure("injected child monitor exit")
                now = time.monotonic_ns()
                observed = int(rss_sampler() if rss_sampler else _rss_bytes())
                shared["peak"].value = max(shared["peak"].value, observed)
                shared["samples"].value += 1
                if previous:
                    shared["maxgap"].value = max(shared["maxgap"].value, now - previous)
                previous = now
                shared["heartbeat"].value = now
                if observed > rss_stop_bytes:
                    shared["failure"].value = 2
                    return
                monitor_stop.wait(0.1)
        except Exception:
            shared["failure"].value = 3
        finally:
            shared["monitor_alive"].value = 0

    def check_resources() -> None:
        failure = int(shared["failure"].value)
        if failure:
            raise WriterFailure(RESOURCE_ERRORS.get(failure, "RESOURCE_MONITOR_FAILED"))
        if monitor is not None and not monitor.is_alive():
            raise WriterFailure("CHILD_MONITOR_FAILED")

    def observed_free() -> int:
        return int(disk_sampler() if disk_sampler else shutil.disk_usage(root).free)

    def disk_free() -> int:
        nonlocal last_free_bytes, storage_warning, storage_warning_receipt
        value = observed_free()
        last_free_bytes = value
        if value < free:
            raise WriterFailure("DISK_FREE_LIMIT")
        if value < WARN_FREE_BYTES:
            storage_warning = True
            if not storage_warning_receipt:
                record = _json_line(
                    {
                        "event_type": "DISK_FREE_WARNING",
                        "ts_receive_ns": time.time_ns(),
                        "trusted_cursor": cursor,
                        "free_bytes": value,
                        "threshold_bytes": WARN_FREE_BYTES,
                    }
                )
                budget.reserve_terminal_audit(len(record), value)
                path = root / "errors" / "storage_events.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("ab") as handle:
                    handle.write(record)
                    if fault_operation == "disk_warning" and fault_stage == "before_fsync":
                        raise OSError("injected disk_warning:before_fsync")
                    _fsync(handle, fsync_samples)
                _fsync_dir(path.parent, fsync_samples)
                storage_warning_receipt = True
        return value

    def metrics() -> JsonRow:
        return {
            "journal_frames": frames,
            "journal_checkpoints": checkpoints,
            "journal_full_audits": full_audits,
            "journal_files": last_audit_files,
            "audited_bytes": sum(last_audit_charges.values()),
            "partition_bytes": dict(last_audit_charges),
            "fsync_count": len(fsync_samples),
            "fsync_p95_seconds": _percentile(fsync_samples, 0.95),
            "fsync_max_seconds": max(fsync_samples, default=0.0),
            "audit_count": len(audit_samples),
            "audit_p95_seconds": _percentile(audit_samples, 0.95),
            "worker_rss_peak_bytes": _rss_bytes(),
            "trusted_cursor": cursor,
            "checkpoint_generation": generation,
            "storage_warning": storage_warning,
            "storage_warning_receipt": storage_warning_receipt,
            "last_free_bytes": last_free_bytes,
            "terminal_audit_records": budget.terminal_audit_records,
            "terminal_audit_bytes": budget.terminal_audit_bytes,
            "failure_receipt_status": receipt_status,
        }

    def full_audit() -> None:
        nonlocal full_audits, last_audit_charges, last_audit_files
        began = time.perf_counter()
        charges, files, _ = _audit_tree(root)
        _check_charges(charges, total)
        for partition in PARTITION_ID:
            _, events = _scan_partition(root, partition)
            if events:
                raise WriterFailure(f"JOURNAL_INTEGRITY:{events[0]['event_type']}")
        budget.set_physical_used(charges)
        last_audit_charges = dict(charges)
        last_audit_files = len(files)
        audit_samples.append(time.perf_counter() - began)
        full_audits += 1

    def active_signature(journal: _ActiveJournal) -> None:
        if journal.handle is None or journal.path is None or journal.signature is None:
            return
        descriptor_stat = os.fstat(journal.handle.fileno())
        path_stat = journal.path.lstat()
        if (
            _stat_signature(descriptor_stat) != journal.signature
            or _stat_signature(path_stat) != journal.signature
            or descriptor_stat.st_size != journal.size
        ):
            raise WriterFailure("JOURNAL_EXTERNAL_MUTATION")

    def open_journal(journal: _ActiveJournal, required: int) -> None:
        if journal.handle is not None and journal.size + required <= roll_bytes:
            active_signature(journal)
            return
        journal.close()
        next_index = journal.index + 1
        if (
            fault_operation == "roll"
            and fault_stage == "before_create"
            and next_index == fault_after
        ):
            raise OSError("injected roll:before_create")
        journal.index = next_index
        journal.path = _partition_path(root, journal.partition, next_index)
        journal.path.parent.mkdir(parents=True, exist_ok=True)
        header = _file_header(journal.partition)
        budget.reserve({PARTITION_BUDGET[journal.partition]: len(header)}, disk_free())
        journal.handle = journal.path.open("xb+")
        journal.handle.write(header)
        _fsync(journal.handle, fsync_samples)
        value = os.fstat(journal.handle.fileno())
        journal.size = value.st_size
        journal.last_offset = value.st_size
        journal.signature = _stat_signature(value)

    def inject(operation: str, stage: str) -> None:
        if (
            operation == fault_operation
            and operation_counts.get(operation) == fault_after
            and stage == fault_stage
        ):
            raise OSError(f"injected {operation}:{stage}")
        if (
            operation == fault_operation
            and operation_counts.get(operation) == fault_after
            and fault_stage == "hang"
            and stage == "before_frame"
        ):
            while True:
                check_resources()
                time.sleep(0.02)

    def append_frame(partition: Partition, rows: list[JsonRow], operation: str) -> JsonRow:
        nonlocal batch_id, cursor, frames, command_count
        if not rows:
            raise WriterFailure("JOURNAL_EMPTY_FRAME")
        if partition in {"raw", "control"}:
            sequences = [int(row["ingest_sequence"]) for row in rows]
            expected = 1 if cursor is None else cursor + 1
            if sequences != list(range(expected, expected + len(rows))):
                raise WriterFailure("JOURNAL_SEQUENCE_GAP")
            first_sequence, last_sequence = sequences[0], sequences[-1]
        else:
            first_sequence = last_sequence = 0
        batch_id += 1
        journal = journals[partition]
        header, payload, trailer, frame_hash = _frame_bytes(
            partition=partition,
            batch_id=batch_id,
            rows=rows,
            previous_hash=journal.last_hash,
            first_sequence=first_sequence,
            last_sequence=last_sequence,
        )
        frame_length = len(header) + len(payload) + len(trailer)
        if frame_length + FILE_HEADER.size > roll_bytes:
            raise WriterFailure("JOURNAL_ROLL_LIMIT")
        open_journal(journal, frame_length)
        assert journal.handle is not None
        inject(operation, "before_frame")
        offset = journal.handle.tell()
        budget.reserve({PARTITION_BUDGET[partition]: frame_length}, disk_free())
        journal.handle.write(header)
        journal.handle.write(payload)
        _fsync(journal.handle, fsync_samples)
        disk_free()
        check_resources()
        inject(operation, "after_payload_fsync")
        if (
            fault_operation == operation
            and operation_counts.get(operation) == fault_after
            and fault_stage == "partial_trailer"
        ):
            journal.handle.write(trailer[: len(trailer) // 2])
            _fsync(journal.handle, fsync_samples)
            raise OSError(f"injected {operation}:partial_trailer")
        journal.handle.write(trailer)
        _fsync(journal.handle, fsync_samples)
        disk_free()
        check_resources()
        value = os.fstat(journal.handle.fileno())
        journal.size = value.st_size
        journal.last_offset = value.st_size
        journal.last_hash = frame_hash
        journal.signature = _stat_signature(value)
        if partition in {"raw", "control"}:
            cursor = last_sequence
        frames += 1
        command_count += 1
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "path": journal.path.relative_to(root).as_posix() if journal.path else None,
            "offset_before": offset,
            "offset_after": journal.last_offset,
            "sha256": frame_hash.hex(),
            "batch_id": batch_id,
            "cursor": cursor,
        }
        return receipt

    def checkpoint() -> JsonRow:
        nonlocal generation, checkpoints
        full_audit()
        generation += 1
        payload = _checkpoint_payload(generation, cursor, batch_id, journals, budget)
        encoded = _checkpoint_bytes(payload)
        slot = root / "checkpoints" / ("slot-a.json" if generation % 2 else "slot-b.json")
        slot.parent.mkdir(parents=True, exist_ok=True)
        temp = slot.with_suffix(slot.suffix + ".tmp")
        old_size = slot.stat().st_size if slot.exists() else 0
        budget.reserve(
            {
                "manifests_indexes": len(encoded),
                "temporary_orphan_quarantine_files": len(encoded),
            },
            disk_free(),
        )
        with temp.open("xb") as handle:
            handle.write(encoded)
            _fsync(handle, fsync_samples)
        if (
            fault_operation == "checkpoint"
            and operation_counts.get("checkpoint") == fault_after
            and fault_stage == "after_temp_fsync"
        ):
            raise OSError("injected checkpoint:after_temp_fsync")
        os.replace(temp, slot)
        _fsync_dir(slot.parent, fsync_samples)
        budget.release({"temporary_orphan_quarantine_files": len(encoded)})
        if old_size:
            budget.release({"manifests_indexes": old_size})
        checkpoints += 1
        full_audit()
        return {"generation": generation, "path": slot.relative_to(root).as_posix()}

    try:
        if root.exists() and any(root.iterdir()):
            raise WriterFailure("JOURNAL_RUN_DIRECTORY_NOT_EMPTY")
        root.mkdir(parents=True, exist_ok=True)
        if free >= STOP_FREE_BYTES and observed_free() < STARTUP_FREE_BYTES:
            raise WriterFailure("STARTUP_DISK_FREE_LIMIT")
        for name in PARTITION_ID:
            (root / "journal" / name).mkdir(parents=True)
        (root / "checkpoints").mkdir()
        (root / "snapshots").mkdir()
        descriptor = _format_descriptor()
        _atomic_file(
            root / "journal-format.json",
            descriptor,
            budget=budget,
            category="manifests_indexes",
            fsync_samples=fsync_samples,
            disk_free=disk_free(),
        )
        budget.reserve_terminal_audit(5, disk_free())
        with (root / "writer-open").open("xb") as handle:
            handle.write(b"open\n")
            _fsync(handle, fsync_samples)
        _fsync_dir(root, fsync_samples)
        full_audit()
        monitor = threading.Thread(target=monitor_rss, daemon=True)
        monitor.start()
        acknowledgement.send_bytes(
            _canonical({"ok": True, "cursor": None, "worker_rss_peak": _rss_bytes()})
        )
        while True:
            check_resources()
            if not connection.poll(0.05):
                continue
            request = json.loads(connection.recv_bytes(), object_hook=_json_object_hook)
            operation = str(request.get("operation"))
            request_bound = _request_encoded_upper_bound(request)
            if request_bound > MAX_COMMAND_BYTES:
                raise WriterFailure("WRITER_COMMAND_LIMIT")
            if _rss_bytes() + 3 * request_bound > rss_stop_bytes:
                raise WriterFailure("WORKER_RSS_ALLOCATION_LIMIT")
            operation_counts[operation] = operation_counts.get(operation, 0) + 1
            manifest: JsonRow | None = None
            if operation == "batch":
                rows = request.get("rows")
                if not isinstance(rows, list) or not rows:
                    raise WriterFailure("JOURNAL_INVALID_BATCH")
                classes = {_partition_for_row(row) for row in rows}
                if len(classes) != 1:
                    raise WriterFailure("MIXED_SEGMENT_CLASS")
                manifest = append_frame(classes.pop(), rows, operation)
            elif operation == "table":
                name = request.get("name")
                rows = request.get("rows")
                if not isinstance(name, str) or not isinstance(rows, list):
                    raise WriterFailure("JOURNAL_INVALID_TABLE")
                table_hash = hashlib.sha256(_canonical({"name": name, "rows": rows})).hexdigest()
                table_id = f"{operation_counts[operation]}:{table_hash}"
                chunk_limit = min(
                    MAX_TABLE_CHUNK_PAYLOAD_BYTES,
                    roll_bytes - FILE_HEADER.size - FRAME_HEADER.size - COMMIT_TRAILER.size,
                )
                chunks = _table_chunks(name, rows, table_id, chunk_limit)
                receipts = []
                for index, chunk in enumerate(chunks):
                    receipts.append(
                        append_frame(
                            "feature",
                            [
                                {
                                    "name": name,
                                    "table_id": table_id,
                                    "chunk_index": index,
                                    "chunk_count": len(chunks),
                                    "rows": chunk,
                                }
                            ],
                            operation,
                        )
                    )
                manifest = {
                    **receipts[-1],
                    "table_id": table_id,
                    "chunk_count": len(chunks),
                    "sha256": table_hash,
                }
            elif operation == "snapshot":
                relative = Path(str(request.get("path", "")))
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or relative.parts[:1] != ("snapshots",)
                ):
                    raise WriterFailure("JOURNAL_SNAPSHOT_PATH")
                data = base64.b64decode(str(request.get("base64", "")), validate=True)
                _atomic_file(
                    root / relative,
                    data,
                    budget=budget,
                    category="rest_metadata",
                    fsync_samples=fsync_samples,
                    disk_free=disk_free(),
                )
                manifest = {"path": relative.as_posix(), "sha256": hashlib.sha256(data).hexdigest()}
                command_count += 1
            elif operation == "checkpoint":
                manifest = checkpoint()
            elif operation == "close":
                checkpoint()
                for journal in journals.values():
                    journal.close()
                budget.reserve_terminal_audit(7, disk_free())
                with (root / "writer-closed").open("xb") as handle:
                    handle.write(b"closed\n")
                    _fsync(handle, fsync_samples)
                _fsync_dir(root, fsync_samples)
                full_audit()
                disk_free()
                acknowledgement.send_bytes(
                    _canonical(
                        {
                            "ok": True,
                            "cursor": cursor,
                            "manifest": None,
                            "metrics": metrics(),
                            "worker_rss_peak": _rss_bytes(),
                        }
                    )
                )
                break
            else:
                raise WriterFailure("JOURNAL_INVALID_OPERATION")
            if operation != "checkpoint" and command_count and command_count % audit_interval == 0:
                checkpoint()
            if _rss_bytes() > rss_stop_bytes:
                raise WriterFailure("WORKER_RSS_LIMIT")
            check_resources()
            disk_free()
            acknowledgement.send_bytes(
                _canonical(
                    {
                        "ok": True,
                        "cursor": cursor,
                        "manifest": manifest,
                        "metrics": metrics(),
                        "worker_rss_peak": _rss_bytes(),
                    }
                )
            )
    except Exception as exc:
        receipt_status = "NOT_ATTEMPTED"
        try:
            record = _json_line(
                {
                    "event_type": "JOURNAL_WRITER_FAILURE",
                    "ts_receive_ns": time.time_ns(),
                    "trusted_cursor": cursor,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:256],
                }
            )
            if len(record) > 4096:
                raise WriterFailure("TERMINAL_AUDIT_RECORD_LIMIT")
            budget.reserve_terminal_audit(len(record), observed_free())
            error_path = root / "errors" / "storage_events.jsonl"
            error_path.parent.mkdir(parents=True, exist_ok=True)
            with error_path.open("ab") as handle:
                handle.write(record)
                _fsync(handle, fsync_samples)
            _fsync_dir(error_path.parent, fsync_samples)
            receipt_status = "DURABLE"
        except Exception:
            receipt_status = "FAILED"
        try:
            acknowledgement.send_bytes(
                _canonical(
                    {
                        "ok": False,
                        "cursor": cursor,
                        "error": (
                            f"{type(exc).__name__}:{str(exc)[:192]};receipt_status={receipt_status}"
                        ),
                        "metrics": metrics(),
                        "worker_rss_peak": _rss_bytes(),
                    }
                )
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        monitor_stop.set()
        if monitor is not None:
            monitor.join(timeout=0.2)
        for journal in journals.values():
            journal.close()
        connection.close()
        acknowledgement.close()


class TestOnlyJournalProcessWriter:
    """Process-isolated TEST_ONLY prototype; not a production Store replacement."""

    TEST_ONLY = True
    __test__ = False

    def __init__(
        self,
        root: Path,
        *,
        run_metadata: JsonRow | None = None,
        max_run_bytes: int = 512 * MIB,
        min_free_bytes: int = 2 * 1024 * MIB,
        ack_seconds: float = 2.0,
        max_records: int = 1000,
        max_bytes: int = MIB,
        rss_stop_bytes: int = 512 * MIB,
        roll_bytes: int = DEFAULT_ROLL_BYTES,
        audit_interval: int = DEFAULT_AUDIT_INTERVAL,
        fault_operation: str | None = None,
        fault_after: int = 1,
        fault_stage: str | None = None,
        parent_rss_sampler: Callable[[], int] | None = None,
        worker_rss_sampler: Callable[[], int] | None = None,
        disk_free_sampler: Callable[[], int] | None = None,
        **_: Any,
    ) -> None:
        del run_metadata
        if (
            not 0 < ack_seconds <= 2
            or not 0 < rss_stop_bytes <= 512 * MIB
            or not 0 < max_run_bytes <= 512 * MIB
            or not 0 < max_records <= 1000
            or not 0 < max_bytes <= MIB
            or (min_free_bytes != 0 and min_free_bytes < STOP_FREE_BYTES)
            or not 1 <= roll_bytes <= 64 * MIB
            or not 1 <= audit_interval <= 256
        ):
            raise ValueError("journal prototype limits")
        if fault_after <= 0:
            raise ValueError("fault_after")
        self.root = root
        self.ack_seconds = ack_seconds
        self.deadline: float | None = None
        self.max_records = max_records
        self.max_bytes = max_bytes
        self.rows: list[JsonRow] = []
        self.encoded_bytes = 0
        self.cursor: int | None = None
        self.closed = False
        self.failed = False
        self.worker_rss_peak = 0
        self._rss_stop_bytes = rss_stop_bytes
        self._worker_metrics: JsonRow = {}
        self._ack_samples: list[float] = []
        self._parent_rss_sampler = parent_rss_sampler
        self._disk_free_sampler = disk_free_sampler
        self._min_free_bytes = min_free_bytes
        self._max_run_bytes = max_run_bytes
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._stop_lock = threading.RLock()
        self._closing = False
        self._resource_stopped = False
        self.resource_failure_reason: str | None = None
        self._parent_samples = self._parent_peak = self._parent_gap = 0
        self._parent_last = 0
        context = mp.get_context("spawn")
        self._shared = {
            name: context.RawValue("Q", 0)
            for name in (
                "peak",
                "samples",
                "heartbeat",
                "maxgap",
                "failure",
                "monitor_alive",
                "monitor_exit",
            )
        }
        child, self.send_connection = context.Pipe(duplex=False)
        self.connection, child_ack = context.Pipe(duplex=False)
        self._pipes_closed = False
        self.stop_status = "OPEN"
        self.process = context.Process(
            target=_worker,
            args=(
                child,
                child_ack,
                str(root),
                max_run_bytes,
                min_free_bytes,
                roll_bytes,
                audit_interval,
                rss_stop_bytes,
                fault_operation,
                fault_after,
                fault_stage,
                self._shared,
                worker_rss_sampler,
                disk_free_sampler,
            ),
            daemon=True,
        )
        self.process.start()
        os.set_blocking(self.connection.fileno(), False)
        os.set_blocking(self.send_connection.fileno(), False)
        child.close()
        child_ack.close()
        try:
            reply = self._receive(self._command_deadline())
            self._accept_reply(reply)
            self._monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
            self._monitor_thread.start()
        except Exception:
            self._stop()
            raise

    def _monitor_resources(self) -> None:
        try:
            while not self._monitor_stop.wait(0.1):
                self._sample_resources(parent_thread=True)
                if self.resource_failure_reason:
                    self._resource_stop()
                    return
        except Exception:
            self.resource_failure_reason = "PARENT_MONITOR_FAILED"
            self._shared["failure"].value = 4
            self._resource_stop()
        finally:
            if not self._monitor_stop.is_set() and not self._closing:
                self.resource_failure_reason = "PARENT_MONITOR_FAILED"
                self._shared["failure"].value = 4
                self._resource_stop()

    def _sample_resources(self, *, parent_thread: bool = False) -> None:
        now = time.monotonic_ns()
        parent = int(self._parent_rss_sampler() if self._parent_rss_sampler else _rss_bytes())
        self._parent_peak = max(self._parent_peak, parent)
        self.worker_rss_peak = max(self.worker_rss_peak, int(self._shared["peak"].value))
        if parent_thread:
            if self._parent_last:
                self._parent_gap = max(self._parent_gap, now - self._parent_last)
            self._parent_last = now
            self._parent_samples += 1
        failure = int(self._shared["failure"].value)
        if failure:
            self.resource_failure_reason = RESOURCE_ERRORS.get(failure, "RESOURCE_MONITOR_FAILED")
        elif parent + self.worker_rss_peak > self._rss_stop_bytes:
            self.resource_failure_reason = "RSS_LIMIT"
            self._shared["failure"].value = 1
        elif not self._closing and not self.process.is_alive():
            self.resource_failure_reason = "JOURNAL_WORKER_EXITED"
        elif (
            not self._closing
            and self._shared["heartbeat"].value
            and now - self._shared["heartbeat"].value > 1_000_000_000
        ):
            self.resource_failure_reason = "CHILD_MONITOR_FAILED"
            self._shared["failure"].value = 3

    def _resource_stop(self) -> None:
        with self._stop_lock:
            if self._resource_stopped:
                return
            # Allow the cooperative worker to persist its terminal receipt first.
            self.process.join(timeout=0.15)
            self.failed = True
            self._stop()
            self._parent_failure_receipt()
            self._resource_stopped = True

    def _parent_failure_receipt(self) -> None:
        if self.process.is_alive():
            self._worker_metrics["failure_receipt_status"] = "STOP_FAILED"
            return
        path = self.root / "errors" / "storage_events.jsonl"
        try:
            charges, _, _ = _audit_tree(self.root, allow_checkpoint_temp=True)
            if path.exists() and "JOURNAL_WRITER_FAILURE" in path.read_text():
                # A killed worker may have written the receipt but not fsynced it.
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                _fsync_dir(path.parent, [])
                self._worker_metrics["failure_receipt_status"] = "DURABLE_WORKER"
                return
            budget = Budget(
                self._max_run_bytes,
                self._min_free_bytes,
                terminal_audit_reserve_bytes=TERMINAL_RESERVE_BYTES,
            )
            budget.set_physical_used(charges)
            free = int(
                self._disk_free_sampler()
                if self._disk_free_sampler
                else shutil.disk_usage(self.root).free
            )
            record = _json_line(
                {
                    "event_type": "JOURNAL_PARENT_FAILURE",
                    "ts_receive_ns": time.time_ns(),
                    "trusted_cursor": self.cursor,
                    "error": self.resource_failure_reason,
                    "stop_status": self.stop_status,
                }
            )
            budget.reserve_terminal_audit(len(record), free)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(record)
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_dir(path.parent, [])
            self._worker_metrics["failure_receipt_status"] = "DURABLE_PARENT"
        except Exception as exc:
            self._worker_metrics["failure_receipt_status"] = f"FAILED:{type(exc).__name__}"

    def _check_resources(self) -> None:
        if self._closing:
            return
        if (
            self._monitor_thread is not None
            and not self._monitor_thread.is_alive()
            and not self.failed
        ):
            self.resource_failure_reason = "PARENT_MONITOR_FAILED"
            self._shared["failure"].value = 4
        try:
            self._sample_resources()
        except Exception:
            self.resource_failure_reason = "PARENT_MONITOR_FAILED"
            self._shared["failure"].value = 4
        if self.resource_failure_reason:
            self._resource_stop()
            raise WriterFailure(self.resource_failure_reason)

    def _command_deadline(self) -> float:
        deadline = time.monotonic() + self.ack_seconds
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        return deadline

    def _send(self, payload: bytes, deadline: float) -> None:
        wire = struct.pack("!i", len(payload)) + payload
        descriptor = self.send_connection.fileno()
        offset = 0
        while offset < len(wire):
            self._check_resources()
            remaining = deadline - time.monotonic()
            wait = min(0.1, remaining)
            if wait <= 0 or not select.select([], [descriptor], [], wait)[1]:
                if remaining > 0:
                    continue
                raise WriterFailure("JOURNAL_SEND_TIMEOUT")
            if not self.process.is_alive():
                raise WriterFailure("JOURNAL_WORKER_EXITED")
            try:
                offset += os.write(descriptor, wire[offset:])
            except BlockingIOError:
                continue
            except (BrokenPipeError, OSError) as exc:
                raise WriterFailure("JOURNAL_WORKER_EXITED") from exc

    def _receive(self, deadline: float) -> JsonRow:
        descriptor = self.connection.fileno()
        received = bytearray()
        expected: int | None = None
        while expected is None or len(received) < expected + 4:
            if self._monitor_thread is not None:
                self._check_resources()
            remaining = deadline - time.monotonic()
            wait = min(0.1, remaining)
            if wait <= 0 or not select.select([descriptor], [], [], wait)[0]:
                if remaining > 0:
                    if not self.process.is_alive():
                        raise WriterFailure("JOURNAL_WORKER_EXITED")
                    continue
                raise WriterFailure("JOURNAL_ACK_TIMEOUT")
            try:
                data = os.read(descriptor, 16_384)
            except BlockingIOError:
                continue
            except OSError as exc:
                raise WriterFailure("JOURNAL_WORKER_EXITED") from exc
            if not data:
                raise WriterFailure("JOURNAL_WORKER_EXITED")
            received.extend(data)
            if len(received) >= 4 and expected is None:
                expected = struct.unpack("!i", received[:4])[0]
                if expected < 0 or expected > MAX_ACK_BYTES:
                    raise WriterFailure("JOURNAL_ACK_LENGTH")
        return cast(JsonRow, json.loads(received[4:], object_hook=_json_object_hook))

    def _accept_reply(self, reply: JsonRow) -> None:
        self.worker_rss_peak = max(self.worker_rss_peak, int(reply.get("worker_rss_peak", 0)))
        if isinstance(reply.get("metrics"), dict):
            self._worker_metrics = dict(reply["metrics"])
        if reply.get("ok") is not True:
            self.failed = True
            raise WriterFailure(str(reply.get("error", "JOURNAL_WORKER_ERROR")))
        self.cursor = reply.get("cursor")

    def _command(self, request: JsonRow) -> JsonRow:
        self._check_resources()
        if self.failed or self.closed:
            raise WriterFailure("JOURNAL_WRITER_NOT_OPEN")
        upper_bound = _request_encoded_upper_bound(request)
        if upper_bound > MAX_COMMAND_BYTES:
            raise WriterFailure("WRITER_COMMAND_LIMIT")
        if _rss_bytes() + 2 * upper_bound > self._rss_stop_bytes:
            raise WriterFailure("RSS_ALLOCATION_LIMIT")
        payload = _canonical(request)
        began = time.perf_counter()
        try:
            deadline = self._command_deadline()
            self._send(payload, deadline)
            reply = self._receive(deadline)
            self._accept_reply(reply)
            return reply
        except Exception as exc:
            self.failed = True
            self._stop()
            if isinstance(exc, WriterFailure):
                raise
            raise WriterFailure(f"JOURNAL_IPC:{type(exc).__name__}") from exc
        finally:
            self._ack_samples.append(time.perf_counter() - began)

    def append(self, row: JsonRow) -> None:
        self._check_resources()
        size = len(_json_line(row))
        if size > self.max_bytes or len(self.rows) + 1 > self.max_records:
            raise WriterFailure("QUEUE_LIMIT")
        if self.encoded_bytes + size > self.max_bytes:
            raise WriterFailure("QUEUE_LIMIT")
        if self.rows and _partition_for_row(self.rows[0]) != _partition_for_row(row):
            self.flush()
        previous = self.rows[-1]["ingest_sequence"] if self.rows else self.cursor
        if previous is not None and int(row["ingest_sequence"]) <= int(previous):
            raise WriterFailure("INPUT_SEQUENCE_LIMIT")
        self.rows.append(deepcopy(row))
        self.encoded_bytes += size

    def flush(self) -> JsonRow | None:
        if not self.rows:
            return None
        reply = self._command({"operation": "batch", "rows": self.rows})
        self.rows.clear()
        self.encoded_bytes = 0
        return reply.get("manifest")

    def append_durable(self, row: JsonRow) -> None:
        self.append(row)
        self.flush()

    def write_snapshot(self, relative_path: str, payload: bytes) -> None:
        self._command(
            {
                "operation": "snapshot",
                "path": relative_path,
                "base64": base64.b64encode(payload).decode("ascii"),
            }
        )

    def write_table(self, name: str, rows: list[JsonRow]) -> JsonRow:
        reply = self._command({"operation": "table", "name": name, "rows": rows})
        return {"manifest": reply.get("manifest")}

    def checkpoint(self) -> JsonRow:
        reply = self._command({"operation": "checkpoint"})
        return dict(reply.get("manifest") or {})

    @property
    def metrics(self) -> JsonRow:
        return {
            **self._worker_metrics,
            "ack_count": len(self._ack_samples),
            "ack_p95_seconds": _percentile(self._ack_samples, 0.95),
            "ack_max_seconds": max(self._ack_samples, default=0.0),
            "child_rss_peak_bytes": self.worker_rss_peak,
            "parent_rss_peak_bytes": self._parent_peak,
            "combined_ru_maxrss_peak_upper_bound_bytes": self._parent_peak + self.worker_rss_peak,
            "rss_warning": self._parent_peak + self.worker_rss_peak > 384 * MIB,
            "rss_sample_interval_ns": 100_000_000,
            "parent_monitor_samples": self._parent_samples,
            "child_monitor_samples": int(self._shared["samples"].value),
            "parent_monitor_max_sample_gap_ns": self._parent_gap,
            "child_monitor_max_sample_gap_ns": int(self._shared["maxgap"].value),
            "resource_failure_reason": self.resource_failure_reason,
        }

    def _stop(self) -> None:
        self._monitor_stop.set()
        with self._stop_lock:
            self._stop_inner()

    def _stop_inner(self) -> None:
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=0.1)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=0.2)
        self.stop_status = "STOP_FAILED" if self.process.is_alive() else "STOPPED_UNCERTAIN"
        if not self._pipes_closed:
            self.connection.close()
            self.send_connection.close()
            self._pipes_closed = True

    def close(self) -> None:
        if self.closed:
            return
        if self.failed:
            self._stop()
            raise WriterFailure("JOURNAL_STOPPED_UNCERTAIN")
        self.flush()
        self._closing = True
        self._command({"operation": "close"})
        self.process.join(timeout=0.5)
        if self.process.is_alive():
            self.failed = True
            self._stop()
            raise WriterFailure("JOURNAL_STOP_FAILED")
        self.closed = True
        self._monitor_stop.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=0.2)
        if not self._pipes_closed:
            self.connection.close()
            self.send_connection.close()
            self._pipes_closed = True


def _read_checkpoint(path: Path) -> JsonRow:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if envelope.get("schema_version") != SCHEMA_VERSION:
        raise WriterFailure("CHECKPOINT_SCHEMA")
    encoded = base64.b64decode(envelope["payload_base64"], validate=True)
    if hashlib.sha256(encoded).hexdigest() != envelope.get("payload_sha256"):
        raise WriterFailure("CHECKPOINT_HASH")
    value = json.loads(encoded)
    if value.get("schema_version") != SCHEMA_VERSION:
        raise WriterFailure("CHECKPOINT_SCHEMA")
    return cast(JsonRow, value)


def _scan_partition(root: Path, partition: Partition) -> tuple[list[_Frame], list[JsonRow]]:
    frames: list[_Frame] = []
    events: list[JsonRow] = []
    previous_hash = ZERO_HASH
    paths = sorted((root / "journal" / partition).glob(f"{partition}-*.jnl"))
    expected_index = 1
    for path in paths:
        try:
            index = int(path.stem.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            events.append({"event_type": "JOURNAL_NAME_INVALID", "path": path.name})
            break
        if index != expected_index:
            events.append({"event_type": "JOURNAL_FILE_GAP", "path": path.name})
            break
        expected_index += 1
        with path.open("rb") as handle:
            file_header = handle.read(FILE_HEADER.size)
            if len(file_header) != FILE_HEADER.size:
                events.append({"event_type": "JOURNAL_FILE_HEADER_TRUNCATED", "path": path.name})
                break
            magic, version, partition_id = FILE_HEADER.unpack(file_header)
            if (
                magic != FILE_MAGIC
                or version != FORMAT_VERSION
                or partition_id != PARTITION_ID[partition]
            ):
                events.append({"event_type": "JOURNAL_FILE_HEADER_INVALID", "path": path.name})
                break
            while True:
                start = handle.tell()
                raw_header = handle.read(FRAME_HEADER.size)
                if not raw_header:
                    break
                if len(raw_header) != FRAME_HEADER.size:
                    events.append(
                        {
                            "event_type": "JOURNAL_FRAME_TRUNCATED",
                            "path": path.name,
                            "offset": start,
                        }
                    )
                    return frames, events
                try:
                    (
                        magic,
                        version,
                        partition_id,
                        flags,
                        batch_id,
                        first_sequence,
                        last_sequence,
                        count,
                        payload_length,
                        prior,
                        payload_hash,
                        crc,
                    ) = FRAME_HEADER.unpack(raw_header)
                except struct.error:
                    events.append({"event_type": "JOURNAL_HEADER_INVALID", "path": path.name})
                    return frames, events
                expected_crc = zlib.crc32(raw_header[:-4]) & 0xFFFFFFFF
                if (
                    magic != FRAME_MAGIC
                    or version != FORMAT_VERSION
                    or partition_id != PARTITION_ID[partition]
                    or flags != 0
                    or payload_length > MAX_FRAME_PAYLOAD_BYTES
                    or crc != expected_crc
                    or prior != previous_hash
                ):
                    events.append(
                        {
                            "event_type": "JOURNAL_HEADER_OR_CHAIN_INVALID",
                            "path": path.name,
                            "offset": start,
                        }
                    )
                    return frames, events
                payload = handle.read(payload_length)
                trailer = handle.read(COMMIT_TRAILER.size)
                if len(payload) != payload_length or len(trailer) != COMMIT_TRAILER.size:
                    events.append(
                        {
                            "event_type": "JOURNAL_FRAME_TRUNCATED",
                            "path": path.name,
                            "offset": start,
                        }
                    )
                    return frames, events
                commit_magic, committed_hash, total_length = COMMIT_TRAILER.unpack(trailer)
                frame_hash = hashlib.sha256(raw_header + payload).digest()
                if (
                    hashlib.sha256(payload).digest() != payload_hash
                    or commit_magic != COMMIT_MAGIC
                    or committed_hash != frame_hash
                    or total_length != FRAME_HEADER.size + payload_length + COMMIT_TRAILER.size
                ):
                    events.append(
                        {
                            "event_type": "JOURNAL_CHECKSUM_FAILURE",
                            "path": path.name,
                            "offset": start,
                        }
                    )
                    return frames, events
                try:
                    decoded = json.loads(payload, object_hook=_json_object_hook)
                    rows = decoded["rows"]
                except (ValueError, KeyError, TypeError):
                    events.append(
                        {
                            "event_type": "JOURNAL_PAYLOAD_INVALID",
                            "path": path.name,
                            "offset": start,
                        }
                    )
                    return frames, events
                if not isinstance(rows, list) or len(rows) != count:
                    events.append(
                        {"event_type": "JOURNAL_COUNT_INVALID", "path": path.name, "offset": start}
                    )
                    return frames, events
                if partition in {"raw", "control"}:
                    sequences = [int(row["ingest_sequence"]) for row in rows]
                    if (
                        not sequences
                        or sequences[0] != first_sequence
                        or sequences[-1] != last_sequence
                        or sequences != list(range(first_sequence, last_sequence + 1))
                        or any(_partition_for_row(row) != partition for row in rows)
                    ):
                        events.append(
                            {
                                "event_type": "JOURNAL_SEQUENCE_INVALID",
                                "path": path.name,
                                "offset": start,
                            }
                        )
                        return frames, events
                frames.append(
                    _Frame(
                        partition,
                        batch_id,
                        first_sequence,
                        last_sequence,
                        rows,
                        index,
                        start,
                        handle.tell(),
                        frame_hash.hex(),
                    )
                )
                previous_hash = frame_hash
    return frames, events


def recover_test_only_journal(root: Path) -> JsonRow:
    before = _safe_tree_digest(root)
    try:
        descriptor = json.loads((root / "journal-format.json").read_text(encoding="utf-8"))
        if (
            descriptor.get("schema_version") != SCHEMA_VERSION
            or descriptor.get("test_only") is not True
        ):
            raise WriterFailure("JOURNAL_FORMAT")
        charges, files, audit_events = _audit_tree(root, allow_checkpoint_temp=True)
        _check_charges(charges, 512 * MIB)
    except JournalAuditFailure as exc:
        return {
            "status": "BLOCKED",
            "trusted_cursor": None,
            "rows": [],
            "feature_tables": {},
            "recovery_events": [{"event_type": "RECOVERY_FAILED", "reason": str(exc)}],
            "partition_bytes": exc.charges,
            "files": len(exc.files),
            "input_unchanged": before == _safe_tree_digest(root),
        }
    except (OSError, ValueError, TypeError, WriterFailure) as exc:
        return {
            "status": "BLOCKED",
            "trusted_cursor": None,
            "rows": [],
            "feature_tables": {},
            "recovery_events": [{"event_type": "RECOVERY_FAILED", "reason": str(exc)}],
            "input_unchanged": before == _safe_tree_digest(root),
        }
    all_frames: dict[Partition, list[_Frame]] = {}
    events = list(audit_events)
    for partition in PARTITION_ID:
        frames, observed = _scan_partition(root, partition)
        all_frames[partition] = frames
        events.extend(observed)
    rows = [row for frame in all_frames["raw"] for row in frame.rows]
    rows.extend(row for frame in all_frames["control"] for row in frame.rows)
    rows.sort(key=lambda row: int(row["ingest_sequence"]))
    trusted: list[JsonRow] = []
    expected = 1
    for row in rows:
        sequence = int(row["ingest_sequence"])
        if sequence != expected:
            events.append(
                {"event_type": "GLOBAL_SEQUENCE_GAP", "expected": expected, "observed": sequence}
            )
            break
        trusted.append(row)
        expected += 1
        if len(trusted) > MAX_RECOVERY_ROWS:
            events.append({"event_type": "RECOVERY_ROW_LIMIT"})
            break
    checkpoints: list[JsonRow] = []
    for slot in (root / "checkpoints" / "slot-a.json", root / "checkpoints" / "slot-b.json"):
        if not slot.exists():
            continue
        try:
            checkpoints.append(_read_checkpoint(slot))
        except (OSError, ValueError, KeyError, TypeError, WriterFailure) as exc:
            events.append(
                {"event_type": "CHECKPOINT_INVALID", "path": slot.name, "reason": str(exc)}
            )

    def checkpoint_matches(value: JsonRow) -> bool:
        if int(value.get("trusted_cursor") or 0) > len(trusted):
            return False
        journal_values = value.get("journals")
        if not isinstance(journal_values, dict):
            return False
        for partition in PARTITION_ID:
            state = journal_values.get(partition)
            if not isinstance(state, dict):
                return False
            index = int(state.get("file_index") or 0)
            offset = int(state.get("committed_offset") or 0)
            digest = str(state.get("last_frame_sha256") or "")
            if index == 0:
                if offset != 0 or digest != ZERO_HASH.hex():
                    return False
                continue
            if not any(
                frame.file_index == index
                and frame.end_offset == offset
                and frame.frame_sha256 == digest
                for frame in all_frames[partition]
            ):
                return False
        return True

    valid_checkpoints = [value for value in checkpoints if checkpoint_matches(value)]
    if len(valid_checkpoints) != len(checkpoints):
        events.append({"event_type": "CHECKPOINT_JOURNAL_MISMATCH"})
    checkpoint = max(
        valid_checkpoints,
        key=lambda item: int(item["generation"]),
        default=None,
    )
    table_groups: dict[tuple[str, str], JsonRow] = {}
    for frame in all_frames["feature"]:
        for item in frame.rows:
            name, table_id, values = item.get("name"), item.get("table_id"), item.get("rows")
            index, count = item.get("chunk_index"), item.get("chunk_count")
            if not (
                isinstance(name, str)
                and isinstance(table_id, str)
                and isinstance(values, list)
                and isinstance(index, int)
                and isinstance(count, int)
                and 0 <= index < count <= 4096
            ):
                events.append({"event_type": "FEATURE_FRAME_INVALID"})
                continue
            group = table_groups.setdefault(
                (name, table_id),
                {"count": count, "chunks": {}, "last_batch_id": frame.batch_id},
            )
            if group["count"] != count or index in group["chunks"]:
                events.append({"event_type": "FEATURE_CHUNK_CONFLICT"})
                continue
            group["chunks"][index] = values
            group["last_batch_id"] = max(group["last_batch_id"], frame.batch_id)
    complete_table_groups: dict[tuple[str, str], JsonRow] = {}
    candidate_feature_tables: dict[str, list[JsonRow]] = {}
    feature_generations: dict[str, int] = {}
    for (name, table_id), group in table_groups.items():
        if len(group["chunks"]) != group["count"]:
            events.append({"event_type": "FEATURE_TABLE_INCOMPLETE", "name": name})
            continue
        assembled = [row for index in range(group["count"]) for row in group["chunks"][index]]
        complete_table_groups[(name, table_id)] = {
            **group,
            "rows": assembled,
            "sha256": hashlib.sha256(_canonical({"name": name, "rows": assembled})).hexdigest(),
        }
        if group["last_batch_id"] < feature_generations.get(name, -1):
            continue
        feature_generations[name] = group["last_batch_id"]
        candidate_feature_tables[name] = assembled

    def matching_cutoff(published: JsonRow) -> JsonRow | None:
        cutoff = published.get("cutoff_ns")
        candidates = [
            row
            for row in trusted
            if row.get("kind") == "FEATURE_CUTOFF"
            and row.get("cutoff_ns") == cutoff
            and int(row["ingest_sequence"]) < int(published["ingest_sequence"])
        ]
        return candidates[-1] if candidates else None

    invalid_publication_sequence: int | None = None
    trusted_feature_cutoffs: list[int] = []
    for published in [row for row in trusted if row.get("kind") == "FEATURE_PUBLISHED"]:
        cutoff = matching_cutoff(published)
        manifest = published.get("feature_manifest")
        publication_group: JsonRow | None = None
        if isinstance(manifest, dict) and isinstance(manifest.get("table_id"), str):
            publication_group = complete_table_groups.get(
                ("features_pending", manifest["table_id"])
            )
        group_rows = None if publication_group is None else publication_group.get("rows")
        valid = (
            cutoff is not None
            and isinstance(manifest, dict)
            and isinstance(publication_group, dict)
            and isinstance(group_rows, list)
            and manifest.get("sha256") == publication_group.get("sha256")
            and manifest.get("batch_id") == publication_group.get("last_batch_id")
            and manifest.get("chunk_count") == publication_group.get("count")
            and bool(group_rows)
            and all(
                row.get("feature_cutoff_ns") == published.get("cutoff_ns")
                and row.get("input_cursor") == cutoff.get("input_cursor")
                for row in group_rows
            )
        )
        if not valid:
            invalid_publication_sequence = (
                int(cutoff["ingest_sequence"])
                if cutoff is not None
                else int(published["ingest_sequence"])
            )
            events.append(
                {
                    "event_type": "FEATURE_PUBLICATION_UNVERIFIED",
                    "cutoff_ns": published.get("cutoff_ns"),
                    "published_sequence": published.get("ingest_sequence"),
                }
            )
            break
        trusted_feature_cutoffs.append(int(published["cutoff_ns"]))
    if invalid_publication_sequence is not None:
        trusted = [
            row for row in trusted if int(row["ingest_sequence"]) < invalid_publication_sequence
        ]
    clean_close = (root / "writer-closed").exists()
    if not clean_close:
        events.append({"event_type": "NO_WRITER_CLOSE_ACK"})
    status = "PASS" if not events and checkpoint is not None and clean_close else "PARTIAL"
    feature_tables = candidate_feature_tables if status == "PASS" else {}
    return {
        "status": status,
        "trusted_cursor": len(trusted) or None,
        "physical_trusted_cursor": expected - 1 or None,
        "rows": trusted,
        "feature_tables": feature_tables,
        "trusted_feature_cutoffs": trusted_feature_cutoffs,
        "recovery_events": events,
        "checkpoint": checkpoint,
        "partition_bytes": charges,
        "files": len(files),
        "frames": {name: len(values) for name, values in all_frames.items()},
        "frame_details": {
            name: [
                {
                    "batch_id": frame.batch_id,
                    "first_sequence": frame.first_sequence,
                    "last_sequence": frame.last_sequence,
                    "path_index": frame.file_index,
                    "start_offset": frame.start_offset,
                    "end_offset": frame.end_offset,
                    "sha256": frame.frame_sha256,
                }
                for frame in values
            ]
            for name, values in all_frames.items()
        },
        "input_unchanged": before == _safe_tree_digest(root),
    }


def replay_test_only_journal(root: Path) -> JsonRow:
    before = _safe_tree_digest(root)
    recovered = recover_test_only_journal(root)
    if recovered["status"] == "BLOCKED":
        return {
            **recovered,
            "network_access": "DISABLED",
            "read_only": True,
            "replay_matches_original": None,
            "normalized": [],
            "quarantine": [],
            "features": [],
        }
    try:
        context = object_json((root / "snapshots" / "run-context.json").read_bytes())
        evidence_bytes = _decode_snapshot(context["unit_evidence"])
        metadata_bytes = _decode_snapshot(context["metadata_response"])
        evidence, evidence_hash = load_unit_evidence(evidence_bytes)
        binding = resolve_test_only_unit_binding(
            evidence=evidence,
            evidence_sha256=evidence_hash,
            current_metadata=object_json(metadata_bytes),
            approval=context["unit_approval"],
            source_stream=STREAM,
            symbol_type_raw=None,
        )
        config = context["config"]
        core = Core(
            {"time_view": "as_received", "window_seconds": config["window_seconds"]},
            dict(binding.core_metadata()),
            binding=binding,
        )
        for row in recovered["rows"]:
            if row["kind"] in {
                "SESSION_OPEN",
                "SESSION_CLOSE",
                "SUBSCRIBED",
                "RAW",
                "FEATURE_CUTOFF",
                "FEATURE_PUBLISHED",
                "GAP_OPEN",
                "GAP_CLOSE",
                "STORAGE_ERROR",
                "CLOCK_UNCERTAIN",
                "UNCERTAIN_GAP",
            }:
                core.process(_core_row(row))
        published_features = list(core.features)
        pending_features = core.pending_features
        features_for_digest = [*published_features, *pending_features]
        replay_tables = [
            _arrow_table(rows).to_pylist()
            for rows in (core.normalized, core.quarantine, features_for_digest)
        ]
        stored_tables = recovered["feature_tables"]
        original = [
            stored_tables.get("normalized"),
            stored_tables.get("quarantine"),
            stored_tables.get("features"),
        ]
        matches = None if any(value is None for value in original) else original == replay_tables
        status = recovered["status"]
        if matches is False or before != _safe_tree_digest(root):
            status = "FAIL"
        elif matches is None or core.pending_features:
            status = "PARTIAL"
        return {
            **recovered,
            "status": status,
            "network_access": "DISABLED",
            "read_only": True,
            "input_unchanged": before == _safe_tree_digest(root),
            "logical_sha256": _tables_digest(replay_tables),
            "replay_matches_original": matches,
            "normalized": core.normalized,
            "quarantine": core.quarantine,
            "features": published_features,
            "pending_features": pending_features,
        }
    except Exception as exc:
        return {
            **recovered,
            "status": "BLOCKED",
            "network_access": "DISABLED",
            "read_only": True,
            "input_unchanged": before == _safe_tree_digest(root),
            "replay_matches_original": None,
            "normalized": [],
            "quarantine": [],
            "features": [],
            "replay_error": f"{type(exc).__name__}:{exc}",
        }


def replay_test_only_storage(root: Path) -> JsonRow:
    """Read-only dispatcher; old stores retain the existing reader unchanged."""

    if (root / "journal-format.json").exists():
        return replay_test_only_journal(root)
    return replay_bounded(root)


__all__ = [
    "SCHEMA_VERSION",
    "TestOnlyJournalProcessWriter",
    "recover_test_only_journal",
    "replay_test_only_journal",
    "replay_test_only_storage",
]
