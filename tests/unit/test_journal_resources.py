"""Resource contracts with artificial readings, never real memory/disk exhaustion."""

from __future__ import annotations

from dataclasses import dataclass
import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import threading
import time
from typing import Any

import pytest

from btc_microstructure.bounded_writer import MIB, WriterFailure
from btc_microstructure.journal_prototype import (
    TestOnlyJournalProcessWriter,
    recover_test_only_journal,
)


@dataclass
class Reading:
    value: Any

    def __call__(self) -> int:
        return int(self.value.value)


@dataclass
class FailingReading:
    fail: Any

    def __call__(self) -> int:
        if self.fail.value:
            raise RuntimeError("injected monitor sampler failure")
        return 64 * MIB


@dataclass
class DropDiskAfterReads:
    remaining: Any

    def __call__(self) -> int:
        if self.remaining.value == 0:
            return 2048 * MIB - 1
        if self.remaining.value > 0:
            self.remaining.value -= 1
        return 3000 * MIB


def reading(value: int) -> Reading:
    return Reading(mp.get_context("spawn").RawValue("Q", value))


def row(sequence: int) -> dict[str, Any]:
    return {
        "kind": "SUBSCRIBED",
        "ingest_sequence": sequence,
        "ts_receive_ns": sequence,
        "receive_monotonic_ns": sequence,
        "session_id": "TEST_ONLY",
        "quality_flags": [],
    }


def wait_until(predicate: Any, seconds: float = 2) -> None:
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert predicate()


def receipts(root: Path) -> list[dict[str, Any]]:
    path = root / "errors" / "storage_events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_monitor_samples_during_worker_block(tmp_path: Path) -> None:
    writer = TestOnlyJournalProcessWriter(tmp_path / "run", min_free_bytes=0)
    try:
        writer.append_durable(row(1))
        # Idle worker is blocked waiting for IPC; main thread performs no command.
        time.sleep(0.35)
        metrics = writer.metrics
        assert metrics["parent_monitor_samples"] >= 2
        assert metrics["child_monitor_samples"] >= 2
        assert metrics["parent_monitor_max_sample_gap_ns"] > 0
        assert metrics["child_monitor_max_sample_gap_ns"] > 0
        assert metrics["rss_sample_interval_ns"] == 100_000_000
    finally:
        writer.close()


@pytest.mark.parametrize("side", ["parent", "worker"])
def test_rss_exceeded_idle_worker_stops_and_keeps_ack(tmp_path: Path, side: str) -> None:
    parent, child = reading(64 * MIB), reading(64 * MIB)
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(
        root,
        min_free_bytes=0,
        parent_rss_sampler=parent,
        worker_rss_sampler=child,
    )
    writer.append_durable(row(1))
    (parent if side == "parent" else child).value.value = 512 * MIB + 1
    try:
        wait_until(lambda: not writer.process.is_alive())
        with pytest.raises(WriterFailure, match="RSS"):
            writer.append_durable(row(2))
        assert writer.cursor == 1
        assert any("RSS" in item["error"] for item in receipts(root))
        recovered = recover_test_only_journal(root)
        assert recovered["status"] == "PARTIAL"
        assert recovered["trusted_cursor"] == 1
    finally:
        with pytest.raises(WriterFailure):
            writer.close()


def test_parent_monitor_failure_is_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(root, min_free_bytes=0)
    writer.append_durable(row(1))
    writer._monitor_stop.set()
    writer._monitor_thread.join(timeout=1)
    with pytest.raises(WriterFailure, match="MONITOR"):
        writer.append_durable(row(2))
    assert not writer.process.is_alive()
    assert writer.cursor == 1


def test_startup_disk_floor_is_enforced_by_standalone_writer(tmp_path: Path) -> None:
    with pytest.raises(WriterFailure, match="STARTUP_DISK_FREE_LIMIT"):
        TestOnlyJournalProcessWriter(
            tmp_path / "run",
            disk_free_sampler=reading(2560 * MIB - 1),
        )


