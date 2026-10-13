from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from typing import Literal

from btc_microstructure.bounded_transport import MetadataReply, WsEvent
from btc_microstructure.offline_core import NS


def metadata_body(
    *,
    symbol: str = "BTCUSDT",
    contract_type: str = "PERPETUAL",
    status: str = "TRADING",
    base_asset: str = "BTC",
    quote_asset: str = "USDT",
    margin_asset: str = "USDT",
) -> bytes:
    return json.dumps(
        {
            "symbols": [
                {
                    "symbol": symbol,
                    "baseAsset": base_asset,
                    "quoteAsset": quote_asset,
                    "marginAsset": margin_asset,
                    "contractType": contract_type,
                    "status": status,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    ],
                }
            ],
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": 2400,
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


def agg_trade(
    *,
    agg_id: int,
    price: str = "100.00",
    quantity: str,
    buyer_is_maker: bool,
    event_time: int = 1000,
    trade_time: int = 1000,
    nq: str | None = None,
    symbol_type_raw: int | None = 1,
) -> bytes:
    payload: dict[str, object] = {
        "e": "aggTrade",
        "s": "BTCUSDT",
        "a": agg_id,
        "p": price,
        "q": quantity,
        "f": agg_id * 10,
        "l": agg_id * 10,
        "m": buyer_is_maker,
        "E": event_time,
        "T": trade_time,
    }
    if nq is not None:
        payload["nq"] = nq
    if symbol_type_raw is not None:
        payload["st"] = symbol_type_raw
    return json.dumps(payload, separators=(",", ":")).encode()


@dataclass
class FakeClock:
    wall: int = 30 * NS
    monotonic: int = 30 * NS

    def wall_ns(self) -> int:
        return self.wall

    def monotonic_ns(self) -> int:
        return self.monotonic

    def advance_to(self, value: int) -> None:
        if value < self.wall:
            raise AssertionError("fake clock cannot move backward")
        delta = value - self.wall
        self.wall = value
        self.monotonic += delta

    def advance(self, delta_ns: int) -> None:
        self.advance_to(self.wall + delta_ns)


class FakeTransport:
    TEST_ONLY = True

    def __init__(
        self,
        clock: FakeClock,
        events: Iterable[tuple[int, WsEvent]],
        *,
        metadata: MetadataReply | None = None,
    ) -> None:
        self.clock = clock
        self.events = list(events)
        self.metadata_reply = metadata or MetadataReply(
            status=200, headers={}, body=metadata_body()
        )
        self.metadata_calls = 0
        self.connect_calls = 0
        self.receive_calls = 0
        self.sent_controls: list[tuple[Literal["pong", "close"], bytes, int]] = []
        self.closed = False

    async def metadata(self) -> MetadataReply:
        self.metadata_calls += 1
        return self.metadata_reply

    async def connect(self) -> None:
        self.connect_calls += 1

    async def receive(self) -> WsEvent:
        self.receive_calls += 1
        if not self.events:
            self.clock.advance(NS)
            return WsEvent(kind="disconnect")
        at_ns, event = self.events.pop(0)
        self.clock.advance_to(at_ns)
        return event

    async def poll(self, timeout_ns: int) -> WsEvent | None:
        if timeout_ns < 0:
            raise AssertionError("poll timeout must be non-negative")
        if not self.events:
            self.clock.advance(timeout_ns)
            return None
        at_ns, event = self.events[0]
        deadline = self.clock.wall_ns() + timeout_ns
        if at_ns > deadline:
            self.clock.advance_to(deadline)
            return None
        self.events.pop(0)
        self.receive_calls += 1
        self.clock.advance_to(at_ns)
        return event

    async def send_control(self, kind: Literal["pong", "close"], payload: bytes = b"") -> None:
        self.sent_controls.append((kind, payload, self.clock.wall_ns()))

    async def close(self) -> None:
        self.closed = True
