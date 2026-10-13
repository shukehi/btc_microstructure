"""Isolated, acknowledged R2-04 writer. Timeouts never cancel an fsync thread."""

from __future__ import annotations

import base64
from copy import deepcopy
import io
import json
import multiprocessing as mp
from multiprocessing.connection import Connection
import os
import resource
from pathlib import Path
import select
import shutil
import struct
import sys
import time
from typing import Any

import pyarrow.parquet as pq

from . import offline_storage
from .offline_storage import (
    Store,
    StorageError,
    _arrow_table,
    _fsync_dir,
    _json_line,
    _json_object_hook,
)

MIB = 1_048_576
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


class Budget:
    def __init__(self, total: int = 512 * MIB, free: int = 0) -> None:
        self.total = total
        self.free = free
        self.used = dict.fromkeys(PARTITIONS, 0)

    def reserve(self, charges: dict[str, int], disk_free: int) -> None:
        if any(value < 0 or key not in self.used for key, value in charges.items()):
            raise WriterFailure("INVALID_RESERVATION")
        required = sum(charges.values())
        if sum(self.used.values()) + required > self.total:
            raise WriterFailure("RUN_BYTE_LIMIT")
        if disk_free - required < self.free:
            raise WriterFailure("DISK_FREE_LIMIT")
        for key, amount in charges.items():
            if self.used[key] + amount > PARTITIONS[key]:
                raise WriterFailure(f"PARTITION_LIMIT:{key}")
        for key, amount in charges.items():
            self.used[key] += amount

    def release(self, charges: dict[str, int]) -> None:
        for key, amount in charges.items():
            self.used[key] -= amount


