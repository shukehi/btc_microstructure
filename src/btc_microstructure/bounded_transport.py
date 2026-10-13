"""Injected transport contracts for R2-04 bounded offline live tests."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
from typing import Any, Literal, Protocol

from .probe import REST_URL, WS_URL, ProbeError, object_json


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


class RealNetworkTransport:
    """Fail-closed placeholder; Issue #21 does not authorize real Binance access."""

    def __init__(self, *_: Any, **__: Any) -> None:
        raise TransportScopeError(
            "R2-04 real Binance transport is blocked; inject TEST_ONLY transport instead"
        )


def expected_routes() -> dict[str, str]:
    return {
        "rest_url": REST_URL,
        "ws_url": WS_URL,
        "stream": "btcusdt@aggTrade",
    }
