from __future__ import annotations

import asyncio
import base64
import json
import multiprocessing as mp
from multiprocessing.connection import Connection
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import aiohttp
import pytest

from btc_microstructure import bounded_writer
from btc_microstructure.bounded_transport import MetadataReply, RealNetworkTransport, WsEvent
from btc_microstructure.bounded_writer import _worker
from btc_microstructure.offline_core import BOUND_ADAPTER_VERSION, NS
from btc_microstructure.offline_storage import Store

from bounded_fakes import FakeClock, FakeTransport, metadata_body
from test_bounded_live import bounded_config, run_controller


def _snapshot(path: Path) -> dict[str, Any]:
    value = json.loads((path / "run" / "snapshots" / "run-context.json").read_text())
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(
    "metadata,expected",
    [
        (
            MetadataReply(status=429, headers={"x-mbx-used-weight-1m": "42"}, body=b"limit"),
            "REST_HTTP_429",
        ),
        (
            MetadataReply(status=200, headers={}, body=b'{"symbols":[', body_complete=False),
            "REST_NOT_COMPLETE",
        ),
        (
            MetadataReply(
                status=200,
                headers={},
                body=metadata_body(symbol="ETHUSDT"),
            ),
            "REST_IDENTITY_MISMATCH",
        ),
    ],
)
def test_rest_metadata_rejection_is_durable_before_semantic_start(
    tmp_path: Path, metadata: MetadataReply, expected: str
) -> None:
    clock = FakeClock()
    transport = FakeTransport(clock, [], metadata=metadata)

    result = run_controller(bounded_config(tmp_path), transport, clock)

    assert result["status"] == "PARTIAL"
    assert result["stop_reason"] == expected
    assert result["trusted_cursor"] is not None
    assert transport.connect_calls == 0
    context = _snapshot(tmp_path)
    response = context["metadata_response"]
    assert response["status"] == metadata.status
    assert response["body_complete"] is metadata.body_complete
    assert base64.b64decode(response["base64"]) == metadata.body
    rows = list(Store(tmp_path / "run").read_rows())
    rest_rows = [row for row in rows if row["kind"] == "REST_RESPONSE"]
    assert len(rest_rows) == 1
    assert rest_rows[0]["body_sha256"] == response["sha256"]
    failure = next(row for row in rows if row["kind"] == "STARTUP_FAILURE")
    assert failure["failure_reason"] == expected


def test_unit_binding_rejection_preserves_rest_snapshot_and_receipt(tmp_path: Path) -> None:
    clock = FakeClock()
    config = bounded_config(tmp_path)
    approval = dict(config.unit_approval)
    approval["filters"] = {"tickSize": "0.20", "stepSize": "0.001", "minQty": "0.001"}
    config = bounded_config(tmp_path, unit_approval=approval)

    result = run_controller(config, FakeTransport(clock, []), clock)

    assert result["status"] == "BLOCKED"
    assert "UnitBindingError" in result["stop_reason"]
    assert result["trusted_cursor"] is not None
    context = _snapshot(tmp_path)
    assert base64.b64decode(context["metadata_response"]["base64"]) == metadata_body()
    rows = list(Store(tmp_path / "run").read_rows())
    assert next(row for row in rows if row["kind"] == "REST_RESPONSE")
    failure = next(row for row in rows if row["kind"] == "STARTUP_FAILURE")
    assert "UnitBindingError" in failure["failure_reason"]


def test_test_only_raw_and_control_rows_use_bounded_adapter_version(tmp_path: Path) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    run_controller(
        bounded_config(tmp_path, window_seconds=[1]),
        FakeTransport(
            clock,
            [
                (
                    start,
                    WsEvent(
                        "message",
                        b'{"e":"aggTrade","s":"BTCUSDT","a":1,"p":"100.00","q":"0.001","f":1,"l":1,"m":false,"E":1000,"T":1000,"st":1}',
                    ),
                ),
                (start + NS, WsEvent("disconnect")),
            ],
        ),
        clock,
    )

    rows = list(Store(tmp_path / "run").read_rows())
    assert rows
    assert {row["adapter_version"] for row in rows if row["kind"] != "RAW"} == {
        BOUND_ADAPTER_VERSION
    }
    assert {row["adapter_version"] for row in rows if row["kind"] == "RAW"} == {
        BOUND_ADAPTER_VERSION
    }


