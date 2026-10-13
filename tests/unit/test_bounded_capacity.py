"""L04 sustained-capacity regression for the single TEST_ONLY source."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any
from typing import Literal

from btc_microstructure.bounded_transport import MetadataReply, WsEvent
from btc_microstructure.offline_core import NS

from bounded_fakes import FakeClock, agg_trade, metadata_body
from test_bounded_live import bounded_config, run_controller


class UniformStreamingTransport:
    TEST_ONLY = True

    def __init__(self, clock: FakeClock, *, messages: int, rate: int) -> None:
        self.clock = clock
        self.messages = messages
        self.interval_ns = NS // rate
        self.sent = 0
        self.next_message_ns: int | None = None
        self.sent_controls: list[tuple[str, bytes]] = []

    async def metadata(self) -> MetadataReply:
        return MetadataReply(200, {}, metadata_body())

    async def connect(self) -> None:
        self.next_message_ns = self.clock.wall_ns() + self.interval_ns

    async def poll(self, timeout_ns: int) -> WsEvent:
        assert self.next_message_ns is not None
        deadline = self.clock.wall_ns() + timeout_ns
        if self.sent >= self.messages or self.next_message_ns > deadline:
            self.clock.advance(timeout_ns)
            return WsEvent("tick")
        self.clock.advance_to(self.next_message_ns)
        self.sent += 1
        self.next_message_ns += self.interval_ns
        milliseconds = self.clock.wall_ns() // 1_000_000
        return WsEvent(
            "message",
            agg_trade(
                agg_id=self.sent,
                quantity="0.001",
                buyer_is_maker=bool(self.sent % 2),
                event_time=milliseconds,
                trade_time=milliseconds,
            ),
        )

    async def send_control(self, kind: Literal["pong", "close"], payload: bytes = b"") -> None:
        self.sent_controls.append((kind, payload))

    async def close(self) -> None:
        return None


class RecordingWriter:
    def __init__(self) -> None:
        self.cursor: int | None = None
        self.queue: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self.tables: list[tuple[str, list[dict[str, Any]]]] = []

    def append(self, row: dict[str, Any]) -> None:
        self.queue.append(deepcopy(row))

    def flush(self) -> None:
        if self.queue:
            self.rows.extend(self.queue)
            self.cursor = self.queue[-1]["ingest_sequence"]
            self.queue.clear()

    def append_durable(self, row: dict[str, Any]) -> None:
        self.append(row)
        self.flush()

    def write_snapshot(self, relative_path: str, payload: bytes) -> None:
        return None

    def write_table(self, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        self.tables.append((name, deepcopy(rows)))
        return {"manifest": {"path": name, "sha256": "TEST_ONLY"}}

    def close(self) -> None:
        self.flush()


def test_state_budget_completes_ten_per_second_for_600_simulated_seconds(
    tmp_path,
) -> None:
    clock = FakeClock()
    transport = UniformStreamingTransport(clock, messages=6000, rate=10)
    writer = RecordingWriter()
    config = bounded_config(
        tmp_path,
        observation_seconds=600,
        window_seconds=[30],
    )

    result = run_controller(config, transport, clock, writer)

    assert result["stop_reason"] == "OBSERVATION_COMPLETE"
    assert result["messages"] == 6000
    assert result["normalized_rows"] == 6000
    assert result["feature_rows"] == 600
    assert result["reserved_state_upper_bound_bytes"] <= 128 * 1024 * 1024
    final_features = next(rows for name, rows in reversed(writer.tables) if name == "features")
    final = final_features[-1]
    assert final["agg_message_count"] == 300
    assert final["buy_volume_base"] == Decimal("0.150")
    assert final["sell_volume_base"] == Decimal("0.150")
    assert final["volume_base"] == Decimal("0.300")
    assert final["trade_delta_base"] == Decimal("0.000")
    assert final["trade_imbalance"] == 0.0
    assert final["quality_flags"] == []
