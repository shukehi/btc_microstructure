"""Fault cases for the parent review; all discovered by the existing default CI."""

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from btc_microstructure import bounded_live
from btc_microstructure.bounded_live import run_test_only_bounded_live
from btc_microstructure.bounded_replay import ReadOnlyStore, replay_bounded
from btc_microstructure.bounded_transport import TransportScopeError, WsEvent
from btc_microstructure.bounded_writer import ProcessWriter, WriterFailure
from btc_microstructure.offline_core import NS

from bounded_fakes import FakeClock, FakeTransport, agg_trade
from test_bounded_live import bounded_config, run_controller


class RecordingWriter:
    def __init__(self, clock: FakeClock, *, snapshot_failure: bool = False) -> None:
        self.clock = clock
        self.snapshot_failure = snapshot_failure
        self.cursor: int | None = None
        self.queue: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self.tables: list[tuple[str, list[dict[str, Any]], int]] = []
        self.flush_times: list[int] = []
        self.closed = False

    def append(self, row: dict[str, Any]) -> None:
        self.queue.append(deepcopy(row))

    def flush(self) -> dict[str, Any] | None:
        if self.queue:
            self.rows.extend(self.queue)
            self.cursor = self.queue[-1]["ingest_sequence"]
            self.queue.clear()
            self.flush_times.append(self.clock.monotonic_ns())
        return None

    def append_durable(self, row: dict[str, Any]) -> None:
        self.append(row)
        self.flush()

    def write_snapshot(self, relative_path: str, payload: bytes) -> None:
        if self.snapshot_failure:
            raise WriterFailure("INJECTED_SNAPSHOT_FAILURE")

    def write_table(self, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        self.clock.advance(1_000_000)
        self.tables.append((name, deepcopy(rows), self.clock.wall_ns()))
        return {"manifest": {"path": name, "sha256": "TEST_ONLY"}}

    def close(self) -> None:
        self.flush()
        self.closed = True


def with_writer(
    config: Any, transport: Any, clock: FakeClock, writer: RecordingWriter
) -> dict[str, Any]:
    return asyncio.run(
        run_test_only_bounded_live(config, transport=transport, clock=clock, writer=writer)
    )


def test_legacy_unbounded_receive_protocol_rejected(tmp_path: Path) -> None:
    transport = FakeTransport(FakeClock(), [])
    transport.poll = None  # type: ignore[assignment, method-assign]
    with pytest.raises(TransportScopeError, match="bounded poll"):
        run_controller(bounded_config(tmp_path), transport, transport.clock)
    assert transport.metadata_calls == 0


def test_silent_tick_flushes_and_publishes_on_utc_grid(tmp_path: Path) -> None:
    clock = FakeClock(wall=30 * NS + 250_000_000)
    start = clock.wall_ns()
    mono_start = clock.monotonic_ns()
    writer = RecordingWriter(clock)
    transport = FakeTransport(
        clock,
        [
            (
                start + 100_000_000,
                WsEvent("message", agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False)),
            ),
            (start + 2 * NS, WsEvent("disconnect")),
        ],
    )
    result = with_writer(bounded_config(tmp_path, window_seconds=[1]), transport, clock, writer)
    assert result["status"] == "PARTIAL"
    assert any(
        mono_start + 600_000_000 <= stamp <= mono_start + 700_000_000
        for stamp in writer.flush_times
    )
    cutoff = next(row for row in writer.rows if row["kind"] == "FEATURE_CUTOFF")
    assert cutoff["cutoff_ns"] == 31 * NS
    assert cutoff["cutoff_ns"] % NS == 0
    publish = next(row for row in writer.rows if row["kind"] == "FEATURE_PUBLISHED")
    persistence_done = next(stamp for name, _, stamp in writer.tables if name == "features_pending")
    assert publish["feature_available_ns"] == persistence_done
    assert publish["feature_available_ns"] > cutoff["cutoff_ns"]
    assert publish["feature_manifest"]["sha256"] == "TEST_ONLY"
    assert writer.closed


def test_silent_queue_age_limit_is_checked_before_flush(tmp_path: Path) -> None:
    clock = FakeClock()
    writer = RecordingWriter(clock)
    start = clock.wall_ns()
    transport = FakeTransport(
        clock,
        [(start, WsEvent("message", agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False)))],
    )
    result = with_writer(
        bounded_config(tmp_path, max_queue_age_ns=200_000_000), transport, clock, writer
    )
    assert result["stop_reason"] == "QUEUE_AGE_LIMIT"
    assert result["observation_duration_ns"] == 200_000_000
    assert writer.closed
    assert any(row["kind"] == "RAW" for row in writer.rows)


def test_snapshot_failure_closes_writer_before_session_exists(tmp_path: Path) -> None:
    clock = FakeClock()
    writer = RecordingWriter(clock, snapshot_failure=True)
    transport = FakeTransport(clock, [])
    result = with_writer(bounded_config(tmp_path), transport, clock, writer)
    assert result["status"] == "BLOCKED"
    assert "SNAPSHOT_FAILURE" in result["stop_reason"]
    assert writer.closed
    assert transport.closed
    assert transport.connect_calls == 0


