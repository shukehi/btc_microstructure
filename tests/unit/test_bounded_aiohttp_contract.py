"""Offline aiohttp contracts for R2-04 bounded transport control accounting."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import WSMessage, WSMsgType
from aiohttp.client_ws import ClientWebSocketResponse
from aiohttp.client_proto import ResponseHandler
from aiohttp.connector import BaseConnector, Connection
from aiohttp.http import HttpVersion, RawResponseMessage
from aiohttp.http_websocket import WebSocketError
from aiohttp.streams import EofStream, StreamReader
from multidict import CIMultiDict, CIMultiDictProxy

from btc_microstructure.bounded_live import run_test_only_bounded_live
from btc_microstructure.bounded_transport import (
    AioBoundedTransport,
    MetadataReply,
    TransportScopeError,
)
from btc_microstructure.probe import REST_URL, WS_URL

from bounded_fakes import FakeClock, metadata_body
from test_bounded_live import bounded_config
from test_bounded_parent_review import RecordingWriter


class RealWsReader:
    def __init__(self, read: Callable[[], Awaitable[WSMessage]]) -> None:
        self._read = read

    async def read(self) -> WSMessage:
        return await self._read()

    def feed_data(self, message: WSMessage, size: int) -> None:
        raise AssertionError(f"unexpected feed_data({message!r}, {size!r})")


class RealWsWriter:
    def __init__(self) -> None:
        self.closes: list[tuple[int, bytes]] = []

    async def close(self, code: int = 1000, message: bytes = b"") -> None:
        self.closes.append((code, message))


class RealWsResponse:
    connection = object()

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class RealWsSession:
    TEST_ONLY = True

    def __init__(self, ws: ClientWebSocketResponse) -> None:
        self.ws = ws
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    async def ws_connect(self, url: str, **kwargs: Any) -> ClientWebSocketResponse:
        self.calls.append(("WS", url, kwargs))
        return self.ws

    async def close(self) -> None:
        self.closed = True


class MetadataOnlyAioBoundedTransport(AioBoundedTransport):
    async def metadata(self) -> MetadataReply:
        return MetadataReply(status=200, headers={}, body=metadata_body())


class InMemoryTransport:
    def __init__(self) -> None:
        self.closed = False
        self.aborted = False
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def writelines(self, lines: list[bytes]) -> None:
        self.writes.extend(lines)

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def abort(self) -> None:
        self.aborted = True
        self.closed = True

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return default


class GuardedConnector(BaseConnector):
    def __init__(self, protocol: ResponseHandler) -> None:
        super().__init__(force_close=True)
        self.protocol = protocol
        self.connect_calls = 0

    async def _create_connection(self, req: Any, traces: Any, timeout: Any) -> ResponseHandler:
        raise AssertionError("must not create a real socket")

    async def connect(
        self, req: Any, traces: list[Any], timeout: aiohttp.ClientTimeout
    ) -> Connection:
        self.connect_calls += 1
        self._acquired.add(self.protocol)
        return Connection(self, req.connection_key, self.protocol, self._loop)


class WrappedClientSession:
    TEST_ONLY = True

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self.session = session

    def get(self, *args: Any, **kwargs: Any) -> Any:
        assert self.session is not None
        return self.session.get(*args, **kwargs)

    async def ws_connect(self, *args: Any, **kwargs: Any) -> Any:
        assert self.session is not None
        return await self.session.ws_connect(*args, **kwargs)

    async def close(self) -> None:
        assert self.session is not None
        await self.session.close()


def feed_response(
    protocol: ResponseHandler,
    *,
    status: int,
    headers: dict[str, str] | None = None,
) -> None:
    header_map = CIMultiDict(headers or {})
    message = RawResponseMessage(
        HttpVersion(1, 1),
        status,
        "OK",
        CIMultiDictProxy(header_map),
        tuple((key.encode(), value.encode()) for key, value in header_map.items()),
        True,
        None,
        False,
        False,
    )
    payload = StreamReader(protocol, 2**16, loop=asyncio.get_running_loop())
    payload.feed_eof()
    protocol.feed_data((message, payload), 0)


def make_real_client_session(
    transport: AioBoundedTransport,
    *,
    status: int = 200,
    extra_middlewares: tuple[Any, ...] = (),
) -> tuple[aiohttp.ClientSession, GuardedConnector]:
    loop = asyncio.get_running_loop()
    protocol = ResponseHandler(loop=loop)
    protocol.connection_made(InMemoryTransport())  # type: ignore[arg-type]
    feed_response(protocol, status=status, headers={"Location": REST_URL})
    connector = GuardedConnector(protocol)
    options = transport._session_options()  # noqa: SLF001
    session = aiohttp.ClientSession(
        connector=connector,
        connector_owner=True,
        **{**options, "middlewares": (*options["middlewares"], *extra_middlewares)},
    )
    return session, connector


def make_real_aiohttp_ws(
    read: Callable[[], Awaitable[WSMessage]],
    *,
    autoclose: bool = False,
    autoping: bool = False,
) -> tuple[ClientWebSocketResponse, RealWsWriter, RealWsResponse]:
    loop = asyncio.get_running_loop()
    writer = RealWsWriter()
    response = RealWsResponse()
    ws = ClientWebSocketResponse(
        RealWsReader(read),  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        None,
        response,  # type: ignore[arg-type]
        aiohttp.ClientWSTimeout(ws_receive=5, ws_close=0.5),
        autoclose=autoclose,
        autoping=autoping,
        loop=loop,
        heartbeat=None,
        compress=0,
    )
    return ws, writer, response


async def connect_with_real_ws(
    read: Callable[[], Awaitable[WSMessage]],
) -> tuple[AioBoundedTransport, RealWsWriter, RealWsSession, RealWsResponse]:
    ws, writer, response = make_real_aiohttp_ws(read)
    session = RealWsSession(ws)
    transport = AioBoundedTransport(injected_session=session)
    await transport.connect()
    assert session.calls == [("WS", WS_URL, session.calls[0][2])]
    assert "allow_redirects" not in session.calls[0][2]
    return transport, writer, session, response


def test_receive_eof_internal_close_is_counted_once() -> None:
    async def eof() -> WSMessage:
        raise EofStream

    async def exercise() -> None:
        transport, writer, session, response = await connect_with_real_ws(eof)

        event = await transport.poll(1_000_000)
        assert event is not None
        assert event.kind == "disconnect"

        assert writer.closes == [(1000, b"")]
        assert transport.control_counts["outbound_close"] == 1
        assert transport.control_counts["outbound_total"] == 1

        await transport.close()
        assert writer.closes == [(1000, b"")]
        assert session.closed is True
        assert response.close_calls == 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (WebSocketError(1002, "synthetic protocol error"), 1002),
        (RuntimeError("synthetic reader failure"), 1000),
    ],
)
def test_receive_error_internal_close_is_counted_before_adapter_raises(
    failure: BaseException, expected_code: int
) -> None:
    async def fail() -> WSMessage:
        raise failure

    async def exercise() -> None:
        transport, writer, session, response = await connect_with_real_ws(fail)

        with pytest.raises(TransportScopeError, match="WS_ERROR_FRAME"):
            await transport.poll(1_000_000)

        assert writer.closes == [(expected_code, b"")]
        assert transport.control_counts["outbound_close"] == 1
        assert transport.control_counts["outbound_total"] == 1

        await transport.close()
        assert writer.closes == [(expected_code, b"")]
        assert session.closed is True
        assert response.close_calls == 1

    asyncio.run(exercise())


def test_received_close_frame_does_not_send_unaccounted_close_when_autoclose_disabled() -> None:
    async def close_frame() -> WSMessage:
        return WSMessage(WSMsgType.CLOSE, 1001, "server going away")

    async def exercise() -> None:
        transport, writer, session, response = await connect_with_real_ws(close_frame)

        event = await transport.poll(1_000_000)
        assert event is not None
        assert event.kind == "close"

        assert writer.closes == []
        assert transport.control_counts["inbound_close"] == 1
        assert transport.control_counts["outbound_close"] == 0
        assert response.close_calls == 0

        await transport.close()
        assert writer.closes == [(1000, b"")]
        assert transport.control_counts["outbound_close"] == 1
        assert session.closed is True

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("stage", "error_type", "expected_scope"),
    [
        ("rest", aiohttp.ClientOSError, "REST_NETWORK_ERROR"),
        ("rest", aiohttp.ServerDisconnectedError, "REST_NETWORK_ERROR"),
        ("ws", aiohttp.ClientOSError, "WS_NETWORK_ERROR"),
        ("ws", aiohttp.ServerDisconnectedError, "WS_NETWORK_ERROR"),
    ],
)
def test_real_client_session_retry_loop_reenters_middleware_only_once(
    stage: str, error_type: type[BaseException], expected_scope: str
) -> None:
    async def exercise() -> None:
        wrapped = WrappedClientSession()
        transport = AioBoundedTransport(injected_session=wrapped)
        extra_entries: list[str] = []

        async def disconnect(request: Any, handler: Any) -> Any:
            extra_entries.append(str(request.url))
            raise error_type("synthetic retry path")

        session, connector = make_real_client_session(
            transport,
            status=200,
            extra_middlewares=(disconnect,),
        )
        wrapped.session = session
        try:
            with pytest.raises(TransportScopeError, match=expected_scope):
                if stage == "rest":
                    await transport.metadata()
                else:
                    await transport.connect()
        finally:
            await transport.close()

        expected_url = REST_URL if stage == "rest" else WS_URL
        assert extra_entries == [expected_url]
        assert transport.request_attempts == {
            "rest": 1 if stage == "rest" else 0,
            "ws": 1 if stage == "ws" else 0,
        }
        assert transport.blocked_reentries == 0
        assert connector.connect_calls == 0

    asyncio.run(exercise())


def test_real_ws_connect_redirect_trace_is_blocked_before_following_location() -> None:
    async def exercise() -> None:
        wrapped = WrappedClientSession()
        transport = AioBoundedTransport(injected_session=wrapped)
        session, connector = make_real_client_session(transport, status=302)
        wrapped.session = session
        try:
            with pytest.raises(TransportScopeError, match="REDIRECT_REJECTED:302"):
                await transport.connect()
        finally:
            await transport.close()

        assert transport.request_attempts == {"rest": 0, "ws": 1}
        assert transport.blocked_reentries == 0
        assert connector.connect_calls == 1

    asyncio.run(exercise())


def test_run_cleanup_closes_session_after_aiohttp_internal_close(tmp_path: Path) -> None:
    async def eof() -> WSMessage:
        raise EofStream

    async def exercise() -> None:
        ws, low_level_writer, response = make_real_aiohttp_ws(eof)
        session = RealWsSession(ws)
        transport = MetadataOnlyAioBoundedTransport(injected_session=session)
        clock = FakeClock()
        writer = RecordingWriter(clock)

        result = await run_test_only_bounded_live(
            bounded_config(tmp_path, observation_seconds=1),
            transport=transport,
            clock=clock,
            writer=writer,
        )

        assert result["stop_reason"] == "DISCONNECT"
        assert low_level_writer.closes == [(1000, b"")]
        assert transport.control_counts["outbound_close"] == 1
        assert session.closed is True
        assert writer.closed is True
        assert response.close_calls == 1

    asyncio.run(exercise())
