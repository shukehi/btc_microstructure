"""Explicit TEST_ONLY controller integration and deterministic Writer comparison."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import pytest
import pyarrow.parquet as pq

from btc_microstructure import bounded_live
from btc_microstructure.bounded_live import BoundedLiveConfig, run_test_only_bounded_live
from btc_microstructure.bounded_replay import ReadOnlyStore
from btc_microstructure.bounded_transport import TransportScopeError, WsEvent
from btc_microstructure.bounded_writer import ProcessWriter
from btc_microstructure.journal_prototype import (
    TestOnlyJournalProcessWriter,
    recover_test_only_journal,
    replay_test_only_storage,
)
from btc_microstructure.offline_core import NS
from btc_microstructure.offline_storage import _json_line

from bounded_fakes import FakeClock, FakeTransport, agg_trade
from test_bounded_capacity import UniformStreamingTransport
from test_bounded_live import bounded_config, tree_digest
from test_bounded_live_followup import RecordingWriter
from test_journal_resources import reading


def test_explicit_journal_selection_owns_empty_controller_directory(tmp_path: Path) -> None:
    clock = FakeClock()
    config = bounded_config(tmp_path, observation_seconds=1)
    result = asyncio.run(
        run_test_only_bounded_live(
            config,
            transport=FakeTransport(clock, []),
            clock=clock,
            test_only_writer="journal",
        )
    )
    assert result["stop_reason"] == "OBSERVATION_COMPLETE"
    assert (config.output_root / "journal-format.json").exists()
    assert (config.output_root / "writer-closed").exists()


class DeadlineRecordingWriter(RecordingWriter):
    TEST_ONLY = True

    def __init__(self) -> None:
        super().__init__()
        self.deadline: float | None = None
        self.observed: list[float | None] = []

    def append(self, row: dict[str, Any]) -> None:
        self.observed.append(self.deadline)
        super().append(row)


def test_injected_deadline_capability_receives_controller_deadline(tmp_path: Path) -> None:
    clock = FakeClock()
    writer = DeadlineRecordingWriter()
    asyncio.run(
        run_test_only_bounded_live(
            bounded_config(tmp_path, observation_seconds=1),
            transport=FakeTransport(clock, []),
            clock=clock,
            writer=writer,
        )
    )
    assert writer.observed
    assert all(value is not None for value in writer.observed)
    assert writer.deadline is not None


def test_explicit_factory_is_created_after_controller_directory(tmp_path: Path) -> None:
    clock = FakeClock()
    created: list[TestOnlyJournalProcessWriter] = []

    def factory(root: Path, **kwargs: Any) -> TestOnlyJournalProcessWriter:
        assert root.exists() and not any(root.iterdir())
        writer = TestOnlyJournalProcessWriter(root, **kwargs)
        created.append(writer)
        return writer

    factory.TEST_ONLY = True  # type: ignore[attr-defined]
    result = asyncio.run(
        run_test_only_bounded_live(
            bounded_config(tmp_path, observation_seconds=1),
            transport=FakeTransport(clock, []),
            clock=clock,
            test_only_writer_factory=factory,
        )
    )
    assert result["stop_reason"] == "OBSERVATION_COMPLETE"
    assert created[0].deadline is not None
    assert not created[0].process.is_alive()


def _input_rows(root: Path, kind: str) -> list[dict[str, Any]]:
    return (
        recover_test_only_journal(root)["rows"]
        if kind == "journal"
        else ReadOnlyStore(root).read_rows()
    )


def _logical_controls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Physical table receipts have different schemas; all economic/time fields stay exact.
    return [{key: value for key, value in row.items() if key != "feature_manifest"} for row in rows]


def _compare_runs(roots: dict[str, Path]) -> dict[str, Any]:
    replays: dict[str, dict[str, Any]] = {}
    rows: dict[str, list[dict[str, Any]]] = {}
    for kind, root in roots.items():
        before = tree_digest(root)
        first = replay_test_only_storage(root)
        second = replay_test_only_storage(root)
        assert first == second
        assert first["status"] == "PASS"
        assert first["input_unchanged"] is True
        assert first["replay_matches_original"] is True
        assert tree_digest(root) == before
        replays[kind] = first
        rows[kind] = _input_rows(root, kind)
        for published in (row for row in rows[kind] if row["kind"] == "FEATURE_PUBLISHED"):
            receipt = published["feature_manifest"]
            assert receipt
            if kind == "process":
                table_path = root / receipt["path"]
                assert hashlib.sha256(table_path.read_bytes()).hexdigest() == receipt["sha256"]
                saved = pq.read_table(table_path).to_pylist()
                assert saved and all(
                    row["feature_cutoff_ns"] == published["cutoff_ns"] for row in saved
                )
            assert published["feature_available_ns"] == published["ts_receive_ns"]
            assert published["compute_done_observed_ns"] <= published["feature_available_ns"]
    assert _logical_controls(rows["process"]) == _logical_controls(rows["journal"])
    for name in ("normalized", "quarantine", "features", "logical_sha256"):
        assert replays["process"][name] == replays["journal"][name]
    return {"rows": rows["journal"], "replay": replays["journal"]}


@pytest.mark.parametrize("seconds", [30, 60, 120])
def test_deterministic_real_writers_compare_30_60_120(tmp_path: Path, seconds: int) -> None:
    roots: dict[str, Path] = {}
    timings: dict[str, float] = {}
    for kind in ("process", "journal"):
        clock = FakeClock()
        config = bounded_config(
            tmp_path / kind,
            observation_seconds=seconds,
            window_seconds=[30],
            min_free_bytes=2560 * 1024 * 1024,
        )
        transport = UniformStreamingTransport(clock, messages=seconds * 10, rate=10)
        began = time.perf_counter()
        result = asyncio.run(
            run_test_only_bounded_live(
                config,
                transport=transport,
                clock=clock,
                test_only_writer=kind,
            )
        )
        timings[kind] = time.perf_counter() - began
        assert result["stop_reason"] == "OBSERVATION_COMPLETE"
        assert result["messages"] == seconds * 10
        assert result["feature_rows"] == seconds
        assert result["reserved_state_upper_bound_bytes"] <= 128 * 1024 * 1024
        roots[kind] = config.output_root
    compared = _compare_runs(roots)
    feature = compared["replay"]["features"][-1]
    first_window = seconds == 30
    assert feature["agg_message_count"] == (299 if first_window else 300)
    assert feature["buy_volume_base"] == Decimal("0.149" if first_window else "0.150")
    assert feature["sell_volume_base"] == Decimal("0.150")
    assert feature["volume_base"] == Decimal("0.299" if first_window else "0.300")
    assert feature["trade_delta_base"] == Decimal("-0.001" if first_window else "0.000")
    assert feature["trade_imbalance"] == (-1 / 299 if first_window else 0.0)
    assert feature["quality_flags"] == []
    print(
        "INTEGRATION_EVIDENCE "
        + json.dumps(
            {
                "seconds": seconds,
                "messages": seconds * 10,
                "features": seconds,
                "elapsed_seconds": timings,
                "files": {
                    kind: sum(1 for path in root.rglob("*") if path.is_file())
                    for kind, root in roots.items()
                },
                "logical_sha256": compared["replay"]["logical_sha256"],
                "raw_control_sha256": hashlib.sha256(
                    b"".join(_json_line(row) for row in _logical_controls(compared["rows"]))
                ).hexdigest(),
                "raw_control_equal": True,
                "derived_equal": True,
                "two_replays_each": "PASS",
                "history_available_equal": True,
                "oracle": "PASS",
            },
            sort_keys=True,
        )
    )


def test_duplicate_conflict_schema_quality_and_raw_first_match(tmp_path: Path) -> None:
    roots: dict[str, Path] = {}
    for kind in ("process", "journal"):
        clock = FakeClock()
        start = clock.wall_ns()
        messages = [
            (start + 100_000_000, agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False)),
            (start + 200_000_000, agg_trade(agg_id=2, quantity="0.20", buyer_is_maker=False)),
            (start + 300_000_000, agg_trade(agg_id=3, quantity="0.10", buyer_is_maker=True)),
            (start + NS + 200_000_000, agg_trade(agg_id=2, quantity="0.20", buyer_is_maker=False)),
            (
                start + 2 * NS + 200_000_000,
                agg_trade(agg_id=2, quantity="0.25", buyer_is_maker=False),
            ),
            (start + 3 * NS + 200_000_000, b"{invalid-json"),
        ]
        config = bounded_config(tmp_path / kind, observation_seconds=4, window_seconds=[1])
        result = asyncio.run(
            run_test_only_bounded_live(
                config,
                transport=FakeTransport(
                    clock, [(at, WsEvent("message", payload)) for at, payload in messages]
                ),
                clock=clock,
                test_only_writer=kind,
            )
        )
        assert result["stop_reason"] == "OBSERVATION_COMPLETE"
        roots[kind] = config.output_root
    compared = _compare_runs(roots)
    replay = compared["replay"]
    assert len(replay["normalized"]) == 3
    assert len(replay["quarantine"]) == 3
    assert replay["features"][0]["trade_delta_base"] == Decimal("0.40")
    assert replay["features"][0]["trade_imbalance"] == 2 / 3
    assert any(
        "DUPLICATE_CONFLICT" in row["quality_flags"] and row["volume_base"] is None
        for row in replay["features"]
    )
    assert any(
        "SOURCE_SCHEMA_CHANGED" in row["quality_flags"] and row["volume_base"] is None
        for row in replay["features"]
    )
    assert len([row for row in compared["rows"] if row["kind"] == "RAW"]) == 6


def test_default_remains_process_and_import_does_not_load_journal(tmp_path: Path) -> None:
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import btc_microstructure.bounded_live; "
            "assert 'btc_microstructure.journal_prototype' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    clock = FakeClock()
    config = bounded_config(tmp_path, observation_seconds=1)
    result = asyncio.run(
        run_test_only_bounded_live(config, transport=FakeTransport(clock, []), clock=clock)
    )
    assert result["stop_reason"] == "OBSERVATION_COMPLETE"
    assert (config.output_root / "manifests").is_dir()
    assert not (config.output_root / "journal-format.json").exists()


@pytest.mark.parametrize("kind", ["process", "journal"])
def test_existing_run_is_never_overwritten(tmp_path: Path, kind: str) -> None:
    config = bounded_config(tmp_path)
    config.output_root.mkdir()
    (config.output_root / "evidence").write_bytes(b"original")
    before = tree_digest(config.output_root)
    clock = FakeClock()
    with pytest.raises(FileExistsError):
        asyncio.run(
            run_test_only_bounded_live(
                config, clock=clock, transport=FakeTransport(clock, []), test_only_writer=kind
            )
        )
    assert tree_digest(config.output_root) == before


def test_journal_selection_cannot_enter_real_or_ordinary_config(tmp_path: Path) -> None:
    clock = FakeClock()
    config = bounded_config(tmp_path, network_authorized=True)
    transport = FakeTransport(clock, [])
    with pytest.raises(TransportScopeError, match="blocked"):
        asyncio.run(
            run_test_only_bounded_live(
                config, clock=clock, transport=transport, test_only_writer="journal"
            )
        )
    assert transport.metadata_calls == 0
    assert not config.output_root.exists()
    data = bounded_config(tmp_path).model_dump()
    data["test_only_writer"] = "journal"
    with pytest.raises(ValueError, match="Extra inputs"):
        BoundedLiveConfig(**data)


@pytest.mark.parametrize("base", [ProcessWriter, TestOnlyJournalProcessWriter])
def test_both_writers_observe_absolute_controller_deadlines(tmp_path: Path, base: Any) -> None:
    observations: list[tuple[str, float]] = []
    instances: list[Any] = []

    class ObservedWriter(base):
        TEST_ONLY = True

        def __init__(self, root: Path, **kwargs: Any) -> None:
            assert 0 < kwargs["ack_seconds"] <= 2
            super().__init__(root, **kwargs)
            instances.append(self)

        def _command(self, request: dict[str, Any]) -> dict[str, Any]:
            assert self.deadline is not None
            observations.append((request["operation"], self.deadline))
            return super()._command(request)

    clock = FakeClock()
    config = bounded_config(tmp_path, observation_seconds=1)
    before = time.monotonic()
    result = asyncio.run(
        run_test_only_bounded_live(
            config,
            transport=FakeTransport(clock, []),
            clock=clock,
            test_only_writer_factory=ObservedWriter,
        )
    )
    assert result["stop_reason"] == "OBSERVATION_COMPLETE"
    assert {operation for operation, _ in observations} >= {"snapshot", "batch", "table", "close"}
    assert observations[0][1] <= before + config.startup_seconds + 0.05
    assert all(deadline <= before + 61.05 for _, deadline in observations)
    assert not instances[0].process.is_alive()


def test_journal_parser_runs_after_trusted_batch_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = bounded_live.payload_for_core
    root = tmp_path / "run"
    observed: list[int] = []

    def parse(payload: bytes) -> Any:
        recovered = recover_test_only_journal(root)
        raw = [row for row in recovered["rows"] if row["kind"] == "RAW"]
        assert raw and recovered["physical_trusted_cursor"] >= raw[-1]["ingest_sequence"]
        observed.append(len(raw))
        return original(payload)

    monkeypatch.setattr(bounded_live, "payload_for_core", parse)
    clock = FakeClock()
    payload = agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False)
    result = asyncio.run(
        run_test_only_bounded_live(
            bounded_config(tmp_path, observation_seconds=1),
            clock=clock,
            transport=FakeTransport(
                clock, [(clock.wall_ns() + 100_000_000, WsEvent("message", payload))]
            ),
            test_only_writer="journal",
        )
    )
    assert observed == [1]
    assert result["normalized_rows"] == 1


def test_unmarked_factory_rejected_before_directory_or_metadata(tmp_path: Path) -> None:
    clock = FakeClock()
    transport = FakeTransport(clock, [])
    config = bounded_config(tmp_path)
    with pytest.raises(TransportScopeError, match="TEST_ONLY"):
        asyncio.run(
            run_test_only_bounded_live(
                config,
                clock=clock,
                transport=transport,
                test_only_writer_factory=lambda **kwargs: None,
            )
        )
    assert not config.output_root.exists()
    assert transport.metadata_calls == 0


@pytest.mark.parametrize("fault", ["timeout", "incomplete", "exit", "rss", "warning", "disk_stop"])
def test_integrated_journal_faults_are_fail_closed(tmp_path: Path, fault: str) -> None:
    clock = FakeClock()
    free, child = reading(3000 * 1024 * 1024), reading(64 * 1024 * 1024)
    instances: list[TestOnlyJournalProcessWriter] = []

    class FaultWriter(TestOnlyJournalProcessWriter):
        TEST_ONLY = True

        def __init__(self, root: Path, **kwargs: Any) -> None:
            kwargs.update(disk_free_sampler=free, worker_rss_sampler=child)
            if fault in {"timeout", "incomplete"}:
                kwargs.update(
                    fault_operation="batch",
                    fault_after=4,
                    fault_stage="hang" if fault == "timeout" else "after_payload_fsync",
                )
            super().__init__(root, **kwargs)
            self.ack_seconds = 0.3
            instances.append(self)

        def append(self, value: dict[str, Any]) -> None:
            if value["kind"] == "RAW":
                if fault == "exit":
                    self.process.kill()
                    self.process.join(timeout=0.2)
                elif fault == "rss":
                    child.value.value = 512 * 1024 * 1024 + 1
                    time.sleep(0.15)
                elif fault in {"warning", "disk_stop"}:
                    free.value.value = (2304 if fault == "warning" else 2048) * 1024 * 1024 - 1
            super().append(value)

    config = bounded_config(
        tmp_path, observation_seconds=1, window_seconds=[1], min_free_bytes=2560 * 1024 * 1024
    )
    result = asyncio.run(
        run_test_only_bounded_live(
            config,
            clock=clock,
            transport=FakeTransport(
                clock,
                [
                    (
                        clock.wall_ns() + 100_000_000,
                        WsEvent(
                            "message", agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False)
                        ),
                    )
                ],
            ),
            test_only_writer_factory=FaultWriter,
        )
    )
    assert not instances[0].process.is_alive()
    before = tree_digest(config.output_root)
    recovered = recover_test_only_journal(config.output_root)
    if fault == "warning":
        assert result["stop_reason"] == "OBSERVATION_COMPLETE"
        assert result["writer_metrics"]["storage_warning_receipt"] is True
        assert recovered["status"] == "PASS"
    else:
        assert result["status"] == "BLOCKED"
        assert result["feature_rows"] == 0
        assert recovered["status"] == "PARTIAL"
        assert instances[0].cursor == 3
        assert result["stop_reason"] != "OBSERVATION_COMPLETE"
    assert tree_digest(config.output_root) == before
