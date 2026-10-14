"""Independent reliability review cases for the isolated journal prototype."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
from pathlib import Path
import signal
import time
from typing import Any

import pytest

from btc_microstructure import bounded_live, journal_prototype
from btc_microstructure.bounded_live import run_test_only_bounded_live
from btc_microstructure.bounded_writer import WriterFailure
from btc_microstructure.journal_prototype import (
    FRAME_HEADER,
    TestOnlyJournalProcessWriter,
    recover_test_only_journal,
    replay_test_only_journal,
)

from bounded_fakes import FakeClock
from test_bounded_capacity import UniformStreamingTransport
from test_bounded_live import bounded_config, tree_digest


def _control(sequence: int, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "SUBSCRIBED",
        "session_id": "TEST_ONLY",
        "ingest_sequence": sequence,
        "ts_receive_ns": sequence,
        "receive_monotonic_ns": sequence,
        "quality_flags": [],
        **extra,
    }


def _raw(sequence: int, size: int = 256) -> dict[str, Any]:
    return {
        **_control(sequence),
        "kind": "RAW",
        "payload_base64": "eA==",
        "payload_sha256": "0" * 64,
        "padding": "r" * size,
    }


def _blocked_command_helper(root_value: str, mode: str, queue: Any) -> None:
    root = Path(root_value)
    writer = TestOnlyJournalProcessWriter(root, min_free_bytes=0, ack_seconds=0.2)
    writer.append_durable(_control(1))
    worker_pid = writer.process.pid
    queue.put({"phase": "ready", "worker_pid": worker_pid})
    if mode in {"blocked_send", "no_ack"}:
        os.kill(worker_pid, signal.SIGSTOP)
    else:
        writer.process.kill()
        writer.process.join(timeout=0.5)
    began = time.perf_counter()
    try:
        if mode == "blocked_send":
            writer.write_table(
                "features",
                [{"index": index, "payload": "x" * 60_000} for index in range(18)],
            )
        else:
            writer.checkpoint()
        outcome = "NO_ERROR"
    except Exception as exc:  # noqa: BLE001 - cross-process evidence
        outcome = f"{type(exc).__name__}:{exc}"
    elapsed = time.perf_counter() - began
    alive = writer.process.is_alive()
    cursor = writer.cursor
    if alive:
        try:
            os.kill(worker_pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
        writer._stop()  # noqa: SLF001 - review fault cleanup
    queue.put(
        {
            "phase": "done",
            "outcome": outcome,
            "elapsed": elapsed,
            "worker_alive_after_command": alive,
            "cursor": cursor,
        }
    )


@pytest.mark.parametrize("mode", ["blocked_send", "no_ack", "unexpected_exit"])
def test_command_send_and_ack_share_deadline_and_stop_worker(tmp_path: Path, mode: str) -> None:
    context = mp.get_context("spawn")
    queue = context.Queue()
    process = context.Process(
        target=_blocked_command_helper,
        args=(str(tmp_path / mode), mode, queue),
    )
    process.start()
    ready = queue.get(timeout=2)
    assert ready["phase"] == "ready"
    process.join(timeout=1.5)
    if process.is_alive():
        process.terminate()
        process.join(timeout=0.5)
        try:
            os.kill(int(ready["worker_pid"]), signal.SIGKILL)
        except ProcessLookupError:
            pass
        pytest.fail(f"{mode}: command exceeded bounded deadline")
    done = queue.get(timeout=1)
    assert done["phase"] == "done"
    assert done["outcome"].startswith("WriterFailure:")
    assert done["elapsed"] < 0.8
    assert done["worker_alive_after_command"] is False
    assert done["cursor"] == 1


def test_ack_limit_cannot_be_raised_above_two_seconds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="journal prototype limits"):
        TestOnlyJournalProcessWriter(tmp_path / "run", min_free_bytes=0, ack_seconds=2.01)


def _closed_control_run(root: Path, records: int = 3, *, roll_bytes: int = 4096) -> None:
    writer = TestOnlyJournalProcessWriter(
        root,
        min_free_bytes=0,
        roll_bytes=roll_bytes,
        audit_interval=32,
    )
    for sequence in range(1, records + 1):
        writer.append_durable(_control(sequence, padding="c" * 400))
    writer.close()


def test_noncanonical_journal_name_is_rejected_and_charged_as_orphan(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _closed_control_run(root, records=1)
    invalid = root / "journal" / "raw" / "raw-1.jnl"
    invalid.write_bytes(b"untrusted journal bytes")

    recovered = recover_test_only_journal(root)

    assert recovered["status"] == "BLOCKED"
    assert (
        recovered["partition_bytes"]["temporary_orphan_quarantine_files"] == invalid.stat().st_size
    )
    assert recovered["trusted_cursor"] is None


@pytest.mark.parametrize("fault", ["missing_number", "invalid_header", "broken_chain"])
def test_number_header_and_chain_faults_never_trust_later_files(tmp_path: Path, fault: str) -> None:
    root = tmp_path / "run"
    _closed_control_run(root, records=8, roll_bytes=1024)
    files = sorted((root / "journal" / "control").glob("control-*.jnl"))
    assert len(files) >= 3
    if fault == "missing_number":
        files[1].unlink()
    elif fault == "invalid_header":
        with files[1].open("r+b") as handle:
            handle.write(b"INVALID!")
    else:
        with files[1].open("r+b") as handle:
            handle.seek(FRAME_HEADER.size + 9)
            value = handle.read(1)
            handle.seek(-1, 1)
            handle.write(b"x" if value != b"x" else b"y")

    recovered = recover_test_only_journal(root)

    assert recovered["status"] == "PARTIAL"
    assert recovered["trusted_cursor"] < 8
    assert any(
        event["event_type"]
        in {
            "JOURNAL_FILE_GAP",
            "JOURNAL_FILE_HEADER_INVALID",
            "JOURNAL_HEADER_OR_CHAIN_INVALID",
            "JOURNAL_CHECKSUM_FAILURE",
        }
        for event in recovered["recovery_events"]
    )


def _controller_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seconds: int = 5,
    writer_class: type[TestOnlyJournalProcessWriter] = TestOnlyJournalProcessWriter,
) -> Path:
    monkeypatch.setattr(bounded_live, "ProcessWriter", writer_class)
    clock = FakeClock()
    transport = UniformStreamingTransport(clock, messages=seconds * 10, rate=10)
    config = bounded_config(
        tmp_path,
        observation_seconds=seconds,
        window_seconds=[1],
    )
    result = asyncio.run(run_test_only_bounded_live(config, transport=transport, clock=clock))
    assert result["stop_reason"] == "OBSERVATION_COMPLETE"
    return config.output_root


@pytest.mark.parametrize("partition", ["raw", "control"])
def test_partial_input_hides_complete_but_untrusted_derived_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partition: str
) -> None:
    root = _controller_run(tmp_path, monkeypatch)
    complete = recover_test_only_journal(root)
    details = complete["frame_details"][partition]
    target = details[min(1, len(details) - 1)]
    journal = root / "journal" / partition / f"{partition}-{target['path_index']:06d}.jnl"
    with journal.open("r+b") as handle:
        handle.seek(target["start_offset"] + FRAME_HEADER.size + 11)
        value = handle.read(1)
        handle.seek(-1, 1)
        handle.write(b"x" if value != b"x" else b"y")
    before = tree_digest(root)

    recovered = recover_test_only_journal(root)
    replayed = replay_test_only_journal(root)

    assert recovered["status"] == "PARTIAL"
    assert recovered["feature_tables"] == {}
    assert replayed["status"] == "PARTIAL"
    assert replayed["input_unchanged"] is True
    assert all(
        feature["input_cursor"] <= recovered["trusted_cursor"] for feature in replayed["features"]
    )
    published = {
        row["cutoff_ns"]: row for row in recovered["rows"] if row["kind"] == "FEATURE_PUBLISHED"
    }
    assert all(
        feature["feature_cutoff_ns"] in published
        and feature["feature_available_ns"]
        == published[feature["feature_cutoff_ns"]]["feature_available_ns"]
        and feature["publication_status"] == "RECORDED_TEST_ONLY"
        for feature in replayed["features"]
    )
    assert tree_digest(root) == before


def test_feature_frame_must_match_trusted_cutoff_and_published_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _controller_run(tmp_path, monkeypatch)
    complete = recover_test_only_journal(root)
    first = complete["frame_details"]["feature"][0]
    journal = root / "journal" / "feature" / f"feature-{first['path_index']:06d}.jnl"
    with journal.open("r+b") as handle:
        handle.seek(first["start_offset"] + FRAME_HEADER.size + 7)
        value = handle.read(1)
        handle.seek(-1, 1)
        handle.write(b"x" if value != b"x" else b"y")

    replayed = replay_test_only_journal(root)

    assert replayed["status"] == "PARTIAL"
    assert replayed["features"] == []
    assert any(
        event["event_type"] == "FEATURE_PUBLICATION_UNVERIFIED"
        for event in replayed["recovery_events"]
    )


class SmallRollWriter(TestOnlyJournalProcessWriter):
    __test__ = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["roll_bytes"] = 32_768
        kwargs["audit_interval"] = 4
        super().__init__(*args, **kwargs)


def test_small_roll_all_partitions_contiguous_and_replay_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _controller_run(
        tmp_path,
        monkeypatch,
        seconds=30,
        writer_class=SmallRollWriter,
    )
    before = tree_digest(root)
    recovered = recover_test_only_journal(root)
    first = replay_test_only_journal(root)
    second = replay_test_only_journal(root)

    assert recovered["status"] == "PASS"
    assert recovered["trusted_cursor"] == max(row["ingest_sequence"] for row in recovered["rows"])
    for partition in ("raw", "control", "feature"):
        files = sorted((root / "journal" / partition).glob(f"{partition}-*.jnl"))
        assert len(files) >= 2
        assert [int(path.stem.rsplit("-", 1)[1]) for path in files] == list(
            range(1, len(files) + 1)
        )
        assert recovered["partition_bytes"][journal_prototype.PARTITION_BUDGET[partition]] == sum(
            path.stat().st_size for path in files
        )
    assert first == second
    assert first["status"] == "PASS"
    assert first["input_unchanged"] is True
    assert tree_digest(root) == before


def test_new_roll_file_creation_failure_keeps_old_cursor(tmp_path: Path) -> None:
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(
        root,
        min_free_bytes=0,
        roll_bytes=2048,
        fault_operation="roll",
        fault_after=2,
        fault_stage="before_create",
    )
    writer.append_durable(_control(1, padding="x" * 500))
    writer.append_durable(_control(2, padding="x" * 500))
    with pytest.raises(WriterFailure, match="injected roll:before_create"):
        writer.append_durable(_control(3, padding="x" * 500))
    assert writer.cursor == 2
    with pytest.raises(WriterFailure, match="STOPPED_UNCERTAIN"):
        writer.close()
    recovered = recover_test_only_journal(root)
    assert recovered["trusted_cursor"] == 2


def test_external_inode_replacement_blocks_append_and_recovery(tmp_path: Path) -> None:
    root = tmp_path / "run"
    writer = TestOnlyJournalProcessWriter(root, min_free_bytes=0, audit_interval=32)
    writer.append_durable(_control(1))
    journal = root / "journal" / "control" / "control-000001.jnl"
    replacement = root / "replacement.tmp"
    replacement.write_bytes(journal.read_bytes())
    os.replace(replacement, journal)

    with pytest.raises(WriterFailure, match="JOURNAL_EXTERNAL_MUTATION"):
        writer.append_durable(_control(2))
    assert writer.cursor == 1
    with pytest.raises(WriterFailure, match="STOPPED_UNCERTAIN"):
        writer.close()
    recovered = recover_test_only_journal(root)
    assert recovered["trusted_cursor"] == 1
    assert recovered["status"] == "PARTIAL"


def test_truncated_old_rolled_file_does_not_trust_newer_files(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _closed_control_run(root, records=8, roll_bytes=1024)
    files = sorted((root / "journal" / "control").glob("control-*.jnl"))
    assert len(files) >= 3
    with files[0].open("r+b") as handle:
        handle.truncate(files[0].stat().st_size - 7)
    before = tree_digest(root)

    first = recover_test_only_journal(root)
    second = recover_test_only_journal(root)

    assert first == second
    assert first["status"] == "PARTIAL"
    assert first["trusted_cursor"] is None or first["trusted_cursor"] < 8
    assert first["input_unchanged"] is True
    assert tree_digest(root) == before
