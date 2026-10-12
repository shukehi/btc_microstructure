"""Fixed public endpoints only; lazy session, no credentials, redirects or retries."""

import asyncio
from dataclasses import replace
import time
from typing import Any

import aiohttp

from .probe import AccessError, ProbeLimits, Reply, REST_URL, WS_URL, budget_headers


class AioTransport:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._request_attempts = {"rest": 0, "ws": 0}
        self._blocked_reentries = 0
        self._metadata_reply: Reply | None = None
        self._metadata_body = bytearray()
        self._metadata_phase = "NOT_STARTED"
        self._timeout_diagnostic: dict[str, str] | None = None

    @property
    def request_attempts(self) -> dict[str, int]:
        """Allowed request-handler entries, not claimed socket/wire send counts."""
        return dict(self._request_attempts)

    @property
    def blocked_reentries(self) -> int:
        return self._blocked_reentries

    @property
    def metadata_observation(self) -> Reply | None:
        """Last bounded header/body snapshot, including after caller cancellation."""
        if self._metadata_reply is None:
            return None
        return replace(self._metadata_reply, body=bytes(self._metadata_body))

    @property
    def timeout_diagnostic(self) -> dict[str, str] | None:
        """Safe timeout provenance from public aiohttp exception classes only."""
        return None if self._timeout_diagnostic is None else dict(self._timeout_diagnostic)

    @property
    def metadata_phase(self) -> str:
        """Current REST metadata phase without transport exception attribution."""
        return self._metadata_phase

    async def _single_attempt(
        self, request: aiohttp.ClientRequest, handler: aiohttp.ClientHandlerType
    ) -> aiohttp.ClientResponse:
        url = str(request.url)
        if request.method != "GET" or url not in (REST_URL, WS_URL):
            raise AccessError("UNEXPECTED_REQUEST_SCOPE")
        stage = "rest" if url == REST_URL else "ws"
        if self._request_attempts[stage] >= 1:
            self._blocked_reentries += 1
            raise AccessError("IMPLICIT_RETRY_BLOCKED")
        self._request_attempts[stage] += 1
        try:
            return await handler(request)
        except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError) as exc:
            # Public middleware boundary: these errors otherwise trigger aiohttp's
            # retry of idempotent GETs. AccessError is NOT an aiohttp.ClientError.
            raise AccessError(
                "REST_NETWORK_ERROR" if stage == "rest" else "WS_NETWORK_ERROR"
            ) from exc

    async def _get_session(self, limits: ProbeLimits) -> aiohttp.ClientSession:
        if self.session is None:
            trace = aiohttp.TraceConfig()

            async def reject_redirect(session: Any, context: Any, params: Any) -> None:
                raise AccessError(
                    "REDIRECT_REJECTED",
                    Reply(params.response.status, dict(params.response.headers)),
                )

            trace.on_request_redirect.append(reject_redirect)
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=limits.connect_seconds,
                    connect=limits.connect_seconds,
                    sock_connect=limits.connect_seconds,
                    sock_read=limits.idle_seconds,
                ),
                headers={"Accept-Encoding": "identity"},
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                trace_configs=[trace],
                auto_decompress=False,
                middlewares=(self._single_attempt,),
            )
        return self.session

    async def metadata(self, limits: ProbeLimits) -> Reply:
        session = await self._get_session(limits)
        self._metadata_reply = None
        self._metadata_body.clear()
        self._metadata_phase = "REST_WAIT_HEADERS_OR_CONNECT"
        self._timeout_diagnostic = None
        request_options: dict[str, Any] = {"allow_redirects": False}
        if limits.rest_total_seconds is not None:
            request_options["timeout"] = aiohttp.ClientTimeout(
                total=limits.rest_total_seconds,
                connect=limits.connect_seconds,
                sock_connect=limits.connect_seconds,
                sock_read=limits.idle_seconds,
            )
        try:
            async with session.get(REST_URL, **request_options) as response:
                self._metadata_phase = "REST_READ_BODY"
                self._metadata_reply = Reply(
                    response.status,
                    budget_headers(dict(response.headers)),
                    body_complete=False,
                    headers_received_ns=time.time_ns(),
                    body_read_state="READING",
                )
                # Rejection is already conclusive; do not wait on an untrusted body.
                if response.status != 200:
                    self._metadata_reply = replace(
                        self._metadata_reply, body_read_state="NOT_READ_HTTP_REJECTION"
                    )
                    self._metadata_phase = "HTTP_REJECTION"
                    observation = self.metadata_observation
                    assert observation is not None
                    return observation
                while len(self._metadata_body) <= limits.max_bytes:
                    chunk = await response.content.read(
                        min(16384, limits.max_bytes + 1 - len(self._metadata_body))
                    )
                    if not chunk:
                        self._metadata_reply = replace(
                            self._metadata_reply, body_complete=True, body_read_state="COMPLETE"
                        )
                        break
                    self._metadata_body.extend(chunk)
                if len(self._metadata_body) > limits.max_bytes:
                    self._metadata_reply = replace(
                        self._metadata_reply, body_read_state="BYTE_LIMIT"
                    )
                observation = self.metadata_observation
                assert observation is not None
                return observation
        except aiohttp.ConnectionTimeoutError:
            self._timeout_diagnostic = {
                "phase": "BODY" if self._metadata_phase == "REST_READ_BODY" else "REQUEST",
                "source": "AIOHTTP_CONNECT_TIMEOUT",
            }
            raise TimeoutError
        except aiohttp.SocketTimeoutError:
            self._timeout_diagnostic = {
                "phase": "BODY" if self._metadata_phase == "REST_READ_BODY" else "REQUEST",
                "source": "AIOHTTP_SOCKET_READ_TIMEOUT",
            }
            raise TimeoutError
        except TimeoutError:
            self._timeout_diagnostic = {
                "phase": "BODY" if self._metadata_phase == "REST_READ_BODY" else "REQUEST",
                "source": "PYTHON_TIMEOUT_UNKNOWN",
            }
            raise
        except aiohttp.ClientError as exc:
            if self._metadata_reply is not None:
                self._metadata_reply = replace(self._metadata_reply, body_read_state="INTERRUPTED")
            raise AccessError("REST_NETWORK_ERROR", self.metadata_observation) from exc
        finally:
            if (
                self._metadata_reply is not None
                and self._metadata_reply.body_read_state == "READING"
            ):
                self._metadata_reply = replace(self._metadata_reply, body_read_state="INTERRUPTED")

    async def connect(self, limits: ProbeLimits) -> Reply:
        session = await self._get_session(limits)
        self._timeout_diagnostic = None
        try:
            async with asyncio.timeout(limits.connect_seconds):
                self.ws = await session.ws_connect(
                    WS_URL,
                    timeout=aiohttp.ClientWSTimeout(ws_receive=limits.idle_seconds, ws_close=0.5),
                    max_msg_size=limits.max_frame_bytes,
                    autoping=True,
                    heartbeat=None,
                    compress=0,
                )
            # Handshake headers are not exposed by aiohttp's public WS API.
            return Reply(101, {})
        except aiohttp.WSServerHandshakeError as exc:
            raise AccessError(
                "WS_HANDSHAKE_REJECTED", Reply(exc.status, dict(exc.headers or {}))
            ) from exc
        except aiohttp.ConnectionTimeoutError:
            self._timeout_diagnostic = {
                "phase": "WS_CONNECT",
                "source": "AIOHTTP_CONNECT_TIMEOUT",
            }
            raise TimeoutError
        except aiohttp.SocketTimeoutError:
            self._timeout_diagnostic = {
                "phase": "WS_CONNECT",
                "source": "AIOHTTP_SOCKET_READ_TIMEOUT",
            }
            raise TimeoutError
        except TimeoutError:
            self._timeout_diagnostic = {
                "phase": "WS_CONNECT",
                "source": "PYTHON_TIMEOUT_UNKNOWN",
            }
            raise
        except aiohttp.ClientError as exc:
            raise AccessError("WS_NETWORK_ERROR") from exc

    async def receive(self) -> bytes:
        if self.ws is None:
            raise AccessError("WS_NOT_CONNECTED")
        self._timeout_diagnostic = None
        try:
            message = await self.ws.receive()
        except aiohttp.ConnectionTimeoutError:
            self._timeout_diagnostic = {
                "phase": "WS_RECEIVE",
                "source": "AIOHTTP_CONNECT_TIMEOUT",
            }
            raise TimeoutError
        except aiohttp.SocketTimeoutError:
            self._timeout_diagnostic = {
                "phase": "WS_RECEIVE",
                "source": "AIOHTTP_SOCKET_READ_TIMEOUT",
            }
            raise TimeoutError
        except TimeoutError:
            self._timeout_diagnostic = {
                "phase": "WS_RECEIVE",
                "source": "PYTHON_TIMEOUT_UNKNOWN",
            }
            raise
        except aiohttp.ClientError as exc:
            raise AccessError("WS_RECEIVE_ERROR") from exc
        if message.type == aiohttp.WSMsgType.TEXT:
            return str(message.data).encode("utf-8")
        raise AccessError("WS_CLOSED_BINARY_OR_PROTOCOL_ERROR")

    async def close(self) -> None:
        try:
            if self.ws is not None:
                await self.ws.close()
        finally:
            if self.session is not None:
                await self.session.close()
