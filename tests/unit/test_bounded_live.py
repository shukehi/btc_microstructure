from __future__ import annotations

import asyncio
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from btc_microstructure.bounded_live import BoundedLiveConfig, ResourceLedger, StopRun
from btc_microstructure.bounded_replay import replay_bounded
from btc_microstructure.bounded_transport import TransportScopeError, WsEvent
from btc_microstructure.offline_core import NS
from btc_microstructure.offline_storage import Store
from btc_microstructure.unit_binding import (
    TEST_ONLY_APPROVAL_KIND,
    STREAM,
    UNIT_EVIDENCE_ID,
    load_unit_evidence,
)

from bounded_fakes import FakeClock, FakeTransport, agg_trade


EVIDENCE_PATH = Path("reports/round2/units_and_collection_plan/quantity_unit_evidence.json")


def make_test_only_approval() -> dict[str, object]:
    _, evidence_sha = load_unit_evidence(EVIDENCE_PATH.read_bytes())
    return {
        "kind": TEST_ONLY_APPROVAL_KIND,
        "approval_id": "TEST_ONLY:r2-04:unit-binding",
        "unit_evidence_id": UNIT_EVIDENCE_ID,
        "evidence_version": "1",
        "evidence_sha256": evidence_sha,
        "instrument_id": "binance:um_perpetual:BTCUSDT",
        "source_stream": "um_agg_trade",
        "stream_name": STREAM,
        "st_if_present": 1,
        "filters": {"tickSize": "0.10", "stepSize": "0.001", "minQty": "0.001"},
    }


def bounded_config(tmp_path: Path, **updates: Any) -> BoundedLiveConfig:
    value: dict[str, Any] = {
        "run_id": "r2-04-test",
        "output_root": tmp_path / "run",
        "grid_seconds": 1,
        "window_seconds": [30],
        "observation_seconds": 2,
        "min_free_bytes": 0,
        "unit_approval": make_test_only_approval(),
    }
    value.update(updates)
    return BoundedLiveConfig(**value)


def run_controller(
    config: BoundedLiveConfig, transport: Any, clock: FakeClock, writer: Any = None
) -> dict[str, Any]:
    from btc_microstructure.bounded_live import run_test_only_bounded_live

    return asyncio.run(
        run_test_only_bounded_live(config, transport=transport, clock=clock, writer=writer)
    )


