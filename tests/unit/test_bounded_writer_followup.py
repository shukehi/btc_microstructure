from __future__ import annotations

import hashlib
import inspect
import json
import time
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from btc_microstructure import bounded_writer
from btc_microstructure.bounded_writer import Budget, MIB, PARTITIONS, ProcessWriter, WriterFailure


GIB = 1024 * MIB


def _control_row(sequence: int) -> dict[str, Any]:
    return {
        "kind": "SESSION_OPEN",
        "session_id": "TEST_ONLY",
        "ingest_sequence": sequence,
        "ts_receive_ns": sequence,
        "receive_monotonic_ns": sequence,
    }


def _raw_row(sequence: int) -> dict[str, Any]:
    row = _control_row(sequence)
    row["kind"] = "RAW"
    row["payload_base64"] = "e30="
    row["raw_payload_bytes"] = 2
    return row


def _write(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def _write_manifest(root: Path, name: str, payload: dict[str, Any]) -> int:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    return _write(root / "manifests" / name, encoded)


def _manifest_for(
    path: str, data: bytes, *, kind: str, cursor: dict[str, int | None]
) -> dict[str, Any]:
    return {
        "schema_version": "offline-storage-manifest-1",
        "kind": kind,
        "path": path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "row_count": 1,
        "cursor": cursor,
        "created_ns": 1,
    }


def test_default_writer_requires_r203_stop_free_floor_before_startup() -> None:
    signature = inspect.signature(ProcessWriter)

    assert signature.parameters["min_free_bytes"].default == 2 * GIB


def test_regular_diagnostics_cannot_consume_terminal_audit_reserve() -> None:
    budget = Budget(
        total=sum(PARTITIONS.values()),
        free=0,
        terminal_audit_reserve_bytes=MIB,
    )
    budget.reserve(
        {"diagnostics_and_receipts": PARTITIONS["diagnostics_and_receipts"] - MIB},
        10 * GIB,
    )

    with pytest.raises(WriterFailure, match="TERMINAL_AUDIT_RESERVE"):
        budget.reserve({"diagnostics_and_receipts": 1}, 10 * GIB)


def test_snapshot_rejects_symlink_escape_before_temp_file_write(tmp_path: Path) -> None:
    writer = ProcessWriter(tmp_path / "run")
    outside = tmp_path / "outside"
    outside.mkdir()
    (writer.root / "snapshots").symlink_to(outside, target_is_directory=True)

    try:
        with pytest.raises(WriterFailure, match="SYMLINK|ESCAPE|SNAPSHOT"):
            writer.write_snapshot("snapshots/context.json", b"must remain inside run root")
        assert not (outside / "context.json").exists()
        assert not (outside / "context.json.tmp").exists()
    finally:
        if not writer.closed and not writer.failed:
            writer.close()


def test_table_reserves_budget_before_arrow_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    original_reserve = bounded_writer.Budget.reserve

    def reserve(self: Budget, charges: dict[str, int], disk_free: int) -> None:
        calls.append("reserve")
        return original_reserve(self, charges, disk_free)

    def arrow_table(_: list[dict[str, Any]]) -> object:
        calls.append("arrow_table")
        return SimpleNamespace()

    monkeypatch.setattr(bounded_writer.Budget, "reserve", reserve)
    monkeypatch.setattr(bounded_writer, "_arrow_table", arrow_table)

    class FakeConnection:
        def __init__(self) -> None:
            self.requests = [
                json.dumps(
                    {
                        "operation": "table",
                        "name": "normalized",
                        "rows": [_control_row(1)],
                    }
                ).encode()
            ]

        def recv_bytes(self) -> bytes:
            return self.requests.pop(0)

        def close(self) -> None:
            return None

    class FakeAck:
        def __init__(self) -> None:
            self.replies: list[bytes] = []

        def send_bytes(self, data: bytes) -> None:
            self.replies.append(data)

        def close(self) -> None:
            return None

    context = bounded_writer.mp.get_context("spawn")
    bounded_writer._worker(
        FakeConnection(),
        FakeAck(),
        str(tmp_path / "run"),
        {},
        512 * MIB,
        0,
        None,
        512 * MIB,
        context.Value("Q", 0),
        context.Value("Q", 0),
        context.Value("b", 0),
    )

    assert calls[:2] == ["reserve", "reserve"]
    assert calls.index("reserve") < calls.index("arrow_table")


def test_parent_samples_worker_rss_while_command_send_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ProcessWriter.__new__(ProcessWriter)
    writer.ack_seconds = 0.5
    writer.deadline = None
    writer.cursor = None
    writer.budget_used = {}
    writer.worker_rss_peak = 0
    writer.rss_samples = 0
    writer.connection = SimpleNamespace(fileno=lambda: 11)
    writer.send_connection = SimpleNamespace(fileno=lambda: 12)
    writer._worker_rss_sample_count = SimpleNamespace(value=0, get_lock=lambda: nullcontext())

    samples: list[str] = []
    writer._sample_worker_rss = lambda: samples.append("sample")  # type: ignore[method-assign]

    payload = json.dumps({"ok": True, "cursor": 7}).encode()
    framed = len(payload).to_bytes(4, "big") + payload
    writes = {"count": 0}
    reads = {"sent": False}

    def fake_select(read: Any, write: Any, error: Any, timeout: float) -> tuple[Any, Any, Any]:
        if write:
            return ([], write, [])
        return (read, [], [])

    def fake_write(fd: int, data: bytes) -> int:
        writes["count"] += 1
        if writes["count"] <= 3:
            raise BlockingIOError
        return len(data)

    def fake_read(fd: int, size: int) -> bytes:
        if reads["sent"]:
            return b""
        reads["sent"] = True
        return framed

    monkeypatch.setattr(bounded_writer.select, "select", fake_select)
    monkeypatch.setattr(bounded_writer.os, "write", fake_write)
    monkeypatch.setattr(bounded_writer.os, "read", fake_read)

    reply = writer._exchange({"operation": "batch", "rows": [_control_row(7)]})

    assert reply["cursor"] == 7
    assert writes["count"] == 4
    assert len(samples) >= 5


def test_physical_audit_charges_every_file_class_and_old_new_temp(tmp_path: Path) -> None:
    root = tmp_path / "run"
    expected = dict.fromkeys(PARTITIONS, 0)
    expected["diagnostics_and_receipts"] += _write(root / "writer-open", b"open\n")
    expected["diagnostics_and_receipts"] += _write(
        root / "errors" / "storage_events.jsonl", b'{"event_type":"x"}\n'
    )
    expected["rest_metadata"] += _write(root / "snapshots" / "run-context.json", b"{}")
    control_bytes = (
        json.dumps(_control_row(1), sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    raw_bytes = json.dumps(_raw_row(2), sort_keys=True, separators=(",", ":")).encode() + b"\n"
    expected["control"] += _write(root / "raw" / "raw-control-000001.jsonl", control_bytes)
    expected["ws_raw_envelopes"] += _write(root / "raw" / "raw-control-000002.jsonl", raw_bytes)
    expected["manifests_indexes"] += _write_manifest(
        root,
        "manifest-raw-000001.json",
        _manifest_for(
            "raw/raw-control-000001.jsonl",
            control_bytes,
            kind="raw_control_segment",
            cursor={"before": None, "after": 1},
        ),
    )
    expected["manifests_indexes"] += _write_manifest(
        root,
        "manifest-raw-000002.json",
        _manifest_for(
            "raw/raw-control-000002.jsonl",
            raw_bytes,
            kind="raw_control_segment",
            cursor={"before": 1, "after": 2},
        ),
    )
    normalized_old = b"normalized-old"
    normalized_new = b"normalized-new"
    features = b"features"
    expected["normalized_and_quarantine"] += _write(
        root / "tables" / "normalized" / "normalized-000001.parquet", normalized_old
    )
    expected["normalized_and_quarantine"] += _write(
        root / "tables" / "normalized" / "normalized-000002.parquet", normalized_new
    )
    expected["feature_parquet"] += _write(
        root / "tables" / "features" / "features-000001.parquet", features
    )
    expected["manifests_indexes"] += _write_manifest(
        root,
        "manifest-table-normalized-000001.json",
        _manifest_for(
            "tables/normalized/normalized-000001.parquet",
            normalized_old,
            kind="derived_table",
            cursor={"before": 2, "after": 2},
        ),
    )
    expected["manifests_indexes"] += _write_manifest(
        root,
        "manifest-table-normalized-000002.json",
        _manifest_for(
            "tables/normalized/normalized-000002.parquet",
            normalized_new,
            kind="derived_table",
            cursor={"before": 2, "after": 2},
        ),
    )
    expected["manifests_indexes"] += _write_manifest(
        root,
        "manifest-table-features-000001.json",
        _manifest_for(
            "tables/features/features-000001.parquet",
            features,
            kind="derived_table",
            cursor={"before": 2, "after": 2},
        ),
    )
    expected["temporary_orphan_quarantine_files"] += _write(
        root / "raw" / "raw-control-000003.jsonl.tmp", b"partial"
    )
    expected["temporary_orphan_quarantine_files"] += _write(
        root / "quarantine" / "orphan.bin", b"orphan"
    )
    expected["temporary_orphan_quarantine_files"] += _write(
        root / "misc" / "unknown.tmp", b"unknown"
    )

    audit = bounded_writer._audit_physical_root(root)  # noqa: SLF001

    assert audit.files == 16
    assert audit.bytes == sum(expected.values())
    assert audit.charges == expected


def test_physical_audit_rejects_symlink_before_charging_bytes(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WriterFailure, match="PHYSICAL_AUDIT_SYMLINK"):
        bounded_writer._audit_physical_root(root)  # noqa: SLF001


def test_terminal_audit_reserve_allows_only_256_bounded_records() -> None:
    budget = Budget(
        total=sum(PARTITIONS.values()),
        free=0,
        terminal_audit_reserve_bytes=MIB,
    )
    budget.reserve(
        {"diagnostics_and_receipts": PARTITIONS["diagnostics_and_receipts"] - MIB},
        10 * GIB,
    )

    for _ in range(bounded_writer.MAX_TERMINAL_AUDIT_RECORDS):
        budget.reserve_terminal_audit(bounded_writer.TERMINAL_AUDIT_RECORD_BYTES, 10 * GIB)

    assert budget.terminal_audit_records == 256
    assert budget.terminal_audit_bytes == MIB
    with pytest.raises(WriterFailure, match="TERMINAL_AUDIT_RECORDS|TERMINAL_AUDIT_RESERVE"):
        budget.reserve_terminal_audit(1, 10 * GIB)
    with pytest.raises(WriterFailure, match="TERMINAL_AUDIT_RECORD_LIMIT"):
        Budget(terminal_audit_reserve_bytes=MIB).reserve_terminal_audit(
            bounded_writer.TERMINAL_AUDIT_RECORD_BYTES + 1,
            10 * GIB,
        )


def test_budget_distinguishes_run_cap_from_partition_cap_with_terminal_reserve() -> None:
    run_budget = Budget(total=100, terminal_audit_reserve_bytes=10)
    run_budget.reserve({"control": 90}, 10 * GIB)
    with pytest.raises(WriterFailure, match="RUN_BYTE_LIMIT"):
        run_budget.reserve({"ws_raw_envelopes": 1}, 10 * GIB)

    partition_budget = Budget(total=1024 * MIB, terminal_audit_reserve_bytes=MIB)
    partition_budget.reserve({"control": PARTITIONS["control"]}, 10 * GIB)
    with pytest.raises(WriterFailure, match="PARTITION_LIMIT:control"):
        partition_budget.reserve({"control": 1}, 10 * GIB)


def test_worker_reports_storage_warning_after_free_space_drops_below_warn_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    high = bounded_writer.STARTUP_FREE_BYTES
    low = bounded_writer.STOP_FREE_BYTES + MIB
    observed_free: list[int] = []

    def disk_usage(_: object) -> SimpleNamespace:
        free = high if len(observed_free) < 3 else low
        observed_free.append(free)
        return SimpleNamespace(free=free)

    class FakeConnection:
        def __init__(self) -> None:
            self.requests = [json.dumps({"operation": "close"}).encode()]

        def recv_bytes(self) -> bytes:
            return self.requests.pop(0)

        def close(self) -> None:
            return None

    class FakeAck:
        def __init__(self) -> None:
            self.replies: list[bytes] = []

        def send_bytes(self, data: bytes) -> None:
            self.replies.append(data)

        def close(self) -> None:
            return None

    monkeypatch.setattr(bounded_writer.shutil, "disk_usage", disk_usage)
    context = bounded_writer.mp.get_context("spawn")
    ack = FakeAck()

    bounded_writer._worker(
        FakeConnection(),
        ack,
        str(tmp_path / "run"),
        {},
        512 * MIB,
        bounded_writer.STOP_FREE_BYTES,
        None,
        512 * MIB,
        context.RawValue("Q", 0),
        context.RawValue("Q", 0),
        context.RawValue("b", 0),
    )

    close_reply = json.loads(ack.replies[-1])
    metrics = close_reply["metrics"]
    assert metrics["storage_warning"] is True
    assert metrics["last_free_bytes"] == low


def test_parent_monitor_samples_rss_while_worker_is_hung(tmp_path: Path) -> None:
    writer = ProcessWriter(tmp_path / "run", min_free_bytes=0, ack_seconds=0.45)
    writer.append_durable(_control_row(1))
    before = writer.metrics["rss_samples"]
    writer.fault = "hang"
    writer.append(_control_row(2))

    with pytest.raises(WriterFailure, match="WRITER_ACK_TIMEOUT"):
        writer.flush()

    metrics = writer.metrics
    assert metrics["rss_samples"] > before
    assert metrics["rss_sample_interval_ns"] == 100_000_000
    assert 0 < metrics["rss_max_sample_gap_ns"] <= 250_000_000


def test_parent_monitor_thread_samples_while_main_thread_is_idle(tmp_path: Path) -> None:
    writer = ProcessWriter(tmp_path / "run", min_free_bytes=0)
    try:
        before = writer.metrics["parent_thread_samples"]

        time.sleep(0.25)

        metrics = writer.metrics
        assert metrics["parent_thread_samples"] >= before + 2
        assert 0 < metrics["rss_max_sample_gap_ns"] <= 250_000_000
    finally:
        if not writer.closed and not writer.failed:
            writer.close()


def test_parent_monitor_thread_stops_hung_worker_when_parent_rss_exceeds_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ProcessWriter(tmp_path / "run", min_free_bytes=0, ack_seconds=0.3)
    writer.append_durable(_control_row(1))
    writer.fault = "hang"
    writer.append(_control_row(2))
    calls = {"count": 0}

    def fake_rss_bytes() -> int:
        calls["count"] += 1
        if calls["count"] >= 2:
            return writer._rss_stop_bytes + 1  # noqa: SLF001
        return 1

    monkeypatch.setattr(bounded_writer, "_rss_bytes", fake_rss_bytes)

    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and writer.process.is_alive():
            time.sleep(0.05)

        assert writer.resource_failed is True
        assert writer.resource_failure_reason == "RSS_LIMIT"
        assert not writer.process.is_alive()
        assert writer.metrics["parent_thread_samples"] >= 1
        with pytest.raises(WriterFailure, match="RSS_LIMIT"):
            writer.flush()
    finally:
        if writer.process.is_alive():
            writer._stop()  # noqa: SLF001


@pytest.mark.parametrize(
    "value",
    [
        "a" * 1000,
        "\x7f",
        'line\nquote"slash\\',
        "astral:\U0001f600",
    ],
)
def test_json_string_bound_is_tight_independent_upper_bound(value: str) -> None:
    actual = len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
    bound = bounded_writer._json_bound(value)  # noqa: SLF001

    assert actual <= bound <= actual + 2


def test_decimal_positive_exponent_is_rejected_before_json_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ProcessWriter.__new__(ProcessWriter)
    writer.ack_seconds = 0.5
    writer.deadline = None
    writer.connection = SimpleNamespace(fileno=lambda: 11)
    writer.send_connection = SimpleNamespace(fileno=lambda: 12)
    encoded = {"called": False}

    def fail_if_encoded(_: object) -> bytes:
        encoded["called"] = True
        raise AssertionError("preflight must reject before _json_line formats Decimal")

    monkeypatch.setattr(bounded_writer, "_json_line", fail_if_encoded)

    with pytest.raises(WriterFailure, match="WRITER_COMMAND_LIMIT"):
        writer._exchange(
            {
                "operation": "table",
                "name": "normalized",
                "rows": [
                    {"ingest_sequence": 1, "ts_receive_ns": 1, "huge": Decimal("1e100000000")}
                ],
            }
        )
    assert encoded["called"] is False


def test_json_bound_rejects_cycles_and_excessive_depth() -> None:
    cyclic: list[Any] = []
    cyclic.append(cyclic)
    with pytest.raises(WriterFailure, match="WRITER_COMMAND_LIMIT"):
        bounded_writer._json_bound(cyclic)  # noqa: SLF001

    too_deep: list[Any] = []
    current = too_deep
    for _ in range(bounded_writer.MAX_JSON_BOUND_DEPTH + 2):
        child: list[Any] = []
        current.append(child)
        current = child

    with pytest.raises(WriterFailure, match="WRITER_COMMAND_LIMIT"):
        bounded_writer._json_bound(too_deep)  # noqa: SLF001
