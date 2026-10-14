"""L04/L05 finite writer acknowledgements and injected storage failures."""

from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

import pytest

from btc_microstructure.bounded_replay import ReadOnlyStore
from btc_microstructure.bounded_writer import Budget, MIB, PARTITIONS, ProcessWriter, WriterFailure


def row(sequence: int) -> dict[str, Any]:
    return {
        "kind": "SESSION_OPEN",
        "session_id": "TEST_ONLY",
        "ingest_sequence": sequence,
        "ts_receive_ns": sequence,
        "receive_monotonic_ns": sequence,
    }


def test_acknowledged_cursor_and_finite_close(tmp_path: Path) -> None:
    writer = ProcessWriter(tmp_path / "run")
    writer.append(row(1))
    assert writer.cursor is None
    writer.flush()
    assert writer.cursor == 1
    writer.close()
    assert writer.stop_status == "CLOSED_ACKNOWLEDGED"
    assert not writer.process.is_alive()
    assert ReadOnlyStore(writer.root).read_rows() == [row(1)]


@pytest.mark.parametrize("stage", ["append", "flush", "fsync", "rename", "manifest"])
def test_fault_retains_last_acknowledged_trusted_prefix(tmp_path: Path, stage: str) -> None:
    writer = ProcessWriter(tmp_path / "run")
    writer.append_durable(row(1))
    writer.fault = stage
    writer.append(row(2))
    with pytest.raises(WriterFailure, match="UNCERTAIN_GAP"):
        writer.flush()
    assert writer.cursor == 1
    assert writer.failed
    assert not writer.process.is_alive()
    source = ReadOnlyStore(writer.root)
    assert source.cursor == 1
    assert source.read_rows() == [row(1)]
    with pytest.raises(WriterFailure, match="BLOCKED"):
        writer.close()


def test_hung_writer_timeout_is_followed_by_join_not_thread_cancellation(tmp_path: Path) -> None:
    writer = ProcessWriter(tmp_path / "run")
    writer.append_durable(row(1))
    writer.fault = "hang"
    writer.ack_seconds = 0.05
    started = time.monotonic()
    with pytest.raises(WriterFailure, match="WRITER_ACK_TIMEOUT"):
        writer.append_durable(row(2))
    assert time.monotonic() - started < 2
    assert writer.cursor == 1
    assert writer.stop_status == "STOPPED_UNCERTAIN"
    assert not writer.process.is_alive()


def test_unconfirmed_process_stop_is_explicit_stop_failed() -> None:
    writer = ProcessWriter.__new__(ProcessWriter)
    calls = []
    writer.process = SimpleNamespace(
        join=lambda timeout: calls.append(("join", timeout)),
        is_alive=lambda: True,
        terminate=lambda: calls.append(("terminate", 0)),
        kill=lambda: calls.append(("kill", 0)),
    )
    writer.connection = SimpleNamespace(close=lambda: None)
    writer.send_connection = SimpleNamespace(close=lambda: None)
    writer._stop()
    assert writer.stop_status == "STOP_FAILED"
    assert calls == [("join", 0.05), ("terminate", 0), ("join", 0.2), ("kill", 0), ("join", 0.2)]


def test_budget_counts_temporary_and_committed_coexistence_and_boundaries() -> None:
    budget = Budget(total=100, free=20)
    budget.reserve({"ws_raw_envelopes": 50, "temporary_orphan_quarantine_files": 50}, 120)
    assert sum(budget.used.values()) == 100
    with pytest.raises(WriterFailure, match="RUN_BYTE_LIMIT"):
        budget.reserve({"control": 1}, 120)
    budget.release({"temporary_orphan_quarantine_files": 50})
    with pytest.raises(WriterFailure, match="DISK_FREE_LIMIT"):
        budget.reserve({"control": 1}, 20)
    budget.reserve({"control": 1}, 21)
    assert sum(budget.used.values()) == 51
    for partition, limit in PARTITIONS.items():
        quota = Budget(total=1024 * MIB)
        quota.reserve({partition: limit}, 2048 * MIB)
        with pytest.raises(WriterFailure, match="PARTITION_LIMIT"):
            quota.reserve({partition: 1}, 2048 * MIB)


def test_snapshot_reserves_before_any_file_write(tmp_path: Path) -> None:
    writer = ProcessWriter(tmp_path / "run", max_run_bytes=10)
    with pytest.raises(WriterFailure, match="RUN_BYTE_LIMIT"):
        writer.write_snapshot("snapshots/context.json", b"123456")
    assert not (writer.root / "snapshots" / "context.json").exists()
    assert not writer.process.is_alive()


def test_queue_limits_do_not_replace_old_input(tmp_path: Path) -> None:
    writer = ProcessWriter(tmp_path / "run", max_records=1)
    writer.append(row(1))
    with pytest.raises(WriterFailure, match="QUEUE_LIMIT"):
        writer.append(row(2))
    assert writer.rows == [row(1)]
    writer.close()
    assert writer.cursor == 1


def test_worker_rss_startup_failure_is_joined(tmp_path: Path) -> None:
    writer = ProcessWriter.__new__(ProcessWriter)
    with pytest.raises(WriterFailure, match="WORKER_RSS_LIMIT"):
        writer.__init__(tmp_path / "run", rss_stop_bytes=1)
    assert not writer.process.is_alive()
    assert writer.stop_status == "STOPPED_UNCERTAIN"


def test_writer_rejects_existing_run_without_recovery_mutation(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    source = root / "historical-bytes"
    source.write_bytes(b"do not recover or change")
    with pytest.raises(WriterFailure, match="RUN_DIRECTORY_ALREADY_EXISTS"):
        ProcessWriter(root)
    assert source.read_bytes() == b"do not recover or change"
    assert sorted(path.name for path in root.iterdir()) == ["historical-bytes"]