@pytest.mark.parametrize("failure", ["snapshot", "connect"])
def test_real_process_writer_is_joined_on_early_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    created: list[ProcessWriter] = []

    class CapturedWriter(ProcessWriter):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

        def write_snapshot(self, path: str, data: bytes) -> None:
            if failure == "snapshot":
                raise WriterFailure("SNAPSHOT_FAILURE")
            super().write_snapshot(path, data)

    class BrokenConnect(FakeTransport):
        async def connect(self) -> None:
            if failure == "connect":
                raise OSError("CONNECT_FAILURE")
            await super().connect()

    monkeypatch.setattr(bounded_live, "ProcessWriter", CapturedWriter)
    clock = FakeClock()
    result = run_controller(bounded_config(tmp_path), BrokenConnect(clock, []), clock)
    assert result["status"] == "BLOCKED"
    assert len(created) == 1
    assert not created[0].process.is_alive()
    assert created[0].closed


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("metadata", "REST_TIME_LIMIT"),
        ("connect", "CONNECT_TIME_LIMIT"),
        ("poll", "RECEIVE_TIME_LIMIT"),
        ("send_control", "SEND_TIME_LIMIT"),
        ("close", "CLOSE_TIME_LIMIT"),
    ],
)
def test_transport_stage_waits_have_finite_timeouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, expected: str
) -> None:
    wait = asyncio.wait_for
    budgets: list[float] = []

    async def short_wait(operation: Any, timeout: float) -> Any:
        budgets.append(timeout)
        return await wait(operation, min(timeout, 0.02))

    monkeypatch.setattr(bounded_live.asyncio, "wait_for", short_wait)
    clock = FakeClock()
    start = clock.wall_ns()
    transport = FakeTransport(clock, [(start, WsEvent("ping", b"ping"))])

    async def hung(*args: Any, **kwargs: Any) -> None:
        await asyncio.Event().wait()

    setattr(transport, stage, hung)
    writer = RecordingWriter(clock)
    result = with_writer(bounded_config(tmp_path), transport, clock, writer)
    assert expected in result["stop_reason"]
    assert budgets and all(0 < value <= 15 for value in budgets)
    assert writer.closed


def test_external_cancel_runs_finite_writer_cleanup(tmp_path: Path) -> None:
    clock = FakeClock()
    writer = RecordingWriter(clock)

    class Cancelled(FakeTransport):
        async def poll(self, timeout_ns: int) -> WsEvent | None:
            raise asyncio.CancelledError

    transport = Cancelled(clock, [])
    with pytest.raises(asyncio.CancelledError):
        with_writer(bounded_config(tmp_path), transport, clock, writer)
    assert writer.closed
    assert transport.closed


def test_encoded_state_cap_stops_without_claiming_completed_observation(tmp_path: Path) -> None:
    clock = FakeClock()
    writer = RecordingWriter(clock)
    transport = FakeTransport(clock, [])
    result = with_writer(bounded_config(tmp_path, max_state_bytes=128), transport, clock, writer)
    assert result["status"] == "PARTIAL"
    assert result["stop_reason"] == "ENCODED_STATE_LIMIT"
    assert writer.closed


def test_worker_peak_is_counted_in_combined_rss(tmp_path: Path) -> None:
    clock = FakeClock()
    writer = RecordingWriter(clock)
    writer.worker_rss_peak = 536_870_912  # type: ignore[attr-defined]
    result = with_writer(bounded_config(tmp_path), FakeTransport(clock, []), clock, writer)
    assert result["stop_reason"] == "RSS_LIMIT"
    assert writer.closed


@pytest.mark.parametrize("payload", [b"\xff\x80", b"{malformed"])
def test_bad_bytes_are_preserved_and_replay_matches_same_core(
    tmp_path: Path, payload: bytes
) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    config = bounded_config(tmp_path)
    result = run_controller(
        config,
        FakeTransport(
            clock, [(start, WsEvent("message", payload)), (start + NS, WsEvent("disconnect"))]
        ),
        clock,
    )
    assert result["quarantine_rows"] == 1
    source = ReadOnlyStore(config.output_root)
    raw = next(row for row in source.read_rows() if row["kind"] == "RAW")
    import base64

    assert base64.b64decode(raw["payload_base64"]) == payload
    assert "payload" not in raw
    assert replay_bounded(config.output_root)["replay_matches_original"] is True


@pytest.mark.parametrize(
    "metadata_ns,connect_ns,startup_s,expected",
    [
        (15 * NS, 0, 30, "REST_TIME_LIMIT"),
        (0, 10 * NS, 30, "CONNECT_TIME_LIMIT"),
        (14 * NS, 6 * NS, 20, "STARTUP_TIME_LIMIT"),
        (660 * NS, 0, 30, "GLOBAL_TIME_LIMIT"),
    ],
)
def test_exact_stage_and_global_deadlines_stop(
    tmp_path: Path, metadata_ns: int, connect_ns: int, startup_s: int, expected: str
) -> None:
    from test_bounded_live import SlowStartupTransport

    clock = FakeClock()
    writer = RecordingWriter(clock)
    transport = SlowStartupTransport(
        clock, metadata_delay_ns=metadata_ns, connect_delay_ns=connect_ns
    )
    result = with_writer(
        bounded_config(tmp_path, startup_seconds=startup_s, observation_seconds=600),
        transport,
        clock,
        writer,
    )
    assert result["stop_reason"] == expected
    assert writer.closed


def test_send_and_close_share_a_five_second_cleanup_deadline(tmp_path: Path) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    writer = RecordingWriter(clock)

    class SlowClose(FakeTransport):
        async def send_control(self, kind: Any, payload: bytes = b"") -> None:
            if kind == "close":
                self.clock.advance(4 * NS)
            await super().send_control(kind, payload)

        async def close(self) -> None:
            self.clock.advance(2 * NS)
            await super().close()

    result = with_writer(
        bounded_config(tmp_path), SlowClose(clock, [(start, WsEvent("disconnect"))]), clock, writer
    )
    assert result["status"] == "BLOCKED"
    assert "CLOSE_TIME_LIMIT" in result["stop_reason"]
    assert "STOP_FAILED" in result["stop_reason"]
    assert writer.closed
