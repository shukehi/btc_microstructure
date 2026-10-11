"""Offline raw/control storage with atomic manifests and trusted recovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, NamedTuple, cast

import pyarrow as pa
import pyarrow.parquet as pq

JsonRow = dict[str, Any]

MANIFEST_SCHEMA_VERSION = "offline-storage-manifest-1"
RAW_SCHEMA_VERSION = "raw-control-jsonl-1"
TABLE_SCHEMA_VERSION = "derived-parquet-1"


class StorageError(RuntimeError):
    """Raised when storage cannot safely commit or preserve continuity."""


@dataclass(frozen=True)
class QueueStats:
    records: int
    bytes: int
    oldest_age_ns: int | None


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return {"__decimal__": format(value, "f")}
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _json_object_hook(value: dict[str, Any]) -> object:
    if set(value) == {"__decimal__"}:
        return Decimal(str(value["__decimal__"]))
    return value


def _json_line(row: JsonRow) -> bytes:
    text = json.dumps(row, sort_keys=True, separators=(",", ":"), default=_json_default)
    return f"{text}\n".encode("utf-8")


def _write_json_atomic(path: Path, payload: JsonRow) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, indent=2, default=_json_default) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _read_json(path: Path) -> JsonRow:
    return cast(
        JsonRow, json.loads(path.read_text(encoding="utf-8"), object_hook=_json_object_hook)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_sequence(row: JsonRow) -> int:
    value = row.get("ingest_sequence")
    if not isinstance(value, int) or isinstance(value, bool):
        raise StorageError("row.ingest_sequence must be an integer")
    return value


def _row_receive_ns(row: JsonRow) -> int:
    value = row.get("ts_receive_ns")
    if not isinstance(value, int) or isinstance(value, bool):
        raise StorageError("row.ts_receive_ns must be an integer")
    return value


class JsonlStats(NamedTuple):
    row_count: int
    sequences: list[int]


def _quality_counts(rows: list[JsonRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        flags = row.get("quality_flags", [])
        if isinstance(flags, str):
            values = [flags]
        elif isinstance(flags, list):
            values = [str(flag) for flag in flags]
        else:
            values = [str(flags)]
        if not values:
            counts["OK"] = counts.get("OK", 0) + 1
            continue
        for flag in values:
            counts[flag] = counts.get(flag, 0) + 1
    return counts


class Store:
    """Append-only offline store for R1-03 raw/control logs and derived tables."""

    def __init__(
        self,
        root: Path,
        *,
        run_metadata: dict[str, Any] | None = None,
        max_records: int = 1000,
        max_bytes: int = 1_048_576,
        max_age_ns: int = 1_000_000_000,
        min_free_bytes: int = 0,
        disk_usage: Callable[[Path], Any] | None = None,
    ) -> None:
        if max_records <= 0 or max_bytes <= 0 or max_age_ns < 0 or min_free_bytes < 0:
            raise ValueError("queue limits must be positive, with non-negative age/free limits")
        self.root = root
        self.raw_dir = root / "raw"
        self.table_dir = root / "tables"
        self.manifest_dir = root / "manifests"
        self.quarantine_dir = root / "quarantine"
        self.error_log = root / "errors" / "storage_events.jsonl"
        self.run_metadata = dict(run_metadata or {})
        self.max_records = max_records
        self.max_bytes = max_bytes
        self.max_age_ns = max_age_ns
        self.min_free_bytes = min_free_bytes
        self._disk_usage = disk_usage or shutil.disk_usage
        self._queue: list[JsonRow] = []
        self._queue_bytes = 0
        self._cursor: int | None = None
        self._segment_index = 0
        self._trusted_manifests: list[JsonRow] = []
        self.recovery_events: list[JsonRow] = []
        self._recovery_blocked = False
        self._closed = False
        self._failed = False
        for directory in (
            self.raw_dir,
            self.table_dir,
            self.manifest_dir,
            self.quarantine_dir,
            self.error_log.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if self.error_log.exists():
            try:
                for line in self.error_log.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    if event.get("event_type") == "UNCERTAIN_GAP":
                        self.recovery_events.append(event)
                    elif (
                        event.get("event_type") == "STORAGE_ERROR"
                        and event.get("reason") == "FLUSH_FAILED"
                    ):
                        # Historical failures may already have moved raw into quarantine.
                        # Keep the original audit bytes; derive recovery state from them.
                        self.recovery_events.append(
                            {
                                **event,
                                "event_type": "UNCERTAIN_GAP",
                                "reason": "RAW_COMMIT_FAILED",
                                "trusted_cursor": event.get(
                                    "trusted_cursor", event.get("ingest_sequence")
                                ),
                            }
                        )
            except (OSError, ValueError, AttributeError):
                self.recovery_events.append(
                    {"event_type": "UNCERTAIN_GAP", "reason": "UNREADABLE_RECOVERY_AUDIT"}
                )
        self._recover()
        self._recover_quarantined_raw()

    @property
    def cursor(self) -> int | None:
        return self._cursor

    @property
    def queue_stats(self) -> QueueStats:
        return self.queue_stats_at(time.time_ns())

    def queue_stats_at(self, now_ns: int) -> QueueStats:
        if not self._queue:
            return QueueStats(records=0, bytes=0, oldest_age_ns=None)
        oldest = min(_row_receive_ns(row) for row in self._queue)
        return QueueStats(
            records=len(self._queue),
            bytes=self._queue_bytes,
            oldest_age_ns=max(0, now_ns - oldest),
        )

    def append(self, row: JsonRow) -> None:
        self._ensure_open()
        self._ensure_free_space()
        sequence = _row_sequence(row)
        receive_ns = _row_receive_ns(row)
        if self._cursor is not None and sequence <= self._cursor:
            raise StorageError("row.ingest_sequence must advance beyond committed cursor")
        if self._queue and sequence <= max(_row_sequence(item) for item in self._queue):
            raise StorageError("row.ingest_sequence must be strictly increasing within queue")
        encoded = _json_line(row)
        next_records = len(self._queue) + 1
        next_bytes = self._queue_bytes + len(encoded)
        if next_records > self.max_records:
            self._record_error(
                "STORAGE_ERROR",
                {
                    "reason": "QUEUE_RECORD_LIMIT",
                    "records": next_records,
                    "limit": self.max_records,
                },
            )
            raise StorageError("ingest queue record limit exceeded")
        if next_bytes > self.max_bytes:
            self._record_error(
                "STORAGE_ERROR",
                {"reason": "QUEUE_BYTE_LIMIT", "bytes": next_bytes, "limit": self.max_bytes},
            )
            raise StorageError("ingest queue byte limit exceeded")
        if self._queue:
            oldest = min(_row_receive_ns(item) for item in self._queue)
            if receive_ns - oldest > self.max_age_ns:
                self._record_error(
                    "STORAGE_ERROR",
                    {
                        "reason": "QUEUE_AGE_LIMIT",
                        "oldest_age_ns": receive_ns - oldest,
                        "limit": self.max_age_ns,
                    },
                )
                raise StorageError("ingest queue age limit exceeded")
        self._queue.append(deepcopy(row))
        self._queue_bytes = next_bytes

    def flush(self) -> JsonRow | None:
        self._ensure_open()
        self._ensure_free_space()
        if not self._queue:
            return None
        rows = list(self._queue)
        before_cursor = self._cursor
        after_cursor = max(_row_sequence(row) for row in rows)
        receive_times = [_row_receive_ns(row) for row in rows]
        segment_index = self._next_raw_segment_index()
        segment_name = f"raw-control-{segment_index:06d}.jsonl"
        tmp_path = self.raw_dir / f"{segment_name}.tmp"
        final_path = self.raw_dir / segment_name
        manifest_path = self.manifest_dir / f"manifest-raw-{segment_index:06d}.json"
        try:
            with tmp_path.open("wb") as handle:
                for row in rows:
                    handle.write(_json_line(row))
                handle.flush()
                os.fsync(handle.fileno())
            digest = _sha256(tmp_path)
            if _validate_jsonl(tmp_path).row_count != len(rows):
                raise StorageError("temporary raw segment row count verification failed")
            os.replace(tmp_path, final_path)
            _fsync_dir(self.raw_dir)
            if _sha256(final_path) != digest:
                raise StorageError("committed raw checksum verification failed")
            manifest: JsonRow = {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "kind": "raw_control_segment",
                "segment_index": segment_index,
                "path": _relative_path(self.root, final_path),
                "sha256": digest,
                "row_count": len(rows),
                "time_range": {
                    "min_ts_receive_ns": min(receive_times),
                    "max_ts_receive_ns": max(receive_times),
                },
                "versions": {"raw_schema_version": RAW_SCHEMA_VERSION},
                "quality_counts": _quality_counts(rows),
                "cursor": {"before": before_cursor, "after": after_cursor},
                "run_metadata": self.run_metadata,
                "created_ns": time.time_ns(),
            }
            _write_json_atomic(manifest_path, manifest)
        except Exception as exc:
            self._failed = True
            self._record_error(
                "STORAGE_ERROR",
                {
                    "reason": "FLUSH_FAILED",
                    "segment_path": _relative_path(self.root, final_path),
                    "trusted_cursor": before_cursor,
                    "first_ingest_sequence": min(_row_sequence(row) for row in rows),
                    "last_ingest_sequence": after_cursor,
                    "error_type": type(exc).__name__,
                },
            )
            if tmp_path.exists():
                _best_effort_quarantine(tmp_path, self.quarantine_dir)
            if isinstance(exc, StorageError):
                raise
            raise StorageError("failed to commit raw/control segment") from exc
        self._trusted_manifests.append(manifest)
        self._segment_index = segment_index
        self._cursor = after_cursor
        self._queue.clear()
        self._queue_bytes = 0
        return manifest

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._closed = True

    def read_rows(self) -> list[JsonRow]:
        rows: list[JsonRow] = []
        for manifest in self._trusted_manifests:
            if manifest.get("kind") != "raw_control_segment":
                continue
            path = self.root / str(manifest["path"])
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    rows.append(cast(JsonRow, json.loads(line, object_hook=_json_object_hook)))
        return rows

    def write_table(self, name: str, rows: list[JsonRow]) -> JsonRow:
        self._ensure_open()
        self._ensure_free_space()
        _validate_table_name(name)
        table_index = self._next_manifest_index("manifest-table")
        table_dir = self.table_dir / name
        table_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = table_dir / f"{name}-{table_index:06d}.parquet.tmp"
        final_path = table_dir / f"{name}-{table_index:06d}.parquet"
        manifest_path = self.manifest_dir / f"manifest-table-{name}-{table_index:06d}.json"
        before_cursor = self._cursor
        try:
            table = _arrow_table(rows)
            pq.write_table(table, tmp_path, compression="zstd")
            with tmp_path.open("rb") as handle:
                os.fsync(handle.fileno())
            readback = pq.read_table(tmp_path)
            if readback.num_rows != len(rows) or not readback.equals(table):
                raise StorageError("temporary parquet logical readback verification failed")
            digest = _sha256(tmp_path)
            os.replace(tmp_path, final_path)
            _fsync_dir(table_dir)
            if _sha256(final_path) != digest:
                raise StorageError("committed parquet checksum verification failed")
            receive_times = [
                value
                for row in rows
                if isinstance((value := row.get("ts_receive_ns")), int)
                and not isinstance(value, bool)
            ]
            manifest: JsonRow = {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "kind": "derived_table",
                "name": name,
                "path": _relative_path(self.root, final_path),
                "sha256": digest,
                "row_count": len(rows),
                "time_range": {
                    "min_ts_receive_ns": min(receive_times) if receive_times else None,
                    "max_ts_receive_ns": max(receive_times) if receive_times else None,
                    "min_feature_cutoff_ns": min(
                        (r["feature_cutoff_ns"] for r in rows if "feature_cutoff_ns" in r),
                        default=None,
                    ),
                    "max_feature_cutoff_ns": max(
                        (r["feature_cutoff_ns"] for r in rows if "feature_cutoff_ns" in r),
                        default=None,
                    ),
                },
                "versions": {
                    "table_schema_version": TABLE_SCHEMA_VERSION,
                    "arrow_schema": table.schema.to_string(),
                },
                "quality_counts": _quality_counts(rows),
                "cursor": {"before": before_cursor, "after": self._cursor},
                "run_metadata": self.run_metadata,
                "created_ns": time.time_ns(),
            }
            _write_json_atomic(manifest_path, manifest)
        except Exception as exc:
            self._failed = True
            self._record_error(
                "STORAGE_ERROR",
                {
                    "reason": "TABLE_WRITE_FAILED",
                    "table": name,
                    "error_type": type(exc).__name__,
                },
            )
            if tmp_path.exists():
                _best_effort_quarantine(tmp_path, self.quarantine_dir)
            if isinstance(exc, StorageError):
                raise
            raise StorageError(f"failed to commit table {name}") from exc
        self._trusted_manifests.append(manifest)
        return manifest

    def read_table(self, name: str) -> list[JsonRow]:
        _validate_table_name(name)
        matches = [
            manifest
            for manifest in self._trusted_manifests
            if manifest.get("kind") == "derived_table" and manifest.get("name") == name
        ]
        if not matches:
            raise FileNotFoundError(f"no committed table named {name}")
        manifest = max(matches, key=lambda item: str(item["path"]))
        table = pq.read_table(self.root / str(manifest["path"]))
        return cast(list[JsonRow], table.to_pylist())

    def _ensure_open(self) -> None:
        if self._failed:
            raise StorageError(
                "previous storage commit failed; reopen and verify before further writes"
            )
        if self._closed:
            raise StorageError("store is closed")
        if self._recovery_blocked:
            raise StorageError(
                "store recovered untrusted commits; manual review is required before writing"
            )

    def _ensure_free_space(self) -> None:
        if self.min_free_bytes == 0:
            return
        free = int(self._disk_usage(self.root).free)
        if free < self.min_free_bytes:
            self._record_error(
                "STORAGE_ERROR",
                {"reason": "DISK_FREE_LIMIT", "free_bytes": free, "limit": self.min_free_bytes},
            )
            raise StorageError("disk free space below storage limit")

    def _recover(self) -> None:
        trusted_paths: set[str] = set()
        broken_chain = False
        for manifest_path in sorted(self.manifest_dir.glob("manifest-*.json")):
            try:
                manifest = _read_json(manifest_path)
                if broken_chain:
                    raise StorageError("manifest follows an untrusted commit")
                self._validate_manifest(manifest, self._cursor)
            except Exception as exc:
                is_derived = manifest_path.name.startswith("manifest-table-")
                event_type = (
                    "DERIVED_INVALID" if is_derived and not broken_chain else "UNCERTAIN_GAP"
                )
                event: JsonRow = {
                    "event_type": event_type,
                    "reason": "AFTER_UNTRUSTED_COMMIT" if broken_chain else "UNTRUSTED_MANIFEST",
                    "manifest_path": _relative_path(self.root, manifest_path),
                    "error_type": type(exc).__name__,
                }
                self.recovery_events.append(event)
                self._record_error(event_type, event)
                if not is_derived:
                    broken_chain = True
                self._recovery_blocked = True
                continue
            self._trusted_manifests.append(manifest)
            trusted_paths.add(str(manifest["path"]))
            cursor = (
                manifest.get("cursor", {}).get("after")
                if manifest.get("kind") == "raw_control_segment"
                else None
            )
            if isinstance(cursor, int) and not isinstance(cursor, bool):
                self._cursor = cursor if self._cursor is None else max(self._cursor, cursor)
            index = manifest.get("segment_index")
            if isinstance(index, int) and not isinstance(index, bool):
                self._segment_index = max(self._segment_index, index)
        self._quarantine_untrusted_files(trusted_paths)

    def _validate_manifest(self, manifest: JsonRow, current_cursor: int | None) -> None:
        if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise StorageError("unsupported manifest schema")
        path_value = manifest.get("path")
        expected_sha = manifest.get("sha256")
        if not isinstance(path_value, str) or not isinstance(expected_sha, str):
            raise StorageError("manifest missing path or sha256")
        kind = manifest.get("kind")
        if kind not in {"raw_control_segment", "derived_table"}:
            raise StorageError("manifest has unsupported kind")
        expected_rows = manifest.get("row_count")
        if (
            not isinstance(expected_rows, int)
            or isinstance(expected_rows, bool)
            or expected_rows < 0
        ):
            raise StorageError("manifest row_count must be a non-negative integer")
        cursor = manifest.get("cursor")
        if not isinstance(cursor, dict):
            raise StorageError("manifest cursor must be an object")
        if cursor.get("before") is not None and (
            not isinstance(cursor.get("before"), int) or isinstance(cursor.get("before"), bool)
        ):
            raise StorageError("manifest cursor.before must be null or integer")
        if cursor.get("after") is not None and (
            not isinstance(cursor.get("after"), int) or isinstance(cursor.get("after"), bool)
        ):
            raise StorageError("manifest cursor.after must be null or integer")
        data_path = _bounded_manifest_path(self.root, path_value)
        if not data_path.exists():
            raise StorageError("manifest data file is missing")
        if data_path.is_symlink():
            raise StorageError("manifest data file must not be a symlink")
        if _sha256(data_path) != expected_sha:
            raise StorageError("manifest sha256 mismatch")
        if kind == "raw_control_segment":
            stats = _validate_jsonl(data_path)
            if stats.row_count != expected_rows:
                raise StorageError("raw row count mismatch")
            if sorted(stats.sequences) != stats.sequences or len(set(stats.sequences)) != len(
                stats.sequences
            ):
                raise StorageError("raw ingest sequences must be unique and increasing")
            if cursor.get("before") != current_cursor:
                raise StorageError("raw manifest does not continue trusted cursor")
            if (
                stats.sequences
                and current_cursor is not None
                and min(stats.sequences) <= current_cursor
            ):
                raise StorageError("raw manifest reuses committed input sequences")
            if stats.sequences:
                if cursor.get("after") != max(stats.sequences):
                    raise StorageError("raw manifest after cursor does not match data")
            elif cursor.get("after") != current_cursor:
                raise StorageError("empty raw manifest must not advance cursor")
        if kind == "derived_table":
            for key in ("before", "after"):
                cursor_value = cursor.get(key)
                if (
                    current_cursor is not None
                    and isinstance(cursor_value, int)
                    and not isinstance(cursor_value, bool)
                    and cursor_value > current_cursor
                ):
                    raise StorageError("table manifest cursor is ahead of trusted raw cursor")
            if pq.read_table(data_path).num_rows != expected_rows:
                raise StorageError("table row count mismatch")

    def _quarantine_untrusted_files(self, trusted_paths: set[str]) -> None:
        candidates = [
            *self.raw_dir.glob("*"),
            *self.table_dir.glob("*/*"),
            *self.manifest_dir.glob("*.tmp"),
        ]
        for path in sorted(candidates):
            if not path.is_file():
                continue
            relative = _relative_path(self.root, path)
            if relative in trusted_paths:
                continue
            event = _uncertain_gap_event(self.root, path)
            if path.parent != self.raw_dir and not path.name.startswith("manifest-raw-"):
                event["event_type"] = "DERIVED_INVALID"
                event["reason"] = "UNCOMMITTED_DERIVED_TAIL"
            destination = _best_effort_quarantine(path, self.quarantine_dir)
            event["quarantine_path"] = (
                _relative_path(self.root, destination) if destination else None
            )
            self.recovery_events.append(event)
            self._record_error(str(event["event_type"]), event)

    def _record_error(self, event_type: str, payload: JsonRow) -> None:
        event: JsonRow = {
            "event_type": event_type,
            "ts_receive_ns": time.time_ns(),
            "ingest_sequence": self._cursor,
            **payload,
        }
        try:
            self.error_log.parent.mkdir(parents=True, exist_ok=True)
            with self.error_log.open("ab") as handle:
                handle.write(_json_line(event))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            pass

    def _recover_quarantined_raw(self) -> None:
        # The side audit is best effort during I/O failure. Raw bytes in quarantine
        # are independent evidence, including failures produced by older versions.
        known_paths = {
            str(event[key])
            for event in self.recovery_events
            for key in ("path", "quarantine_path", "segment_path")
            if event.get(key) is not None
        }
        for path in sorted(self.quarantine_dir.glob("raw-control-*.jsonl*")):
            relative = _relative_path(self.root, path)
            if relative in known_paths or not path.is_file():
                continue
            event = _uncertain_gap_event(self.root, path)
            event.update(
                reason="QUARANTINED_RAW_TAIL",
                quarantine_path=relative,
                trusted_cursor=self._cursor,
            )
            self.recovery_events.append(event)
            self._record_error("UNCERTAIN_GAP", event)

    def _next_manifest_index(self, prefix: str) -> int:
        existing = sorted(self.manifest_dir.glob(f"{prefix}-*.json"))
        return len(existing) + 1

    def _next_raw_segment_index(self) -> int:
        indexes = [self._segment_index]
        for directory in (self.raw_dir, self.quarantine_dir):
            for path in directory.glob("raw-control-*.jsonl*"):
                match = re.match(r"raw-control-(\d{6})\.jsonl", path.name)
                if match:
                    indexes.append(int(match.group(1)))
        for path in self.manifest_dir.glob("manifest-raw-*.json"):
            match = re.match(r"manifest-raw-(\d{6})\.json", path.name)
            if match:
                indexes.append(int(match.group(1)))
        return max(indexes) + 1


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _bounded_manifest_path(root: Path, value: str) -> Path:
    candidate = root / value
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        raise StorageError("manifest path escapes storage root")
    return candidate


def _validate_jsonl(path: Path) -> JsonlStats:
    sequences: list[int] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = cast(JsonRow, json.loads(line, object_hook=_json_object_hook))
                if not isinstance(row, dict):
                    raise StorageError("raw JSONL row must be an object")
                sequences.append(_row_sequence(row))
                _row_receive_ns(row)
    return JsonlStats(row_count=len(sequences), sequences=sequences)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _uncertain_gap_event(root: Path, path: Path) -> JsonRow:
    sequences: list[int] = []
    diagnostic_errors: set[str] = set()
    if path.suffix in {".jsonl", ".tmp"} or path.name.startswith("raw-control-"):
        try:
            with path.open("rb") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeError, RecursionError):
                        diagnostic_errors.add("UNDECODABLE_OR_INVALID_JSON")
                        continue
                    if not isinstance(row, dict):
                        diagnostic_errors.add("NON_OBJECT_JSON")
                        continue
                    value = row.get("ingest_sequence")
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        sequences.append(value)
        except OSError:
            diagnostic_errors.add("UNREADABLE_TAIL")
    return {
        "event_type": "UNCERTAIN_GAP",
        "reason": "UNCOMMITTED_RAW_TAIL",
        "path": _relative_path(root, path),
        "first_ingest_sequence": min(sequences) if sequences else None,
        "last_ingest_sequence": max(sequences) if sequences else None,
        "diagnostic_errors": sorted(diagnostic_errors),
    }


def _best_effort_quarantine(path: Path, quarantine_dir: Path) -> Path | None:
    try:
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        destination = quarantine_dir / path.name
        suffix = 1
        while destination.exists():
            destination = quarantine_dir / f"{path.name}.{suffix}"
            suffix += 1
        os.replace(path, destination)
        return destination
    except OSError:
        return None


def _validate_table_name(name: str) -> None:
    if not name or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for char in name
    ):
        raise ValueError("table name must contain only ASCII letters, digits, '_' or '-'")


def _arrow_table(rows: list[JsonRow]) -> pa.Table:
    columns = sorted({key for row in rows for key in row})
    arrays: list[pa.Array[Any]] = []
    fields: list[pa.Field[Any]] = []
    for column in columns:
        values = [row.get(column) for row in rows]
        pa_type = _arrow_type(values)
        arrays.append(
            pa.array([_coerce_arrow_value(value, pa_type) for value in values], type=pa_type)
        )
        fields.append(pa.field(column, pa_type))
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def _arrow_type(values: list[Any]) -> pa.DataType:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return pa.string()
    if any(isinstance(value, Decimal) for value in non_null):
        decimal_values = [value for value in non_null if isinstance(value, Decimal)]
        scale = max((_decimal_scale(value) for value in decimal_values), default=0)
        precision = max(_decimal_integral_digits(value) for value in decimal_values) + scale
        if precision <= 38:
            return pa.decimal128(38, scale)
        if precision <= 76:
            return pa.decimal256(76, scale)
        raise StorageError("decimal precision exceeds Decimal256 capacity")
    if all(
        isinstance(value, list) and all(isinstance(item, str) for item in value)
        for value in non_null
    ):
        return pa.list_(pa.string())
    if all(isinstance(value, bool) for value in non_null):
        return pa.bool_()
    if all(isinstance(value, int) and not isinstance(value, bool) for value in non_null):
        return pa.int64()
    if all(isinstance(value, float) for value in non_null):
        return pa.float64()
    if all(isinstance(value, str) for value in non_null):
        return pa.string()
    return pa.string()


def _coerce_arrow_value(value: Any, pa_type: pa.DataType) -> Any:
    if value is None:
        return None
    if pa.types.is_string(pa_type) and not isinstance(value, str):
        return json.dumps(value, sort_keys=True, default=_json_default)
    return value


def _decimal_integral_digits(value: Decimal) -> int:
    sign, digits, raw_exponent = value.as_tuple()
    _ = sign
    if not isinstance(raw_exponent, int):
        raise StorageError("non-finite decimals cannot be written to parquet")
    if not digits:
        return 1
    return max(len(digits) + raw_exponent, 0)


def _decimal_scale(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise StorageError("non-finite decimals cannot be written to parquet")
    return max(-exponent, 0)


__all__ = [
    "JsonRow",
    "QueueStats",
    "Store",
    "StorageError",
]
