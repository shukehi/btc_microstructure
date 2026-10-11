"""Fixed public endpoints only; lazy session, no credentials, redirects or retries."""

import asyncio
from typing import Any

import aiohttp

from .probe import AccessError, ProbeLimits, Reply, REST_URL, WS_URL


class AioTransport:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None

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
            )
        return self.session

    async def metadata(self, limits: ProbeLimits) -> Reply:
        session = await self._get_session(limits)
        try:
            async with session.get(REST_URL, allow_redirects=False) as response:
                body = bytearray()
                # At most one extra byte is read to detect truncation; no unbounded read.
                while len(body) <= limits.max_bytes:
                    chunk = await response.content.read(
                        min(16384, limits.max_bytes + 1 - len(body))
                    )
                    if not chunk:
                        break
                    body.extend(chunk)
                return Reply(response.status, dict(response.headers), bytes(body))
        except aiohttp.ClientError as exc:
            raise AccessError("REST_NETWORK_ERROR") from exc

    async def connect(self, limits: ProbeLimits) -> Reply:
        session = await self._get_session(limits)
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
        except aiohttp.ClientError as exc:
            raise AccessError("WS_NETWORK_ERROR") from exc

    async def receive(self) -> bytes:
        if self.ws is None:
            raise AccessError("WS_NOT_CONNECTED")
        try:
            message = await self.ws.receive()
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