def test_batch_reserves_budget_before_store_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    original_reserve = bounded_writer.Budget.reserve
    original_append = Store.append

    def reserve(self: Any, charges: dict[str, int], disk_free: int) -> None:
        calls.append("reserve")
        return original_reserve(self, charges, disk_free)

    def append(self: Any, row: dict[str, Any]) -> None:
        calls.append("append")
        return original_append(self, row)

    monkeypatch.setattr(bounded_writer.Budget, "reserve", reserve)
    monkeypatch.setattr(Store, "append", append)

    class FakeConnection:
        def __init__(self) -> None:
            self.requests = [
                json.dumps(
                    {
                        "operation": "batch",
                        "rows": [
                            {
                                "kind": "SESSION_OPEN",
                                "session_id": "s",
                                "ingest_sequence": 1,
                                "ts_receive_ns": 1,
                                "receive_monotonic_ns": 1,
                            }
                        ],
                    }
                ).encode(),
                json.dumps({"operation": "close"}).encode(),
            ]

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

    ack = FakeAck()
    context = mp.get_context("spawn")
    _worker(
        cast(Connection, FakeConnection()),
        cast(Connection, ack),
        str(tmp_path / "run"),
        {},
        512 * 1024 * 1024,
        0,
        None,
        512 * 1024 * 1024,
        context.Value("Q", 0),
        context.Value("Q", 0),
        context.Value("b", 0),
    )

    assert "reserve" in calls and "append" in calls
    assert calls.index("reserve") < calls.index("append")


class _FakeRestResponse:
    status = 200
    headers = {"x-mbx-used-weight-1m": "1"}

    def __init__(self, body: bytes) -> None:
        self.content = self
        self._body = body
        self._read = False

    async def __aenter__(self) -> "_FakeRestResponse":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def read(self, size: int) -> bytes:
        if self._read:
            return b""
        self._read = True
        return self._body[:size]


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes]] = []
        self._events = [
            SimpleNamespace(type=aiohttp.WSMsgType.PING, data=b"p"),
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="{}"),
        ]

    async def receive(self, timeout: float | None = None) -> Any:
        return self._events.pop(0)

    async def pong(self, payload: bytes) -> None:
        self.sent.append(("pong", payload))

    async def close(self, *, code: int = 1000, message: bytes = b"") -> None:
        self.sent.append(("close", b""))


class _FakeSession:
    TEST_ONLY = True

    def __init__(self) -> None:
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.ws_calls: list[tuple[str, dict[str, Any]]] = []
        self.ws = _FakeWs()
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> _FakeRestResponse:
        self.get_calls.append((url, kwargs))
        return _FakeRestResponse(metadata_body())

    async def ws_connect(self, url: str, **kwargs: Any) -> _FakeWs:
        self.ws_calls.append((url, kwargs))
        return self.ws

    async def close(self) -> None:
        self.closed = True


def test_physical_adapter_contract_is_exercised_only_with_injected_session() -> None:
    with pytest.raises(Exception, match="blocked|inject"):
        RealNetworkTransport()

    session = _FakeSession()
    transport = RealNetworkTransport.for_test_only_injected_session(session)

    async def run() -> None:
        metadata = await transport.metadata()
        await transport.connect()
        event = await transport.poll(NS)
        assert event == WsEvent("ping", b"p")
        await transport.send_control("pong", b"p")
        assert await transport.poll(NS) == WsEvent("message", b"{}")
        await transport.close()
        assert metadata.status == 200

    asyncio.run(run())

    assert session.get_calls == [
        (
            "https://fapi.binance.com/fapi/v1/exchangeInfo",
            {
                "allow_redirects": False,
                "timeout": RealNetworkTransport.REST_TIMEOUT,
            },
        )
    ]
    assert session.ws_calls and session.ws_calls[0][0] == (
        "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade"
    )
    options = session.ws_calls[0][1]
    assert options["autoping"] is False
    assert options["heartbeat"] is None
    assert options["max_msg_size"] == 65_536
    assert session.ws.sent == [("pong", b"p"), ("close", b"")]