def read_jsonl_rows(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_file in sorted((run_dir / "raw").glob("*.jsonl")):
        rows.extend(json.loads(line) for line in raw_file.read_text(encoding="utf-8").splitlines())
    return rows


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class NonTestOnlyTransport(FakeTransport):
    TEST_ONLY = False


class SlowStartupTransport(FakeTransport):
    def __init__(
        self,
        clock: FakeClock,
        *,
        metadata_delay_ns: int = 0,
        connect_delay_ns: int = 0,
    ) -> None:
        super().__init__(clock, [])
        self.metadata_delay_ns = metadata_delay_ns
        self.connect_delay_ns = connect_delay_ns

    async def metadata(self) -> Any:
        self.clock.advance(self.metadata_delay_ns)
        return await super().metadata()

    async def connect(self) -> None:
        self.clock.advance(self.connect_delay_ns)
        await super().connect()


def test_window_config_rejects_zero_duplicate_and_more_than_six_windows(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="window_seconds"):
        bounded_config(tmp_path / "zero", window_seconds=[0])

    with pytest.raises(ValueError, match="window_seconds"):
        bounded_config(tmp_path / "duplicate", window_seconds=[1, 1])

    with pytest.raises(ValueError, match="window_seconds"):
        bounded_config(tmp_path / "too-many", window_seconds=[1, 5, 30, 60, 300, 900, 1800])


def test_non_test_only_transport_is_rejected_before_any_transport_method(tmp_path: Path) -> None:
    clock = FakeClock()
    transport = NonTestOnlyTransport(clock, [])

    with pytest.raises(TransportScopeError, match="TEST_ONLY"):
        run_controller(bounded_config(tmp_path), transport, clock)

    assert transport.metadata_calls == 0
    assert transport.connect_calls == 0
    assert transport.receive_calls == 0


def test_global_660_second_monotonic_deadline_stops_slow_metadata_before_connect(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    transport = SlowStartupTransport(clock, metadata_delay_ns=661 * NS)

    summary = run_controller(bounded_config(tmp_path, observation_seconds=600), transport, clock)

    assert summary["status"] == "PARTIAL"
    assert summary["stop_reason"] == "GLOBAL_TIME_LIMIT"
    assert transport.metadata_calls == 1
    assert transport.connect_calls == 0
    assert summary["observation_start_ns"] is None


def test_startup_phase_stops_when_valid_rest_and_connect_exceed_phase_budget(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    transport = SlowStartupTransport(
        clock,
        metadata_delay_ns=14 * NS,
        connect_delay_ns=7 * NS,
    )

    summary = run_controller(bounded_config(tmp_path, startup_seconds=20), transport, clock)

    assert summary["status"] == "PARTIAL"
    assert summary["stop_reason"] == "STARTUP_TIME_LIMIT"
    assert summary["observation_start_ns"] is None


def test_silent_poll_watchdog_stops_without_waiting_for_next_market_message(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    transport = FakeTransport(
        clock,
        [
            (
                start + 5 * NS,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False),
                ),
            )
        ],
    )

    summary = run_controller(
        bounded_config(tmp_path, market_silence_seconds=1),
        transport,
        clock,
    )

    assert summary["status"] == "PARTIAL"
    assert summary["stop_reason"] == "MARKET_SILENCE"
    assert summary["messages"] == 0
    assert transport.receive_calls == 0
    assert clock.monotonic_ns() == start + NS


def test_test_only_controller_uses_resolved_unit_binding_and_publishes_fixed_delta_oracle(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    transport = FakeTransport(
        clock,
        [
            (
                start + 100_000_000,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False, nq="0.30"),
                ),
            ),
            (
                start + 200_000_000,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=2, quantity="0.20", buyer_is_maker=False),
                ),
            ),
            (
                start + 300_000_000,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=3, quantity="0.10", buyer_is_maker=True),
                ),
            ),
            (
                start + NS,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=2, quantity="0.20", buyer_is_maker=False),
                ),
            ),
            (start + 2 * NS, WsEvent(kind="disconnect")),
        ],
    )

    summary = run_controller(bounded_config(tmp_path, window_seconds=[1]), transport, clock)

    store = Store(tmp_path / "run")
    feature = next(
        row
        for row in store.read_table("features")
        if row["feature_cutoff_ns"] == start + NS and row["window_seconds"] == 1
    )
    normalized = store.read_table("normalized")
    quarantine = store.read_table("quarantine")
    assert summary["status"] == "PARTIAL"
    assert summary["stop_reason"] == "DISCONNECT"
    assert summary["messages"] == 4
    assert len(normalized) == 3
    assert quarantine[-1]["quality_flags"] == ["DUPLICATE"]
    assert feature["buy_volume_base"] == Decimal("0.500000000000000000")
    assert feature["sell_volume_base"] == Decimal("0.100000000000000000")
    assert feature["volume_base"] == Decimal("0.600000000000000000")
    assert feature["trade_delta_base"] == Decimal("0.400000000000000000")
    assert feature["trade_imbalance"] == 2 / 3
    assert feature["agg_message_count"] == 3
    assert feature["unit_binding_scope"] == "TEST_ONLY_OFFLINE"
    assert feature["unit_binding_id"] == UNIT_EVIDENCE_ID
    assert feature["unit_approval_id"] == "TEST_ONLY:r2-04:unit-binding"


def test_thirty_second_window_is_null_during_warmup(tmp_path: Path) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    transport = FakeTransport(
        clock,
        [
            (
                start + 100_000_000,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False),
                ),
            ),
            (
                start + NS,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=2, quantity="0.20", buyer_is_maker=False),
                ),
            ),
            (start + 2 * NS, WsEvent(kind="disconnect")),
        ],
    )

    run_controller(bounded_config(tmp_path, window_seconds=[30]), transport, clock)

    store = Store(tmp_path / "run")
    feature = next(
        row
        for row in store.read_table("features")
        if row["feature_cutoff_ns"] == start + NS and row["window_seconds"] == 30
    )
    assert feature["quality_flags"] == ["WARMUP"]
    assert feature["volume_base"] is None
    assert feature["trade_delta_base"] is None
    assert feature["trade_imbalance"] is None


def test_feature_publish_is_recorded_only_after_raw_and_cutoff_are_durable(tmp_path: Path) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    transport = FakeTransport(
        clock,
        [
            (
                start + 100_000_000,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False),
                ),
            ),
            (
                start + NS,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=2, quantity="0.20", buyer_is_maker=False),
                ),
            ),
            (start + 2 * NS, WsEvent(kind="disconnect")),
        ],
    )

    run_controller(bounded_config(tmp_path), transport, clock)

    rows = read_jsonl_rows(tmp_path / "run")
    publish = next(row for row in rows if row["kind"] == "FEATURE_PUBLISHED")
    cutoff = next(row for row in rows if row["kind"] == "FEATURE_CUTOFF")
    cutoff_index = rows.index(cutoff)
    prior_sequences = [row["ingest_sequence"] for row in rows[:cutoff_index]]
    assert cutoff["ingest_sequence"] < publish["ingest_sequence"]
    assert cutoff["input_cursor"] == max(prior_sequences)
    assert all(row["ingest_sequence"] <= cutoff["input_cursor"] for row in rows[:cutoff_index])
    assert publish["feature_available_ns"] == publish["ts_receive_ns"]
    assert publish["feature_available_ns"] >= cutoff["cutoff_ns"]