def test_disk_warning_is_durable_once_and_hard_stop_keeps_prefix(tmp_path: Path) -> None:
    free = reading(3000 * MIB)
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(root, disk_free_sampler=free)
    writer.append_durable(row(1))
    free.value.value = 2304 * MIB - 1
    writer.append_durable(row(2))
    writer.append_durable(row(3))
    assert writer.metrics["storage_warning_receipt"] is True
    warnings = [item for item in receipts(root) if item["event_type"] == "DISK_FREE_WARNING"]
    assert len(warnings) == 1
    assert warnings[0]["free_bytes"] == 2304 * MIB - 1
    free.value.value = 2048 * MIB - 1
    with pytest.raises(WriterFailure, match="DISK_FREE_LIMIT"):
        writer.append_durable(row(4))
    assert writer.cursor == 3
    recovered = recover_test_only_journal(root)
    assert recovered["status"] == "PARTIAL"
    assert recovered["trusted_cursor"] == 3


def test_disk_warning_receipt_failure_stops_before_new_frame(tmp_path: Path) -> None:
    free = reading(3000 * MIB)
    writer = TestOnlyJournalProcessWriter(
        tmp_path / "run",
        disk_free_sampler=free,
        fault_operation="disk_warning",
        fault_stage="before_fsync",
    )
    writer.append_durable(row(1))
    free.value.value = 2304 * MIB - 1
    with pytest.raises(WriterFailure, match="disk_warning"):
        writer.append_durable(row(2))
    assert writer.cursor == 1
    assert not writer.process.is_alive()


@pytest.mark.parametrize("side", ["parent", "worker"])
def test_rss_monitor_interrupts_hung_command(tmp_path: Path, side: str) -> None:
    parent, child = reading(64 * MIB), reading(64 * MIB)
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(
        root,
        min_free_bytes=0,
        parent_rss_sampler=parent,
        worker_rss_sampler=child,
        fault_operation="batch",
        fault_after=2,
        fault_stage="hang",
    )
    writer.append_durable(row(1))
    errors: list[Exception] = []

    def command() -> None:
        try:
            writer.append_durable(row(2))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=command)
    thread.start()
    time.sleep(0.25)
    (parent if side == "parent" else child).value.value = 512 * MIB + 1
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors and isinstance(errors[0], WriterFailure)
    assert "RSS" in str(errors[0])
    assert not writer.process.is_alive()
    assert writer.cursor == 1
    assert any("RSS" in event["error"] for event in receipts(root))


def test_child_monitor_exit_stops_worker(tmp_path: Path) -> None:
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(root, min_free_bytes=0)
    writer.append_durable(row(1))
    writer._shared["monitor_exit"].value = 1
    wait_until(lambda: not writer.process.is_alive())
    with pytest.raises(WriterFailure, match="CHILD_MONITOR_FAILED"):
        writer.append_durable(row(2))
    assert writer.cursor == 1
    assert any("MONITOR" in item["error"] for item in receipts(root))


def test_parent_monitor_sampler_exception_stops_without_main_command(tmp_path: Path) -> None:
    fail = mp.get_context("spawn").RawValue("b", 0)
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(
        root,
        min_free_bytes=0,
        parent_rss_sampler=FailingReading(fail),
    )
    writer.append_durable(row(1))
    fail.value = 1
    wait_until(lambda: writer._resource_stopped)
    assert writer.resource_failure_reason == "PARENT_MONITOR_FAILED"
    assert not writer.process.is_alive()
    assert writer.cursor == 1
    assert any("MONITOR" in event["error"] for event in receipts(root))


def test_combined_rss_limit_with_each_process_below_cap(tmp_path: Path) -> None:
    parent, child = reading(250 * MIB), reading(250 * MIB)
    writer = TestOnlyJournalProcessWriter(
        tmp_path / "run",
        min_free_bytes=0,
        parent_rss_sampler=parent,
        worker_rss_sampler=child,
    )
    writer.append_durable(row(1))
    parent.value.value = 270 * MIB
    wait_until(lambda: writer._resource_stopped)
    assert writer.resource_failure_reason == "RSS_LIMIT"
    assert writer.cursor == 1
    assert writer.metrics["rss_warning"] is True


