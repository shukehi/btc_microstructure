"""Fault and replay contracts for the isolated TEST_ONLY journal prototype."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from btc_microstructure import bounded_live, journal_prototype
from btc_microstructure.bounded_live import run_test_only_bounded_live
from btc_microstructure.bounded_transport import WsEvent
from btc_microstructure.bounded_writer import WriterFailure
from btc_microstructure.journal_prototype import (
    FRAME_HEADER,
    TestOnlyJournalProcessWriter,
    recover_test_only_journal,
    replay_test_only_journal,
    replay_test_only_storage,
)
from btc_microstructure.offline_core import NS

from bounded_fakes import FakeClock, FakeTransport, agg_trade
from test_bounded_live import bounded_config, tree_digest


def _control(sequence: int, kind: str = "SUBSCRIBED") -> dict[str, object]:
    return {
        "kind": kind,
        "session_id": "TEST_ONLY",
        "ingest_sequence": sequence,
        "ts_receive_ns": sequence,
        "receive_monotonic_ns": sequence,
        "quality_flags": [],
    }


def _closed_control_journal(root: Path, records: int = 3) -> Path:
    writer = TestOnlyJournalProcessWriter(
        root,
        min_free_bytes=0,
        audit_interval=1,
    )
    for sequence in range(1, records + 1):
        writer.append_durable(_control(sequence))
    writer.close()
    return root / "journal" / "control" / "control-000001.jnl"


def test_controller_ordering_two_replays_and_independent_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bounded_live, "ProcessWriter", TestOnlyJournalProcessWriter)
    clock = FakeClock()
    start = clock.wall_ns()
    transport = FakeTransport(
        clock,
        [
            (
                start + 100_000_000,
                WsEvent("message", agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False)),
            ),
            (
                start + 200_000_000,
                WsEvent("message", agg_trade(agg_id=2, quantity="0.20", buyer_is_maker=False)),
            ),
            (
                start + 300_000_000,
                WsEvent("message", agg_trade(agg_id=3, quantity="0.10", buyer_is_maker=True)),
            ),
            (start + 2 * NS, WsEvent("disconnect")),
        ],
    )
    config = bounded_config(tmp_path, observation_seconds=2, window_seconds=[1])

    result = asyncio.run(run_test_only_bounded_live(config, transport=transport, clock=clock))

    assert result["messages"] == 3
    assert result["normalized_rows"] == 3
    assert result["feature_rows"] == 1
    before = tree_digest(config.output_root)
    first = replay_test_only_journal(config.output_root)
    second = replay_test_only_journal(config.output_root)
    assert first == second
    assert first["status"] == "PASS"
    assert first["input_unchanged"] is True
    assert first["replay_matches_original"] is True
    assert tree_digest(config.output_root) == before
    feature = first["features"][0]
    assert str(feature["buy_volume_base"]) == "0.50"
    assert str(feature["sell_volume_base"]) == "0.10"
    assert str(feature["volume_base"]) == "0.60"
    assert str(feature["trade_delta_base"]) == "0.40"
    cutoff = next(row for row in first["rows"] if row["kind"] == "FEATURE_CUTOFF")
    published = next(row for row in first["rows"] if row["kind"] == "FEATURE_PUBLISHED")
    control_frames = first["frame_details"]["control"]
    cutoff_batch = next(
        item["batch_id"]
        for item in control_frames
        if item["first_sequence"] <= cutoff["ingest_sequence"] <= item["last_sequence"]
    )
    published_batch = next(
        item["batch_id"]
        for item in control_frames
        if item["first_sequence"] <= published["ingest_sequence"] <= item["last_sequence"]
    )
    pending_feature_batch = first["frame_details"]["feature"][0]["batch_id"]
    assert cutoff_batch < pending_feature_batch < published_batch
    assert result["writer_metrics"]["journal_files"] < 20


def test_interrupted_append_keeps_only_acknowledged_prefix(tmp_path: Path) -> None:
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(
        root,
        min_free_bytes=0,
        audit_interval=32,
        fault_operation="batch",
        fault_after=2,
        fault_stage="after_payload_fsync",
    )
    writer.append_durable(_control(1))
    with pytest.raises(WriterFailure, match="injected batch:after_payload_fsync"):
        writer.append_durable(_control(2))
    with pytest.raises(WriterFailure, match="STOPPED_UNCERTAIN"):
        writer.close()

    before = tree_digest(root)
    recovered = recover_test_only_journal(root)
    assert recovered["status"] == "PARTIAL"
    assert recovered["trusted_cursor"] == 1
    assert [row["ingest_sequence"] for row in recovered["rows"]] == [1]
    assert any(
        event["event_type"] == "JOURNAL_FRAME_TRUNCATED" for event in recovered["recovery_events"]
    )
    assert recovered["input_unchanged"] is True
    assert tree_digest(root) == before
    receipt = (root / "errors" / "storage_events.jsonl").read_text(encoding="utf-8")
    assert "JOURNAL_WRITER_FAILURE" in receipt
    assert '"trusted_cursor":1' in receipt


def test_truncated_committed_record_keeps_previous_frame(tmp_path: Path) -> None:
    root = tmp_path / "run"
    journal = _closed_control_journal(root)
    with journal.open("r+b") as handle:
        handle.truncate(journal.stat().st_size - 11)

    recovered = recover_test_only_journal(root)

    assert recovered["status"] == "PARTIAL"
    assert recovered["trusted_cursor"] == 2
    assert [row["ingest_sequence"] for row in recovered["rows"]] == [1, 2]
    assert any(
        event["event_type"] == "JOURNAL_FRAME_TRUNCATED" for event in recovered["recovery_events"]
    )


def test_same_length_checksum_failure_stops_at_previous_frame(tmp_path: Path) -> None:
    root = tmp_path / "run"
    journal = _closed_control_journal(root)
    initial = recover_test_only_journal(root)
    second = initial["frame_details"]["control"][1]
    with journal.open("r+b") as handle:
        handle.seek(second["start_offset"] + FRAME_HEADER.size + 5)
        original = handle.read(1)
        handle.seek(-1, 1)
        handle.write(b"x" if original != b"x" else b"y")
        handle.flush()

    recovered = recover_test_only_journal(root)

    assert recovered["status"] == "PARTIAL"
    assert recovered["trusted_cursor"] == 1
    assert any(
        event["event_type"] == "JOURNAL_CHECKSUM_FAILURE" for event in recovered["recovery_events"]
    )


def test_torn_checkpoint_falls_back_without_editing_input(tmp_path: Path) -> None:
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(
        root,
        min_free_bytes=0,
        audit_interval=32,
        fault_operation="checkpoint",
        fault_after=2,
        fault_stage="after_temp_fsync",
    )
    writer.append_durable(_control(1))
    assert writer.checkpoint()["generation"] == 1
    with pytest.raises(WriterFailure, match="injected checkpoint:after_temp_fsync"):
        writer.checkpoint()
    with pytest.raises(WriterFailure, match="STOPPED_UNCERTAIN"):
        writer.close()
    before = tree_digest(root)

    recovered = recover_test_only_journal(root)

    assert recovered["status"] == "PARTIAL"
    assert recovered["trusted_cursor"] == 1
    assert recovered["checkpoint"]["generation"] == 1
    assert any(event["event_type"] == "TORN_CHECKPOINT" for event in recovered["recovery_events"])
    assert recovered["input_unchanged"] is True
    assert tree_digest(root) == before


def test_recovery_failure_on_external_symlink_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _closed_control_journal(root, records=1)
    (root / "journal" / "raw" / "escape").symlink_to(tmp_path)
    before = tree_digest(root)

    recovered = recover_test_only_journal(root)

    assert recovered["status"] == "BLOCKED"
    assert recovered["trusted_cursor"] is None
    assert recovered["recovery_events"][0]["event_type"] == "RECOVERY_FAILED"
    assert recovered["input_unchanged"] is True
    assert tree_digest(root) == before


def test_same_length_external_mutation_blocks_next_append(tmp_path: Path) -> None:
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(root, min_free_bytes=0, audit_interval=32)
    writer.append_durable(_control(1))
    journal = root / "journal" / "control" / "control-000001.jnl"
    with journal.open("r+b") as handle:
        handle.seek(FRAME_HEADER.size + 20)
        original = handle.read(1)
        handle.seek(-1, 1)
        handle.write(b"x" if original != b"x" else b"y")
        handle.flush()

    with pytest.raises(WriterFailure, match="JOURNAL_EXTERNAL_MUTATION"):
        writer.append_durable(_control(2))
    with pytest.raises(WriterFailure, match="STOPPED_UNCERTAIN"):
        writer.close()


def test_checkpoint_full_audit_blocks_new_orphan(tmp_path: Path) -> None:
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(root, min_free_bytes=0, audit_interval=32)
    writer.append_durable(_control(1))
    (root / "unexpected.bin").write_bytes(b"orphan")

    with pytest.raises(WriterFailure, match="JOURNAL_AUDIT_ORPHAN"):
        writer.checkpoint()
    with pytest.raises(WriterFailure, match="STOPPED_UNCERTAIN"):
        writer.close()


def test_multi_frame_table_requires_complete_chunk_group(tmp_path: Path) -> None:
    root = tmp_path / "run"
    rows = [{"index": index, "value": "x" * 4000} for index in range(600)]
    writer = TestOnlyJournalProcessWriter(root, min_free_bytes=0, audit_interval=32)
    receipt = writer.write_table("features", rows)
    assert receipt["manifest"]["chunk_count"] >= 2
    writer.close()
    complete = recover_test_only_journal(root)
    assert complete["status"] == "PASS"
    assert complete["feature_tables"]["features"] == rows
    journal = root / "journal" / "feature" / "feature-000001.jnl"
    with journal.open("r+b") as handle:
        handle.truncate(journal.stat().st_size - 9)

    partial = recover_test_only_journal(root)

    assert partial["status"] == "PARTIAL"
    assert "features" not in partial["feature_tables"]
    assert any(
        event["event_type"] == "FEATURE_TABLE_INCOMPLETE" for event in partial["recovery_events"]
    )


def test_dispatcher_preserves_legacy_reader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = {"status": "LEGACY"}
    monkeypatch.setattr(journal_prototype, "replay_bounded", lambda root: expected)

    assert replay_test_only_storage(tmp_path) is expected
