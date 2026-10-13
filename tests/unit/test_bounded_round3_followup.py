"""Regression cases for Review 5500034324 F22-02-01/F22-02-02."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

import pytest

from btc_microstructure import bounded_live, bounded_writer
from btc_microstructure.bounded_replay import ReadOnlyStore, replay_bounded
from btc_microstructure.bounded_transport import WsEvent
from btc_microstructure.bounded_writer import Budget, MIB, PARTITIONS, ProcessWriter, WriterFailure
from btc_microstructure.offline_core import NS
from btc_microstructure.offline_storage import MANIFEST_SCHEMA_VERSION

from bounded_fakes import FakeClock, FakeTransport, agg_trade
from test_bounded_live import bounded_config, run_controller


def _control(sequence: int) -> dict[str, Any]:
    return {
        "kind": "SESSION_OPEN",
        "session_id": "TEST_ONLY",
        "ingest_sequence": sequence,
        "ts_receive_ns": sequence,
        "receive_monotonic_ns": sequence,
    }


def _raw(sequence: int) -> dict[str, Any]:
    return {
        **_control(sequence),
        "kind": "RAW",
        "payload_base64": "e30=",
        "payload_sha256": hashlib.sha256(b"{}").hexdigest(),
        "payload_size": 2,
    }


class _AckTrackingWriter:
    def __init__(self) -> None:
        self.cursor: int | None = None
        self.rows: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.raw_acks = 0
        self.closed = False

    def append(self, row: dict[str, Any]) -> None:
        self.pending.append(deepcopy(row))

    def flush(self) -> dict[str, Any] | None:
        if not self.pending:
            return None
        batch = list(self.pending)
        self.rows.extend(batch)
        self.pending.clear()
        self.cursor = batch[-1]["ingest_sequence"]
        if any(row["kind"] == "RAW" for row in batch):
            self.raw_acks += 1
        return {"cursor": self.cursor}

    def append_durable(self, row: dict[str, Any]) -> None:
        self.append(row)
        self.flush()

    def write_snapshot(self, relative_path: str, payload: bytes) -> None:
        return None

    def write_table(self, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"manifest": {"path": name, "sha256": "TEST_ONLY"}}

    def close(self) -> None:
        self.flush()
        self.closed = True


def test_payload_parser_runs_only_after_raw_batch_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    writer = _AckTrackingWriter()
    observed_raw_acks: list[int] = []
    original = bounded_live.payload_for_core

    def observe(payload: bytes) -> tuple[str, list[str]]:
        observed_raw_acks.append(writer.raw_acks)
        return original(payload)

    monkeypatch.setattr(bounded_live, "payload_for_core", observe)
    result = run_controller(
        bounded_config(tmp_path, window_seconds=[1]),
        FakeTransport(
            clock,
            [
                (
                    start + 100_000_000,
                    WsEvent("message", agg_trade(agg_id=1, quantity="0.1", buyer_is_maker=False)),
                ),
                (start + NS, WsEvent("disconnect")),
            ],
        ),
        clock,
        writer,
    )

    assert result["stop_reason"] == "DISCONNECT"
    assert observed_raw_acks == [1]
    assert writer.closed


class _FailRawFlushWriter(_AckTrackingWriter):
    def flush(self) -> dict[str, Any] | None:
        if any(row["kind"] == "RAW" for row in self.pending):
            raise WriterFailure("INJECTED_RAW_FLUSH_FAILURE")
        return super().flush()

    def close(self) -> None:
        self.pending.clear()
        self.closed = True


def test_unacknowledged_raw_never_reaches_parser_or_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    parser_calls: list[bytes] = []
    processed_raw: list[int] = []
    original_parser = bounded_live.payload_for_core
    real_core = bounded_live.Core

    def parser(payload: bytes) -> tuple[str, list[str]]:
        parser_calls.append(payload)
        return original_parser(payload)

    class SpyCore(real_core):
        def process(self, row: dict[str, Any]) -> None:
            if row["kind"] == "RAW":
                processed_raw.append(row["ingest_sequence"])
            super().process(row)

    monkeypatch.setattr(bounded_live, "payload_for_core", parser)
    monkeypatch.setattr(bounded_live, "Core", SpyCore)
    writer = _FailRawFlushWriter()
    payload = agg_trade(agg_id=1, quantity="0.1", buyer_is_maker=False)
    result = run_controller(
        bounded_config(tmp_path),
        FakeTransport(clock, [(clock.wall_ns() + 100_000_000, WsEvent("message", payload))]),
        clock,
        writer,
    )

    assert result["status"] == "BLOCKED"
    assert "INJECTED_RAW_FLUSH_FAILURE" in result["stop_reason"]
    assert parser_calls == []
    assert processed_raw == []
    assert all(row["kind"] != "FEATURE_CUTOFF" for row in writer.rows)


def test_unexpected_parser_failure_occurs_after_durable_raw_and_publishes_no_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    payload = agg_trade(agg_id=7, quantity="0.1", buyer_is_maker=False)

    def fail_after_ack(_: bytes) -> tuple[str, list[str]]:
        raise RuntimeError("INJECTED_PARSER_FAILURE")

    monkeypatch.setattr(bounded_live, "payload_for_core", fail_after_ack)
    config = bounded_config(tmp_path)
    result = run_controller(
        config,
        FakeTransport(clock, [(clock.wall_ns() + 100_000_000, WsEvent("message", payload))]),
        clock,
    )

    assert result["status"] == "BLOCKED"
    assert "INJECTED_PARSER_FAILURE" in result["stop_reason"]
    rows = ReadOnlyStore(config.output_root).read_rows()
    raw = next(row for row in rows if row["kind"] == "RAW")
    assert raw["payload_base64"] == base64.b64encode(payload).decode("ascii")
    assert raw["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert not list((config.output_root / "tables" / "features").glob("*.parquet"))


@pytest.mark.parametrize("payload", [b"\xff\x80", b"{malformed"])
def test_bad_payload_is_durable_and_invalidates_published_feature_and_replay(
    tmp_path: Path, payload: bytes
) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    config = bounded_config(tmp_path, window_seconds=[1], observation_seconds=2)
    result = run_controller(
        config,
        FakeTransport(
            clock,
            [
                (start + 100_000_000, WsEvent("message", payload)),
                (
                    start + NS,
                    WsEvent(
                        "message",
                        agg_trade(agg_id=2, quantity="0.1", buyer_is_maker=False),
                    ),
                ),
                (start + 2 * NS, WsEvent("disconnect")),
            ],
        ),
        clock,
    )

    assert result["trusted_cursor"] is not None
    source = ReadOnlyStore(config.output_root)
    rows = source.read_rows()
    raw = next(row for row in rows if row["kind"] == "RAW")
    assert raw["ingest_sequence"] <= result["trusted_cursor"]
    assert base64.b64decode(raw["payload_base64"], validate=True) == payload
    assert raw["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    replay = replay_bounded(config.output_root)
    feature = next(
        row
        for row in replay["features"]
        if row["feature_cutoff_ns"] == start + NS and row["window_seconds"] == 1
    )
    assert replay["replay_matches_original"] is True
    assert "SOURCE_SCHEMA_CHANGED" in feature["quality_flags"]
    assert feature["data_valid"] is False
    assert feature["volume_base"] is None
    assert feature["trade_delta_base"] is None


def test_process_writer_flushes_on_raw_control_class_transition(tmp_path: Path) -> None:
    writer = ProcessWriter(tmp_path / "run", min_free_bytes=0)
    writer.append(_raw(1))
    assert writer.cursor is None
    writer.append(_control(2))
    assert writer.cursor == 1
    writer.flush()
    writer.close()

    manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((writer.root / "manifests").glob("manifest-raw-*.json"))
    ]
    assert [item["segment_class"] for item in manifests] == ["RAW", "CONTROL"]
    assert [item["cursor"]["after"] for item in manifests] == [1, 2]
    for manifest in manifests:
        rows = [
            json.loads(line)
            for line in (writer.root / manifest["path"]).read_text(encoding="utf-8").splitlines()
        ]
        actual = {"RAW" if row["kind"] == "RAW" else "CONTROL" for row in rows}
        assert actual == {manifest["segment_class"]}


def test_direct_worker_mixed_batch_is_rejected_before_commit(tmp_path: Path) -> None:
    class FakeConnection:
        requests = [json.dumps({"operation": "batch", "rows": [_raw(1), _control(2)]}).encode()]

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

    context = mp.get_context("spawn")
    ack = FakeAck()
    bounded_writer._worker(
        FakeConnection(),
        ack,
        str(tmp_path / "run"),
        {},
        512 * MIB,
        0,
        None,
        512 * MIB,
        context.RawValue("Q", 0),
        context.RawValue("Q", 0),
        context.RawValue("b", 0),
    )

    reply = json.loads(ack.replies[-1])
    assert reply["ok"] is False
    assert "MIXED_SEGMENT_CLASS" in reply["error"]
    assert not list((tmp_path / "run" / "manifests").glob("manifest-raw-*.json"))


@pytest.mark.parametrize(
    "partition,limit",
    [
        ("control", 16 * MIB),
        ("ws_raw_envelopes", 192 * MIB),
    ],
)
def test_raw_and_control_partition_exact_boundary_and_plus_one(partition: str, limit: int) -> None:
    assert PARTITIONS[partition] == limit
    budget = Budget(total=512 * MIB)
    budget.reserve({partition: limit}, 1024 * MIB)
    assert budget.used[partition] == limit
    with pytest.raises(WriterFailure, match=f"PARTITION_LIMIT:{partition}"):
        budget.reserve({partition: 1}, 1024 * MIB)


def test_manifest_audit_uses_homogeneous_class_and_legacy_read_only_compatibility(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    raw_dir = root / "raw"
    manifest_dir = root / "manifests"
    raw_dir.mkdir(parents=True)
    manifest_dir.mkdir()
    rows = [_control(1)]
    data = b"".join(bounded_writer._json_line(row) for row in rows)
    raw_path = raw_dir / "raw-control-000001.jsonl"
    raw_path.write_bytes(data)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "raw_control_segment",
        "segment_index": 1,
        "path": "raw/raw-control-000001.jsonl",
        "sha256": hashlib.sha256(data).hexdigest(),
        "row_count": 1,
        "cursor": {"before": None, "after": 1},
    }
    (manifest_dir / "manifest-raw-000001.json").write_text(json.dumps(manifest))

    audit = bounded_writer._audit_physical_root(root)

    assert audit.charges["control"] == len(data)
    assert audit.charges["ws_raw_envelopes"] == 0
    assert audit.charges["temporary_orphan_quarantine_files"] == 0


def test_mixed_legacy_manifest_is_untrusted_orphan_not_raw_partition(tmp_path: Path) -> None:
    root = tmp_path / "run"
    raw_dir = root / "raw"
    manifest_dir = root / "manifests"
    raw_dir.mkdir(parents=True)
    manifest_dir.mkdir()
    data = bounded_writer._json_line(_raw(1)) + bounded_writer._json_line(_control(2))
    raw_path = raw_dir / "raw-control-000001.jsonl"
    raw_path.write_bytes(data)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "raw_control_segment",
        "segment_index": 1,
        "path": "raw/raw-control-000001.jsonl",
        "sha256": hashlib.sha256(data).hexdigest(),
        "row_count": 2,
        "cursor": {"before": None, "after": 2},
    }
    (manifest_dir / "manifest-raw-000001.json").write_text(json.dumps(manifest))

    audit = bounded_writer._audit_physical_root(root)

    assert audit.charges["ws_raw_envelopes"] == 0
    assert audit.charges["control"] == 0
    assert audit.charges["temporary_orphan_quarantine_files"] == len(data)