def test_two_gib_exactly_cannot_cover_write_reservation(tmp_path: Path) -> None:
    free = reading(2560 * MIB)
    writer = TestOnlyJournalProcessWriter(tmp_path / "run", disk_free_sampler=free)
    writer.append_durable(row(1))
    free.value.value = 2048 * MIB
    with pytest.raises(WriterFailure, match="DISK_FREE_LIMIT"):
        writer.append_durable(row(2))
    assert writer.cursor == 1
    assert not writer.process.is_alive()


def test_disk_drop_after_payload_fsync_does_not_commit_new_frame(tmp_path: Path) -> None:
    remaining = mp.get_context("spawn").RawValue("i", -1)
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(root, disk_free_sampler=DropDiskAfterReads(remaining))
    writer.append_durable(row(1))
    remaining.value = 1
    with pytest.raises(WriterFailure, match="DISK_FREE_LIMIT"):
        writer.append_durable(row(2))
    assert writer.cursor == 1
    recovered = recover_test_only_journal(root)
    assert recovered["trusted_cursor"] == 1
    assert any(
        event["event_type"] == "JOURNAL_FRAME_TRUNCATED" for event in recovered["recovery_events"]
    )


def test_noncooperative_worker_parent_limit_has_parent_receipt(tmp_path: Path) -> None:
    parent = reading(64 * MIB)
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(root, min_free_bytes=0, parent_rss_sampler=parent)
    writer.append_durable(row(1))
    os.kill(writer.process.pid, signal.SIGSTOP)
    parent.value.value = 512 * MIB + 1
    wait_until(lambda: writer._resource_stopped)
    assert not writer.process.is_alive()
    assert writer.cursor == 1
    assert writer.metrics["failure_receipt_status"] == "DURABLE_PARENT"
    event = receipts(root)[-1]
    assert event["event_type"] == "JOURNAL_PARENT_FAILURE"
    assert event["trusted_cursor"] == 1


def test_worker_exit_while_idle_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(root, min_free_bytes=0)
    writer.append_durable(row(1))
    writer.process.kill()
    wait_until(lambda: writer._resource_stopped)
    assert writer.resource_failure_reason == "JOURNAL_WORKER_EXITED"
    assert writer.cursor == 1
    assert writer.metrics["failure_receipt_status"] == "DURABLE_PARENT"


def test_disk_warning_boundary_and_failed_terminal_receipt_are_honest(tmp_path: Path) -> None:
    free = reading(2560 * MIB)
    writer = TestOnlyJournalProcessWriter(tmp_path / "run", disk_free_sampler=free)
    writer.append_durable(row(1))
    free.value.value = 2304 * MIB
    writer.append_durable(row(2))
    assert writer.metrics["storage_warning"] is False
    free.value.value = 2048 * MIB - 1
    with pytest.raises(WriterFailure, match="receipt_status=FAILED"):
        writer.append_durable(row(3))
    assert writer.cursor == 2
    assert not writer.process.is_alive()
    assert recover_test_only_journal(tmp_path / "run")["trusted_cursor"] == 2


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("rss_stop_bytes", 512 * MIB + 1),
        ("max_run_bytes", 512 * MIB + 1),
        ("max_records", 1001),
        ("max_bytes", MIB + 1),
        ("min_free_bytes", 2048 * MIB - 1),
    ],
)
def test_resource_limits_cannot_be_raised_or_disk_floor_lowered(
    tmp_path: Path,
    parameter: str,
    value: int,
) -> None:
    with pytest.raises(ValueError, match="journal prototype limits"):
        TestOnlyJournalProcessWriter(tmp_path / "run", **{parameter: value})