class _CappedBuffer(io.BytesIO):
    def write(self, value: Any) -> int:
        if self.tell() + len(value) > 64 * MIB:
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
) -> None:
    store: Store | None = None
    try:
        store = Store(Path(root), run_metadata=metadata)
        budget = Budget(total, free)
        original_audit = store._record_error

        def audit(event_type: str, payload: dict[str, Any]) -> None:
            if len(_json_line(payload)) > 3072:
                raise WriterFailure("AUDIT_RECORD_LIMIT")
            budget.reserve({"diagnostics_and_receipts": 4096}, shutil.disk_usage(root).free)
            original_audit(event_type, payload)

        store._record_error = audit  # type: ignore[method-assign]
        original_manifest = offline_storage._write_json_atomic

        def write_manifest(path: Path, payload: dict[str, Any]) -> None:
            encoded = json.dumps(
                payload, indent=2, sort_keys=True, default=offline_storage._json_default
            ).encode()
            if len(encoded) + 1 > 8192:
                raise WriterFailure("MANIFEST_BYTE_LIMIT")
            original_manifest(path, payload)

        offline_storage._write_json_atomic = write_manifest
        budget.reserve({"diagnostics_and_receipts": 5}, shutil.disk_usage(root).free)
        with (Path(root) / "writer-open").open("xb") as handle:
            handle.write(b"open\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(Path(root))
        manifest_count = 0

        def rss_peak() -> int:
            observed = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            value = observed if sys.platform == "darwin" else observed * 1024
            if value > rss_stop_bytes:
                raise WriterFailure("WORKER_RSS_LIMIT")
            return value

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
                inject("append")
                for row in rows:
                    store.append(row)
                size = sum(len(_json_line(row)) for row in rows)
                category = (
                    "ws_raw_envelopes" if any(r["kind"] == "RAW" for r in rows) else "control"
                )
                charges = {
                    category: size,
                    "temporary_orphan_quarantine_files": size + 8192,
                    "manifests_indexes": 8192,
                }
                budget.reserve(charges, shutil.disk_usage(root).free)
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
                manifest_count += 1
            elif operation == "table":
                if manifest_count >= 4096:
                    raise WriterFailure("MANIFEST_COUNT_LIMIT")
                table = _arrow_table(request["rows"])
                buffer = _CappedBuffer()
                pq.write_table(table, buffer, compression="zstd")
                encoded = buffer.getvalue()
                name = request["name"]
                category = (
                    "feature_parquet"
                    if name.startswith("features")
                    else "normalized_and_quarantine"
                )
                budget.reserve(
                    {
                        category: len(encoded),
                        "temporary_orphan_quarantine_files": len(encoded) + 8192,
                        "manifests_indexes": 8192,
                    },
                    shutil.disk_usage(root).free,
                )
                original_write = pq.write_table

                def write(table: Any, where: Any, **options: Any) -> None:
                    with Path(where).open("xb") as handle:
                        handle.write(encoded)

                pq.write_table = write
                try:
                    manifest = store.write_table(name, request["rows"])
                finally:
                    pq.write_table = original_write
                budget.release({"temporary_orphan_quarantine_files": len(encoded) + 8192})
                manifest_count += 1
            elif operation == "snapshot":
                relative = Path(request["path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise WriterFailure("INVALID_SNAPSHOT_PATH")
                data = base64.b64decode(request["base64"], validate=True)
                budget.reserve(
                    {"rest_metadata": len(data), "temporary_orphan_quarantine_files": len(data)},
                    shutil.disk_usage(root).free,
                )
                final = Path(root) / relative
                if final.exists():
                    raise WriterFailure("SNAPSHOT_ALREADY_EXISTS")
                final.parent.mkdir(parents=True, exist_ok=True)
                temp = final.with_suffix(final.suffix + ".tmp")
                with temp.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, final)
                _fsync_dir(final.parent)
                budget.release({"temporary_orphan_quarantine_files": len(data)})
                manifest = None
            elif operation == "close":
                store.close()
                budget.reserve({"diagnostics_and_receipts": 7}, shutil.disk_usage(root).free)
                with (Path(root) / "writer-closed").open("xb") as handle:
                    handle.write(b"closed\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                _fsync_dir(Path(root))
                acknowledgement.send_bytes(
                    json.dumps({"ok": True, "cursor": store.cursor}).encode()
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
                    }
                ).encode()
            )
    except Exception as exc:
        try:
            acknowledgement.send_bytes(
                json.dumps(
                    {
                        "ok": False,
                        "cursor": store.cursor if store else None,
                        "error": str(exc)[:256],
                    }
                ).encode()
            )
        except (OSError, EOFError):
            pass
    finally:
        connection.close()
        acknowledgement.close()


class ProcessWriter:
    def __init__(
        self,
        root: Path,
        *,
        run_metadata: dict[str, Any] | None = None,
        max_run_bytes: int = 512 * MIB,
        min_free_bytes: int = 0,
        ack_seconds: float = 2.0,
        fault: str | None = None,
        max_records: int = 1000,
        max_bytes: int = MIB,
        rss_stop_bytes: int = 512 * MIB,
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
        self.deadline: float | None = None
        self.fault = fault
        context = mp.get_context("spawn")
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
            payload = _json_line(request)
            if len(payload) > 128 * MIB:
                raise WriterFailure("WRITER_COMMAND_LIMIT")
            wire = struct.pack("!i", len(payload)) + payload
            offset = 0
            while offset < len(wire):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [send_fd], [], remaining)[1]:
                    raise WriterFailure("WRITER_ACK_TIMEOUT")
                try:
                    offset += os.write(send_fd, wire[offset:])
                except BlockingIOError:
                    continue
        received = bytearray()
        expected: int | None = None
        while expected is None or len(received) < expected + 4:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
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
            raise WriterFailure(str(reply.get("error", "WRITER_ERROR")))
        self.cursor = reply["cursor"]
        self.budget_used = reply.get("budget_used", self.budget_used)
        self.worker_rss_peak = max(self.worker_rss_peak, reply.get("worker_rss_peak", 0))
        return reply

    def _command(self, value: dict[str, Any]) -> dict[str, Any]:
        if self.failed or self.closed:
            raise WriterFailure("WRITER_NOT_OPEN")
        try:
            value["fault"] = self.fault
            return self._exchange(value)
        except Exception as exc:
            self.failed = True
            self._stop()
            raise WriterFailure(
                f"{exc}; {self.stop_status}; UNCERTAIN_GAP cursor={self.cursor}"
            ) from exc

    def append(self, row: dict[str, Any]) -> None:
        size = len(_json_line(row))
        if self.failed or self.closed:
            raise WriterFailure("WRITER_NOT_OPEN")
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
        self.process.join(timeout=0.05)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=0.2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=0.2)
        self.stop_status = "STOP_FAILED" if self.process.is_alive() else "STOPPED_UNCERTAIN"
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
        self.connection.close()
        self.send_connection.close()
