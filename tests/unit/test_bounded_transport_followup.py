"""R2-04 review follow-up for bounded physical transport wiring."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

import btc_microstructure.bounded_transport as bounded_transport
from btc_microstructure.bounded_transport import (
    AioBoundedTransport,
    REST_BODY_BYTES,
    REST_TIMEOUT_SECONDS,
    WS_FRAME_BYTES,
    RealNetworkTransport,
    TransportScopeError,
)
from btc_microstructure.probe import REST_URL, WS_URL


class FakeContent:
    def __init__(self, body: bytes, *, error_after_first: BaseException | None = None) -> None:
        self.body = body
        self.error_after_first = error_after_first
        self.read_calls = 0

    async def read(self, size: int) -> bytes:
        self.read_calls += 1
        if self.error_after_first is not None and self.read_calls > 1:
            raise self.error_after_first
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


class TimeoutAfterPrefixContent(FakeContent):
    async def read(self, size: int) -> bytes:
        self.read_calls += 1
        if self.read_calls == 1:
            chunk, self.body = self.body[:size], self.body[size:]
            return chunk
        raise TimeoutError


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        error_after_first: BaseException | None = None,
    ) -> None:
        self.status = status
        self.headers = {"X-MBX-USED-WEIGHT-1M": "1", "Set-Cookie": "not-retained"}
        self.content = FakeContent(body, error_after_first=error_after_first)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeWS:
    def __init__(self, frames: list[Any] | None = None) -> None:
        self.frames = list(frames or [])
        self.sent: list[tuple[str, bytes]] = []
        self.closed = False

    async def receive(self, *, timeout: float | None = None) -> Any:
        self.sent.append(("receive_timeout", str(timeout).encode()))
        return self.frames.pop(0)

    async def pong(self, payload: bytes) -> None:
        self.sent.append(("pong", payload))

    async def close(self, *, code: int = 1000, message: bytes = b"") -> None:
        self.sent.append(("close", code.to_bytes(2, "big") + message))
        self.closed = True


class SelfClosingFakeWS(FakeWS):
    async def receive(self, *, timeout: float | None = None) -> Any:
        self.sent.append(("receive_timeout", str(timeout).encode()))
        await self.close(code=1002, message=b"protocol")
        return SimpleNamespace(type=aiohttp.WSMsgType.ERROR, data=RuntimeError("synthetic"))


class FakeSession:
    TEST_ONLY = True

    def __init__(
        self,
        *,
        response: FakeResponse | None = None,
        ws: FakeWS | None = None,
        options_sink: list[dict[str, Any]],
        calls_sink: list[tuple[str, str, dict[str, Any]]],
        **options: Any,
    ) -> None:
        options_sink.append(options)
        self.response = response or FakeResponse(b'{"ok":true}')
        self.ws = ws or FakeWS()
        self.calls_sink = calls_sink
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls_sink.append(("GET", url, kwargs))
        return self.response

    async def ws_connect(self, url: str, **kwargs: Any) -> FakeWS:
        self.calls_sink.append(("WS", url, kwargs))
        return self.ws

    async def close(self) -> None:
        self.closed = True


class TimeoutAfterPrefixResponse(FakeResponse):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.content = TimeoutAfterPrefixContent(body)


def make_transport(
    *,
    response: FakeResponse | None = None,
    ws: FakeWS | None = None,
    options: list[dict[str, Any]] | None = None,
    calls: list[tuple[str, str, dict[str, Any]]] | None = None,
) -> AioBoundedTransport:
    options_sink = options if options is not None else []
    calls_sink = calls if calls is not None else []

    def factory(**session_options: Any) -> FakeSession:
        return FakeSession(
            response=response,
            ws=ws,
            options_sink=options_sink,
            calls_sink=calls_sink,
            **session_options,
        )

    factory.TEST_ONLY = True  # type: ignore[attr-defined]
    return AioBoundedTransport(session_factory=factory)


def test_real_construction_remains_blocked() -> None:
    with pytest.raises(TransportScopeError, match="TEST_ONLY fake"):
        AioBoundedTransport()
    with pytest.raises(TransportScopeError, match="real Binance transport is blocked"):
        RealNetworkTransport()
    with pytest.raises(TransportScopeError, match="injected_session"):
        RealNetworkTransport.for_test_only_injected_session(object())


def test_factory_returning_real_session_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    class RealLookingSession:
        pass

    def factory(**_: Any) -> object:
        return RealLookingSession()

    factory.TEST_ONLY = True  # type: ignore[attr-defined]
    monkeypatch.setattr(bounded_transport.aiohttp, "ClientSession", RealLookingSession)
    transport = AioBoundedTransport(session_factory=factory)

    with pytest.raises(TransportScopeError, match="REAL_AIOHTTP_SESSION_REJECTED"):
        asyncio.run(transport.metadata())


def test_metadata_uses_fixed_rest_policy_and_retains_rejection_body() -> None:
    options: list[dict[str, Any]] = []
    calls: list[tuple[str, str, dict[str, Any]]] = []
    response = FakeResponse(b'{"code":451,"msg":"blocked"}', status=451)
    transport = make_transport(response=response, options=options, calls=calls)

    reply = asyncio.run(transport.metadata())

    assert reply.status == 451
    assert reply.body == b'{"code":451,"msg":"blocked"}'
    assert reply.body_complete is True
    assert reply.headers == {"x-mbx-used-weight-1m": "1"}
    session_options = options[0]
    assert session_options["trust_env"] is False
    assert isinstance(session_options["cookie_jar"], aiohttp.DummyCookieJar)
    assert session_options["headers"] == {"Accept-Encoding": "identity"}
    assert session_options["auto_decompress"] is False
    assert session_options["timeout"].total == REST_TIMEOUT_SECONDS
    assert session_options["timeout"].connect == 5
    assert session_options["timeout"].sock_read == 5
    method, url, kwargs = calls[0]
    assert method == "GET" and url == REST_URL
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"].total == REST_TIMEOUT_SECONDS


def test_metadata_reports_body_limit_and_partial_body_without_swallowing_prefix() -> None:
    transport = make_transport(response=FakeResponse(b"x" * (REST_BODY_BYTES + 2)))

    reply = asyncio.run(transport.metadata())

    assert len(reply.body) == REST_BODY_BYTES + 1
    assert reply.body_complete is False


def test_metadata_interruption_after_headers_returns_durable_prefix() -> None:
    response = FakeResponse(
        b'{"prefix":true}',
        error_after_first=aiohttp.ClientPayloadError("synthetic partial"),
    )
    transport = make_transport(response=response)

    reply = asyncio.run(transport.metadata())

    assert reply.status == 200
    assert reply.body == b'{"prefix":true}'
    assert reply.body_complete is False


def test_metadata_total_timeout_wraps_complete_body_read_and_preserves_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeouts: list[float] = []

    class ObservedTimeout:
        def __init__(self, delay: float) -> None:
            observed_timeouts.append(delay)

        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(bounded_transport.asyncio, "timeout", ObservedTimeout)
    transport = make_transport(response=TimeoutAfterPrefixResponse(b'{"prefix":true}'))

    with pytest.raises(TimeoutError):
        asyncio.run(transport.metadata())

    assert observed_timeouts == [REST_TIMEOUT_SECONDS]
    observation = transport.metadata_observation
    assert observation is not None
    assert observation.status == 200
    assert observation.body == b'{"prefix":true}'
    assert observation.body_complete is False


def test_ws_connect_autoping_disabled_and_control_send_points_are_counted() -> None:
    ws = FakeWS(
        [
            SimpleNamespace(type=aiohttp.WSMsgType.PING, data=b"hb"),
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data='{"stream":"x"}'),
            SimpleNamespace(type=aiohttp.WSMsgType.CLOSE, data=b"bye"),
        ]
    )
    calls: list[tuple[str, str, dict[str, Any]]] = []
    transport = make_transport(ws=ws, calls=calls)

    async def exercise() -> None:
        await transport.connect()
        first = await transport.poll(123_000_000)
        assert first is not None
        assert first.kind == "ping"
        assert first.payload == b"hb"
        await transport.send_control("pong", first.payload)
        second = await transport.poll(456_000_000)
        assert second is not None
        assert second.kind == "message"
        third = await transport.poll(789_000_000)
        assert third is not None
        assert third.kind == "close"
        await transport.send_control("close", b"")
        await transport.close()

    asyncio.run(exercise())

    method, url, kwargs = calls[0]
    assert method == "WS" and url == WS_URL
    assert kwargs["max_msg_size"] == WS_FRAME_BYTES
    assert kwargs["autoping"] is False
    assert kwargs["autoclose"] is False
    assert kwargs["heartbeat"] is None
    assert kwargs["compress"] == 0
    assert kwargs["timeout"].ws_receive == 5
    assert ws.sent == [
        ("receive_timeout", b"0.123"),
        ("pong", b"hb"),
        ("receive_timeout", b"0.456"),
        ("receive_timeout", b"0.789"),
        ("close", (1000).to_bytes(2, "big")),
    ]
    assert transport.control_counts == {
        "inbound_ping": 1,
        "inbound_pong": 0,
        "inbound_close": 1,
        "outbound_pong": 1,
        "outbound_close": 1,
        "outbound_total": 2,
        "outbound_payload_bytes": 2,
    }


def test_ws_payload_and_outbound_control_limits_are_enforced_at_transport_boundary() -> None:
    ws = FakeWS([SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="x" * (WS_FRAME_BYTES + 1))])
    transport = make_transport(ws=ws)

    async def oversized_frame() -> None:
        await transport.connect()
        await transport.poll(1_000_000)

    with pytest.raises(TransportScopeError, match="WS_FRAME_BYTE_LIMIT"):
        asyncio.run(oversized_frame())

    ws2 = FakeWS()
    transport2 = make_transport(ws=ws2)

    async def oversized_control() -> None:
        await transport2.connect()
        await transport2.send_control("pong", b"x" * 126)

    with pytest.raises(TransportScopeError, match="OUTBOUND_CONTROL_PAYLOAD_LIMIT"):
        asyncio.run(oversized_control())


def test_poll_rejects_ws_event_shortcut_and_counts_error_frame() -> None:
    shortcut = make_transport(ws=FakeWS([bounded_transport.WsEvent("ping", b"masked")]))

    async def shortcut_poll() -> None:
        await shortcut.connect()
        await shortcut.poll(1_000_000)

    with pytest.raises(TransportScopeError, match="WS_UNSUPPORTED_FRAME"):
        asyncio.run(shortcut_poll())
    assert shortcut.control_counts["inbound_ping"] == 0

    errored = make_transport(ws=FakeWS([SimpleNamespace(type=aiohttp.WSMsgType.ERROR, data=None)]))

    async def error_poll() -> None:
        await errored.connect()
        await errored.poll(1_000_000)

    with pytest.raises(TransportScopeError, match="WS_ERROR_FRAME"):
        asyncio.run(error_poll())
    assert errored.control_counts["inbound_close"] == 1


def test_binary_payload_and_close_code_reason_are_preserved_as_control_bytes() -> None:
    transport = make_transport(
        ws=FakeWS(
            [
                SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=b"\xffraw"),
                SimpleNamespace(type=aiohttp.WSMsgType.CLOSE, data=1001, extra="bye"),
            ]
        )
    )

    async def exercise() -> tuple[bounded_transport.WsEvent, bounded_transport.WsEvent]:
        await transport.connect()
        first = await transport.poll(1_000_000)
        second = await transport.poll(1_000_000)
        assert first is not None and second is not None
        return first, second

    binary, close = asyncio.run(exercise())
    assert binary == bounded_transport.WsEvent("message", b"\xffraw")
    assert close == bounded_transport.WsEvent("close", (1001).to_bytes(2, "big") + b"bye")
    assert transport.control_counts["inbound_close"] == 1


def test_internal_ws_close_from_receive_is_counted_at_wrapped_send_boundary() -> None:
    ws = SelfClosingFakeWS()
    transport = make_transport(ws=ws)

    async def exercise() -> None:
        await transport.connect()
        with pytest.raises(TransportScopeError, match="WS_ERROR_FRAME"):
            await transport.poll(1_000_000)

    asyncio.run(exercise())

    assert ws.sent == [
        ("receive_timeout", b"0.001"),
        ("close", (1002).to_bytes(2, "big") + b"protocol"),
    ]
    assert transport.control_counts["outbound_close"] == 1
    assert transport.control_counts["inbound_close"] == 1


def test_outbound_control_rolling_window_reserves_before_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = FakeWS()
    transport = make_transport(ws=ws)
    now = 10_000_000_000
    monkeypatch.setattr(bounded_transport.time, "monotonic_ns", lambda: now)

    async def same_second_limit() -> None:
        await transport.connect()
        await transport.send_control("pong", b"one")
        with pytest.raises(TransportScopeError, match="OUTBOUND_CONTROL_RATE_LIMIT"):
            await transport.send_control("pong", b"two")
        await transport.send_control("close", b"")

    asyncio.run(same_second_limit())
    assert ws.sent == [("pong", b"one"), ("close", (1000).to_bytes(2, "big"))]
    assert transport.control_counts["outbound_pong"] == 1
    assert transport.control_counts["outbound_close"] == 1


def test_aiohttp_middleware_blocks_hidden_reentry() -> None:
    transport = make_transport()
    request = SimpleNamespace(method="GET", url=REST_URL)

    async def handler(_: object) -> object:
        raise aiohttp.ServerDisconnectedError("synthetic retry branch")

    with pytest.raises(TransportScopeError, match="REST_NETWORK_ERROR"):
        asyncio.run(transport._single_attempt(request, handler))  # noqa: SLF001
    with pytest.raises(TransportScopeError, match="IMPLICIT_RETRY_BLOCKED"):
        asyncio.run(transport._single_attempt(request, handler))  # noqa: SLF001
    assert transport.request_attempts == {"rest": 1, "ws": 0}
    assert transport.blocked_reentries == 1