def test_read_only_bounded_replay_preserves_input_and_matches_original_features(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    transport = FakeTransport(
        clock,
        [
            (
                start + 100_000_000,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False),
                ),
            ),
            (
                start + NS,
                WsEvent(
                    kind="message",
                    payload=agg_trade(agg_id=2, quantity="0.20", buyer_is_maker=False),
                ),
            ),
            (start + 2 * NS, WsEvent(kind="disconnect")),
        ],
    )

    run_controller(bounded_config(tmp_path, window_seconds=[1]), transport, clock)
    original_features = Store(tmp_path / "run").read_table("features")
    before = tree_digest(tmp_path / "run")
    result1 = replay_bounded(tmp_path / "run")
    result2 = replay_bounded(tmp_path / "run")
    after = tree_digest(tmp_path / "run")

    assert result1["status"] == "PASS"
    assert result1["read_only"] is True
    assert after == before
    assert result1["features"] == original_features
    assert result2["features"] == original_features
    assert result1["logical_sha256"] == result2["logical_sha256"]
    assert result1["features"][0]["buy_volume_base"] == Decimal("0.30")
    assert result1["features"][0]["volume_base"] == Decimal("0.30")
    assert result2 == result1


def test_message_burst_does_not_consume_outbound_control_budget(tmp_path: Path) -> None:
    # Directional control accounting is independent of actual fsync throughput.
    # The real ProcessWriter one-second pressure failures remain in data/session.
    from test_bounded_parent_review import RecordingWriter

    clock = FakeClock()
    start = clock.wall_ns()
    events = [
        (
            start + index * 1_000_000,
            WsEvent(
                kind="message",
                payload=agg_trade(
                    agg_id=index + 1,
                    quantity="0.001",
                    buyer_is_maker=bool(index % 2),
                ),
            ),
        )
        for index in range(1000)
    ]
    events.append((start + NS, WsEvent(kind="disconnect")))
    transport = FakeTransport(clock, events)

    summary = run_controller(
        bounded_config(tmp_path, observation_seconds=1, max_messages=1000),
        transport,
        clock,
        writer=RecordingWriter(clock),
    )

    assert summary["messages"] == 1000
    assert summary["stop_reason"] == "DISCONNECT"
    assert [kind for kind, _, _ in transport.sent_controls] == ["close"]


def test_outbound_control_rate_cap_stops_on_second_pong_when_close_is_reserved(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    transport = FakeTransport(
        clock,
        [
            (start + 100_000_000, WsEvent(kind="ping", payload=b"a")),
            (start + 200_000_000, WsEvent(kind="ping", payload=b"b")),
        ],
    )

    summary = run_controller(
        bounded_config(tmp_path, max_outbound_control_per_second=1),
        transport,
        clock,
    )

    assert summary["status"] == "PARTIAL"
    assert summary["stop_reason"] == "OUTBOUND_CONTROL_RATE_LIMIT"
    assert [kind for kind, _, _ in transport.sent_controls] == ["pong", "close"]


def test_live_entry_fails_closed_when_network_or_shared_budget_are_marked_authorized(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    transport = FakeTransport(clock, [])

    with pytest.raises(TransportScopeError, match="blocked"):
        run_controller(
            bounded_config(tmp_path / "network", network_authorized=True),
            transport,
            clock,
        )

    with pytest.raises(TransportScopeError, match="blocked"):
        run_controller(
            bounded_config(tmp_path / "budget", shared_budget_trusted=True),
            transport,
            clock,
        )


def test_resource_ledger_rejects_message_count_byte_and_outbound_boundaries(tmp_path: Path) -> None:
    config = cast(
        BoundedLiveConfig,
        SimpleNamespace(
            max_messages=1,
            max_ws_payload_bytes=4,
            max_message_bytes=65_536,
            max_outbound_control_payload_bytes=2,
            max_outbound_control_total=64,
            max_outbound_control_per_second=2,
        ),
    )
    ledger = ResourceLedger(config)

    ledger.observe_message(4)
    with pytest.raises(StopRun, match="MESSAGE_COUNT_LIMIT"):
        ledger.observe_message(1)

    with pytest.raises(StopRun, match="OUTBOUND_CONTROL_PAYLOAD_LIMIT"):
        ledger.reserve_outbound_control(0, 3)
