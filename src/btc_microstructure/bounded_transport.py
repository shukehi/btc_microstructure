"""Injected transport contracts for R2-04 bounded offline live tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import base64
import hashlib
import json
import time
from typing import Any, Callable, Literal, Protocol

import aiohttp

from .probe import REST_URL, WS_URL, ProbeError, budget_headers, object_json

REST_TIMEOUT_SECONDS = 15.0
CONNECT_TIMEOUT_SECONDS = 5.0
IDLE_TIMEOUT_SECONDS = 5.0
REST_BODY_BYTES = 2_097_152
WS_FRAME_BYTES = 65_536
OUTBOUND_CONTROL_TOTAL = 64
OUTBOUND_NORMAL_TOTAL = 63
OUTBOUND_ANY_PER_SECOND = 2
OUTBOUND_NORMAL_PER_SECOND = 1
OUTBOUND_CONTROL_PAYLOAD_BYTES = 125
REST_TIMEOUT = aiohttp.ClientTimeout(
    total=REST_TIMEOUT_SECONDS,
    connect=CONNECT_TIMEOUT_SECONDS,
    sock_connect=CONNECT_TIMEOUT_SECONDS,
    sock_read=IDLE_TIMEOUT_SECONDS,
)


def payload_for_core(payload: bytes) -> tuple[str, list[str]]:
    """A parse failure never constructs an apparently valid economic event."""
    try:
        text = payload.decode("utf-8")
        object_json(payload)
        return text, []
    except (UnicodeError, ProbeError):
        return json.dumps(
            {
                "invalid_payload_base64": base64.b64encode(payload).decode(),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            sort_keys=True,
        ), ["SOURCE_SCHEMA_CHANGED"]


Direction = Literal["inbound", "outbound"]


@dataclass(frozen=True)
class MetadataReply:
    status: int
    headers: dict[str, str]
    body: bytes
    body_complete: bool = True


@dataclass(frozen=True)
class WsEvent:
    kind: Literal["message", "ping", "pong", "close", "disconnect", "tick"]
    payload: bytes = b""


class BoundedTransport(Protocol):
    async def metadata(self) -> MetadataReply: ...
    async def connect(self) -> None: ...
    async def poll(self, timeout_ns: int) -> WsEvent | None: ...
    async def send_control(self, kind: Literal["pong", "close"], payload: bytes = b"") -> None: ...
    async def close(self) -> None: ...


class TransportScopeError(RuntimeError):
    """Raised before any physical network adapter can be accepted by this task."""


SessionFactory = Callable[..., Any]


def _payload_bytes(data: Any) -> bytes:
    if data is None:
        return b""
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    raise TransportScopeError("WS_CONTROL_PAYLOAD_UNSUPPORTED")


def _close_frame_payload(code: Any, reason: Any) -> bytes:
    if code is None:
        prefix = b""
    elif isinstance(code, int):
        if code < 0 or code > 65_535:
            raise TransportScopeError("WS_CLOSE_CODE_UNSUPPORTED")
        prefix = code.to_bytes(2, "big")
    else:
        prefix = _payload_bytes(code)
    return prefix + _payload_bytes(reason)


@dataclass
class _ControlCounters:
    inbound_ping: int = 0
    inbound_pong: int = 0
    inbound_close: int = 0
    outbound_pong: int = 0
    outbound_close: int = 0
    outbound_payload_bytes: int = 0
    outbound_send_times: list[tuple[int, Literal["pong", "close"]]] | None = None

    @property
    def outbound_total(self) -> int:
        return self.outbound_pong + self.outbound_close


class AioBoundedTransport:
    """Physical aiohttp contract with fake-session injection for offline tests only."""

    TEST_ONLY = True
    REST_TIMEOUT = REST_TIMEOUT

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        injected_session: Any | None = None,
    ) -> None:
        if (session_factory is None) == (injected_session is None):
            raise TransportScopeError(
                "R2-04 real aiohttp construction is blocked; inject exactly one TEST_ONLY fake"
            )
        if session_factory is aiohttp.ClientSession or isinstance(
            injected_session, aiohttp.ClientSession
        ):
            raise TransportScopeError("REAL_AIOHTTP_SESSION_REJECTED")
        if session_factory is not None and getattr(session_factory, "TEST_ONLY", False) is not True:
            raise TransportScopeError("session_factory: explicit TEST_ONLY marker required")
        if (
            injected_session is not None
            and getattr(injected_session, "TEST_ONLY", False) is not True
        ):
            raise TransportScopeError("injected_session: explicit TEST_ONLY marker required")
        self._session_factory = session_factory
        self._session: Any | None = injected_session
        self._ws: Any | None = None
        self._request_attempts = {"rest": 0, "ws": 0}
        self._blocked_reentries = 0
        self._middleware_seen = {"rest": False, "ws": False}
        self._inflight_stage: Literal["rest", "ws"] | None = None
        self._controls = _ControlCounters()
        self._controls.outbound_send_times = []
        self._close_pre_reserved = False
        self._pong_pre_reserved = False
        self._metadata_observation: MetadataReply | None = None

    @property
    def request_attempts(self) -> dict[str, int]:
        return dict(self._request_attempts)

    @property
    def blocked_reentries(self) -> int:
        return self._blocked_reentries

    @property
    def control_counts(self) -> dict[str, int]:
        return {
            "inbound_ping": self._controls.inbound_ping,
            "inbound_pong": self._controls.inbound_pong,
            "inbound_close": self._controls.inbound_close,
            "outbound_pong": self._controls.outbound_pong,
            "outbound_close": self._controls.outbound_close,
            "outbound_total": self._controls.outbound_total,
            "outbound_payload_bytes": self._controls.outbound_payload_bytes,
        }

    @property
    def metadata_observation(self) -> MetadataReply | None:
        return self._metadata_observation

    def _claim_attempt(self, stage: Literal["rest", "ws"]) -> None:
        if self._request_attempts[stage] >= 1:
            self._blocked_reentries += 1
            raise TransportScopeError("IMPLICIT_RETRY_BLOCKED")
        self._request_attempts[stage] += 1
        self._middleware_seen[stage] = False
        self._inflight_stage = stage

    async def _single_attempt(
        self, request: aiohttp.ClientRequest, handler: aiohttp.ClientHandlerType
    ) -> aiohttp.ClientResponse:
        url = str(request.url)
        if request.method != "GET" or url not in (REST_URL, WS_URL):
            raise TransportScopeError("UNEXPECTED_REQUEST_SCOPE")
        stage = "rest" if url == REST_URL else "ws"
        if self._inflight_stage == stage and not self._middleware_seen[stage]:
            self._middleware_seen[stage] = True
        elif self._request_attempts[stage] == 0:
            self._request_attempts[stage] += 1
            self._middleware_seen[stage] = True
        else:
            self._blocked_reentries += 1
            raise TransportScopeError("IMPLICIT_RETRY_BLOCKED")
        try:
            return await handler(request)
        except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError) as exc:
            raise TransportScopeError(f"{stage.upper()}_NETWORK_ERROR") from exc

    def _session_options(self) -> dict[str, Any]:
        trace = aiohttp.TraceConfig()

        async def reject_redirect(session: Any, context: Any, params: Any) -> None:
            raise TransportScopeError(f"REDIRECT_REJECTED:{params.response.status}")

        trace.on_request_redirect.append(reject_redirect)
        return {
            "timeout": self.REST_TIMEOUT,
            "headers": {"Accept-Encoding": "identity"},
            "trust_env": False,
            "cookie_jar": aiohttp.DummyCookieJar(),
            "trace_configs": [trace],
            "auto_decompress": False,
            "middlewares": (self._single_attempt,),
        }

    async def _get_session(self) -> Any:
        if self._session is None:
            assert self._session_factory is not None
            self._session = self._session_factory(**self._session_options())
            if isinstance(self._session, aiohttp.ClientSession):
                raise TransportScopeError("REAL_AIOHTTP_SESSION_REJECTED")
        return self._session

    async def metadata(self) -> MetadataReply:
        self._claim_attempt("rest")
        session = await self._get_session()
        body = bytearray()
        status: int | None = None
        headers: dict[str, str] = {}
        complete = False
        try:
            async with asyncio.timeout(REST_TIMEOUT_SECONDS):
                async with session.get(
                    REST_URL,
                    allow_redirects=False,
                    timeout=self.REST_TIMEOUT,
                ) as response:
                    status = int(response.status)
                    headers = budget_headers(dict(response.headers))
                    self._metadata_observation = MetadataReply(
                        status=status,
                        headers=headers,
                        body=b"",
                        body_complete=False,
                    )
                    while len(body) <= REST_BODY_BYTES:
                        chunk = await response.content.read(
                            min(16_384, REST_BODY_BYTES + 1 - len(body))
                        )
                        if not chunk:
                            complete = True
                            break
                        body.extend(chunk)
                        self._metadata_observation = MetadataReply(
                            status=status,
                            headers=headers,
                            body=bytes(body),
                            body_complete=False,
                        )
        except (aiohttp.ConnectionTimeoutError, aiohttp.SocketTimeoutError, TimeoutError):
            raise TimeoutError
        except aiohttp.ClientError as exc:
            if status is None:
                raise TransportScopeError("REST_NETWORK_ERROR") from exc
            complete = False
        finally:
            self._inflight_stage = None
        if status is None:
            raise TransportScopeError("REST_NO_RESPONSE")
        self._metadata_observation = MetadataReply(
            status=status,
            headers=headers,
            body=bytes(body),
            body_complete=complete,
        )
        return self._metadata_observation

    async def connect(self) -> None:
        self._claim_attempt("ws")
        session = await self._get_session()
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                self._ws = await session.ws_connect(
                    WS_URL,
                    timeout=aiohttp.ClientWSTimeout(
                        ws_receive=IDLE_TIMEOUT_SECONDS,
                        ws_close=0.5,
                    ),
                    max_msg_size=WS_FRAME_BYTES,
                    autoping=False,
                    autoclose=False,
                    heartbeat=None,
                    compress=0,
                )
            self._instrument_ws_control_boundaries()
        except (aiohttp.ConnectionTimeoutError, aiohttp.SocketTimeoutError, TimeoutError):
            raise TimeoutError
        except aiohttp.WSServerHandshakeError as exc:
            if exc.status in {301, 302, 303, 307, 308}:
                raise TransportScopeError(f"REDIRECT_REJECTED:{exc.status}") from exc
            raise TransportScopeError(f"WS_HANDSHAKE_REJECTED:{exc.status}") from exc
        except aiohttp.ClientError as exc:
            raise TransportScopeError("WS_NETWORK_ERROR") from exc
        finally:
            self._inflight_stage = None

    def _instrument_ws_control_boundaries(self) -> None:
        if self._ws is None:
            return
        original_close = self._ws.close
        original_pong = self._ws.pong

        async def counted_close(
            *,
            code: int = 1000,
            message: bytes = b"",
        ) -> Any:
            if not self._close_pre_reserved:
                self._reserve_outbound_control("close", message)
            return await original_close(code=code, message=message)

        async def counted_pong(payload: bytes = b"") -> Any:
            if not self._pong_pre_reserved:
                self._reserve_outbound_control("pong", payload)
            return await original_pong(payload)

        self._ws.close = counted_close
        self._ws.pong = counted_pong

    async def poll(self, timeout_ns: int) -> WsEvent | None:
        if self._ws is None:
            raise TransportScopeError("WS_NOT_CONNECTED")
        timeout_seconds = max(0.0, timeout_ns / 1_000_000_000)
        try:
            message = await self._ws.receive(timeout=timeout_seconds)
        except (aiohttp.ConnectionTimeoutError, aiohttp.SocketTimeoutError, TimeoutError):
            raise TimeoutError
        except aiohttp.ClientError as exc:
            raise TransportScopeError("WS_RECEIVE_ERROR") from exc
        message_type = getattr(message, "type", None)
        if message_type == aiohttp.WSMsgType.TEXT:
            payload = _payload_bytes(message.data)
            if len(payload) > WS_FRAME_BYTES:
                raise TransportScopeError("WS_FRAME_BYTE_LIMIT")
            return WsEvent(kind="message", payload=payload)
        if message_type == aiohttp.WSMsgType.BINARY:
            payload = _payload_bytes(message.data)
            if len(payload) > WS_FRAME_BYTES:
                raise TransportScopeError("WS_FRAME_BYTE_LIMIT")
            return WsEvent(kind="message", payload=payload)
        if message_type == aiohttp.WSMsgType.PING:
            payload = _payload_bytes(message.data)
            self._controls.inbound_ping += 1
            return WsEvent(kind="ping", payload=payload)
        if message_type == aiohttp.WSMsgType.PONG:
            payload = _payload_bytes(message.data)
            self._controls.inbound_pong += 1
            return WsEvent(kind="pong", payload=payload)
        if message_type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING}:
            payload = _close_frame_payload(
                getattr(message, "data", None),
                getattr(message, "extra", None),
            )
            self._controls.inbound_close += 1
            return WsEvent(kind="close", payload=payload)
        if message_type == aiohttp.WSMsgType.CLOSED:
            return WsEvent(kind="disconnect")
        if message_type == aiohttp.WSMsgType.ERROR:
            self._controls.inbound_close += 1
            raise TransportScopeError("WS_ERROR_FRAME")
        raise TransportScopeError("WS_UNSUPPORTED_FRAME")

    def _reserve_outbound_control(self, kind: Literal["pong", "close"], payload: bytes) -> None:
        effective_payload_size = len(payload) + (2 if kind == "close" else 0)
        if effective_payload_size > OUTBOUND_CONTROL_PAYLOAD_BYTES:
            raise TransportScopeError("OUTBOUND_CONTROL_PAYLOAD_LIMIT")
        now_ns = time.monotonic_ns()
        assert self._controls.outbound_send_times is not None
        cutoff = now_ns - 1_000_000_000
        self._controls.outbound_send_times = [
            (stamp, sent_kind)
            for stamp, sent_kind in self._controls.outbound_send_times
            if stamp > cutoff
        ]
        recent_any = len(self._controls.outbound_send_times)
        recent_normal = sum(
            1 for _, sent_kind in self._controls.outbound_send_times if sent_kind == "pong"
        )
        if recent_any + 1 > OUTBOUND_ANY_PER_SECOND:
            raise TransportScopeError("OUTBOUND_CONTROL_RATE_LIMIT")
        if kind == "pong":
            if recent_normal + 1 > OUTBOUND_NORMAL_PER_SECOND:
                raise TransportScopeError("OUTBOUND_CONTROL_RATE_LIMIT")
            if self._controls.outbound_pong + 1 > OUTBOUND_NORMAL_TOTAL:
                raise TransportScopeError("OUTBOUND_CONTROL_TOTAL_LIMIT")
            self._controls.outbound_pong += 1
        else:
            if self._controls.outbound_close:
                raise TransportScopeError("OUTBOUND_CLOSE_ALREADY_USED")
            if self._controls.outbound_total + 1 > OUTBOUND_CONTROL_TOTAL:
                raise TransportScopeError("OUTBOUND_CONTROL_TOTAL_LIMIT")
            self._controls.outbound_close += 1
        self._controls.outbound_payload_bytes += len(payload)
        self._controls.outbound_send_times.append((now_ns, kind))

    async def send_control(self, kind: Literal["pong", "close"], payload: bytes = b"") -> None:
        if self._ws is None:
            raise TransportScopeError("WS_NOT_CONNECTED")
        if kind == "pong":
            self._reserve_outbound_control(kind, payload)
            self._pong_pre_reserved = True
            try:
                await self._ws.pong(payload)
            finally:
                self._pong_pre_reserved = False
            return
        if kind == "close":
            self._reserve_outbound_control(kind, payload)
            self._close_pre_reserved = True
            try:
                await self._ws.close(message=payload)
            finally:
                self._close_pre_reserved = False
            return
        raise TransportScopeError("OUTBOUND_CONTROL_KIND_UNSUPPORTED")

    async def close(self) -> None:
        try:
            if self._ws is not None and not self._controls.outbound_close:
                await self.send_control("close", b"")
        finally:
            if self._session is not None:
                await self._session.close()


class RealNetworkTransport:
    """Fail-closed placeholder; Issue #21 does not authorize real Binance access."""

    REST_TIMEOUT = REST_TIMEOUT

    def __init__(self, *_: Any, **__: Any) -> None:
        raise TransportScopeError(
            "R2-04 real Binance transport is blocked; inject TEST_ONLY transport instead"
        )

    @classmethod
    def for_test_only_injected_session(cls, session: Any) -> AioBoundedTransport:
        if isinstance(session, aiohttp.ClientSession):
            raise TransportScopeError("REAL_AIOHTTP_SESSION_REJECTED")
        if getattr(session, "TEST_ONLY", False) is not True:
            raise TransportScopeError("injected_session: explicit TEST_ONLY marker required")
        return AioBoundedTransport(injected_session=session)


def expected_routes() -> dict[str, str]:
    return {
        "rest_url": REST_URL,
        "ws_url": WS_URL,
        "stream": "btcusdt@aggTrade",
    }
