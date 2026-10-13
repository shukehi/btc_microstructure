"""Bounded protocol inspection only: no collector, normalizer or research runtime."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

REST_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
WS_URL = "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade"
STREAM = "btcusdt@aggTrade"


class ProbeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    total_seconds: float = Field(default=30.0, ge=2, le=60, allow_inf_nan=False)
    connect_seconds: float = Field(default=5.0, gt=0, le=10, allow_inf_nan=False)
    rest_total_seconds: float | None = Field(default=None, gt=0, le=20, allow_inf_nan=False)
    idle_seconds: float = Field(default=5.0, gt=0, le=10, allow_inf_nan=False)
    max_messages: int = Field(default=20, ge=1, le=20)
    max_bytes: int = Field(default=2097152, ge=2048, le=2097152)
    max_frame_bytes: int = Field(default=65536, ge=512, le=65536)


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes = b""
    body_complete: bool = True
    headers_received_ns: int | None = None
    body_read_state: str = "COMPLETE"


class Transport(Protocol):
    async def metadata(self, limits: ProbeLimits) -> Reply: ...
    async def connect(self, limits: ProbeLimits) -> Reply: ...
    async def receive(self) -> bytes: ...
    async def close(self) -> None: ...


class ProbeError(ValueError):
    """Safe field-path protocol error, never a raw transport exception."""


class AccessError(Exception):
    def __init__(self, reason: str, reply: Reply | None = None):
        self.reason = reason
        self.reply = reply
        super().__init__(reason)


def object_json(body: bytes) -> dict[str, Any]:
    def reject(value: str) -> None:
        raise ProbeError("json: nonfinite number")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProbeError("json: duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(body, parse_constant=reject, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProbeError("json: invalid object encoding or duplicate/nonfinite fields") from exc
    if not isinstance(value, dict):
        raise ProbeError("json: expected object")
    return value


def integer(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ProbeError(f"{field}: expected nonnegative integer")
    return value


def decimal_string(value: Any, field: str, allow_zero: bool = False) -> str:
    if not isinstance(value, str):
        raise ProbeError(f"{field}: expected exact decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ProbeError(f"{field}: invalid decimal string") from exc
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise ProbeError(f"{field}: expected finite positive value")
    return value


def inspect_metadata(body: bytes) -> dict[str, Any]:
    raw = object_json(body)
    symbols = raw.get("symbols")
    if not isinstance(symbols, list) or not all(isinstance(s, dict) for s in symbols):
        raise ProbeError("symbols: expected object array")
    matches = [s for s in symbols if s.get("symbol") == "BTCUSDT"]
    if len(matches) != 1:
        raise ProbeError("symbols.BTCUSDT: expected exactly one instrument")
    symbol = matches[0]
    expected = {
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "contractType": "PERPETUAL",
        "status": "TRADING",
    }
    for key, value in expected.items():
        if symbol.get(key) != value:
            raise ProbeError(f"symbols.BTCUSDT.{key}: unexpected or unknown instrument state")
    filters = symbol.get("filters")
    if not isinstance(filters, list) or not all(isinstance(f, dict) for f in filters):
        raise ProbeError("symbols.BTCUSDT.filters: expected object array")
    result: dict[str, Any] = {"symbol": "BTCUSDT", **expected, "quantity_unit": "UNKNOWN"}
    for kind, fields in {"PRICE_FILTER": ["tickSize"], "LOT_SIZE": ["stepSize", "minQty"]}.items():
        found = [f for f in filters if f.get("filterType") == kind]
        if len(found) != 1:
            raise ProbeError(f"filters.{kind}: expected exactly one filter")
        for field in fields:
            result[field] = decimal_string(found[0].get(field), f"filters.{kind}.{field}")
    limits = raw.get("rateLimits")
    if not isinstance(limits, list):
        raise ProbeError("rateLimits: expected array")
    observed = []
    for entry in limits:
        if not isinstance(entry, dict) or not isinstance(entry.get("rateLimitType"), str):
            raise ProbeError("rateLimits: invalid rateLimitType")
        if entry.get("interval") not in ("SECOND", "MINUTE", "HOUR", "DAY"):
            raise ProbeError("rateLimits.interval: unknown interval")
        for field in ("intervalNum", "limit"):
            if integer(entry.get(field), f"rateLimits.{field}") == 0:
                raise ProbeError(f"rateLimits.{field}: must be positive")
        observed.append(
            {key: entry[key] for key in ("rateLimitType", "interval", "intervalNum", "limit")}
        )
    if not any(e["rateLimitType"] == "REQUEST_WEIGHT" for e in observed):
        raise ProbeError("rateLimits: REQUEST_WEIGHT budget not observed")
    result["rateLimits"] = observed
    return result


def inspect_trade(body: bytes, *, combined: bool = False) -> dict[str, Any]:
    raw = object_json(body)
    if combined and ("stream" not in raw or "data" not in raw):
        raise ProbeError("stream/data: combined route requires envelope")
    wrapper_extras: list[str] = []
    if "stream" in raw or "data" in raw:
        if raw.get("stream") != STREAM or not isinstance(raw.get("data"), dict):
            raise ProbeError("stream/data: unexpected combined stream envelope")
        wrapper_extras = sorted(set(raw) - {"stream", "data"})
        raw = raw["data"]
    if raw.get("e") != "aggTrade" or raw.get("s") != "BTCUSDT":
        raise ProbeError("e/s: unexpected event or symbol")
    for field in ("a", "E", "T", "f", "l"):
        integer(raw.get(field), field)
    if raw["f"] > raw["l"]:
        raise ProbeError("f/l: reversed trade ID range")
    for field in ("p", "q"):
        decimal_string(raw.get(field), field)
    if type(raw.get("m")) is not bool:
        raise ProbeError("m: expected strict boolean")
    if "st" in raw and (type(raw["st"]) is not int or raw["st"] != 1):
        raise ProbeError("st: unknown or non-UM symbol type")
    if "nq" in raw:
        decimal_string(raw["nq"], "nq", allow_zero=True)
        if Decimal(raw["nq"]) > Decimal(raw["q"]):
            raise ProbeError("nq: exceeds total quantity")
    return {
        "agg_trade_id": raw["a"],
        "event_time": raw["E"],
        "trade_time": raw["T"],
        "source_time_unit": "ms",
        "price_native": raw["p"],
        "quantity_native": raw["q"],
        "normal_quantity_native": raw.get("nq"),
        "quantity_unit": "UNKNOWN",
        "buyer_is_maker": raw["m"],
        "symbol_type_raw": raw.get("st"),
        "unknown_fields": sorted(
            set(raw) - {"e", "s", "a", "E", "T", "f", "l", "p", "q", "m", "nq", "st"}
        ),
        "unknown_wrapper_fields": wrapper_extras,
    }


def budget_headers(headers: dict[str, str]) -> dict[str, str]:
    # Exclude all cookies, auth, proxy, and unrelated server headers.
    return {
        key.lower(): value[:128]
        for key, value in headers.items()
        if key.lower().startswith("x-mbx-used-weight-") or key.lower() == "retry-after"
    }


async def run_probe(
    transport: Transport,
    limits: ProbeLimits,
    *,
    evidence_kind: Literal["SYNTHETIC", "LIVE"] = "SYNTHETIC",
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    started = time.monotonic()
    work_deadline = loop.time() + limits.total_seconds - 1
    active_timeout: dict[str, Any] = {"phase": "NOT_STARTED", "scopes": {}}
    rest_mode = "EXPLICIT" if limits.rest_total_seconds is not None else "FALLBACK_CONNECT"
    report: dict[str, Any] = {
        "schema_version": "probe-0.2",
        "evidence_kind": evidence_kind,
        "started_ns": time.time_ns(),
        "limits": limits.model_dump(),
        "configured_timeouts": {
            "total_seconds": limits.total_seconds,
            "connect_seconds": limits.connect_seconds,
            "rest_total_seconds": limits.rest_total_seconds,
            "rest_total_mode": rest_mode,
            "idle_seconds": limits.idle_seconds,
            "cleanup_seconds": 1.0,
        },
        "effective_timeouts": {
            "work_seconds": limits.total_seconds - 1,
            "work_deadline_source": "TOTAL_MINUS_CLEANUP_RESERVE",
        },
        "status": "NOT_RUN",
        "rest": {
            "status": "NOT_RUN",
            "method": "GET",
            "url": REST_URL,
            "params": {},
            "timeout_diagnostics": {"phase": "NOT_STARTED", "source": "NOT_EXPIRED"},
        },
        "ws": {
            "status": "NOT_RUN",
            "url": WS_URL,
            "subscription": "URL combined stream",
            "messages": [],
            "frames_seen": 0,
            "timeout_diagnostics": {"phase": "NOT_STARTED", "source": "NOT_EXPIRED"},
        },
        "bytes_received": 0,
        "logical_calls": {"rest": 0, "ws": 0},
        "transport_attempts": None,
        "retry_count": 0,
        "backoff_cap_seconds": 0,
        "continuous_collection_enabled": False,
    }
    stage = "rest"

    def store(reply: Reply, name: str) -> None:
        item = report[name]
        item["http_status"] = reply.status
        item["rate_headers"] = budget_headers(reply.headers)
        item["response_received_ns"] = time.time_ns()
        if reply.headers_received_ns is not None:
            item["headers_received_ns"] = reply.headers_received_ns

    def account(body: bytes) -> bool:
        remaining = limits.max_bytes - int(report["bytes_received"])
        report["bytes_received"] += len(body)
        return len(body) <= remaining

    def retain_metadata(reply: Reply) -> bool:
        store(reply, "rest")
        fits = account(reply.body)
        report["rest"]["body_hex"] = reply.body[: limits.max_bytes].hex()
        report["rest"]["body_bytes_observed"] = len(reply.body)
        report["rest"]["body_complete"] = reply.body_complete and fits
        report["rest"]["body_read_state"] = reply.body_read_state
        report["rest"]["truncated"] = not fits or not reply.body_complete
        return fits

    def retain_observation() -> None:
        # Optional additive interface: legacy injected transports still return Reply.
        if "body_hex" not in report["rest"]:
            observation = getattr(transport, "metadata_observation", None)
            if isinstance(observation, Reply):
                retain_metadata(observation)

    def deadline_cap_source(deadline: float, *candidates: tuple[str, float]) -> str:
        minimum = min(value for _, value in candidates)
        if deadline != minimum:
            return "UNKNOWN"
        tied = [name for name, value in candidates if value == minimum]
        if len(tied) == 1:
            return tied[0]
        if len(tied) > 1:
            return "SHARED_DEADLINE"
        return "UNKNOWN"

    def timeout_source(scopes: dict[str, asyncio.Timeout]) -> str:
        expired = sorted(name for name, scope in scopes.items() if scope.expired())
        if len(expired) == 1:
            return expired[0]
        if len(expired) > 1:
            return "MULTIPLE_EXPIRED"
        return "UNKNOWN"

    def timeout_diagnostic() -> dict[str, Any]:
        diagnostic = {
            "phase": active_timeout.get("phase", "UNKNOWN"),
            "source": timeout_source(active_timeout.get("scopes", {})),
        }
        transport_phase = getattr(transport, "metadata_phase", None)
        transport_diagnostic = getattr(transport, "timeout_diagnostic", None)
        if isinstance(transport_diagnostic, dict):
            diagnostic["transport_phase"] = transport_diagnostic.get("phase", "UNKNOWN")
            diagnostic["transport_source"] = transport_diagnostic.get("source", "UNKNOWN")
        elif isinstance(transport_phase, str) and stage == "rest":
            diagnostic["transport_phase"] = transport_phase
            diagnostic["transport_source"] = "UNKNOWN"
        else:
            diagnostic["transport_phase"] = "UNKNOWN"
            diagnostic["transport_source"] = "UNKNOWN"
        return diagnostic

    def active_scopes(
        primary: str,
        cap_source: str,
        primary_timeout: asyncio.Timeout,
        global_timeout: asyncio.Timeout,
    ) -> dict[str, asyncio.Timeout]:
        scopes = {"GLOBAL_WORK": global_timeout}
        if cap_source != "GLOBAL_WORK":
            scopes[primary] = primary_timeout
        return scopes

    async def perform(global_timeout: asyncio.Timeout) -> None:
        nonlocal stage
        report["rest"]["request_started_ns"] = time.time_ns()
        report["logical_calls"]["rest"] += 1
        rest_started = loop.time()
        configured_rest = (
            limits.rest_total_seconds
            if limits.rest_total_seconds is not None
            else limits.connect_seconds
        )
        rest_configured_deadline = rest_started + configured_rest
        rest_deadline = min(work_deadline, rest_configured_deadline)
        # Preserve the configured duration unless the global deadline clips it.
        # Subtracting absolute float timestamps can round beyond that duration.
        rest_effective = (
            configured_rest
            if rest_deadline == rest_configured_deadline
            else min(configured_rest, max(0.0, rest_deadline - rest_started))
        )
        report["effective_timeouts"]["rest_total_seconds"] = rest_effective
        report["effective_timeouts"]["rest_total_mode"] = rest_mode
        rest_cap_source = deadline_cap_source(
            rest_deadline,
            ("REST_TOTAL", rest_configured_deadline),
            ("GLOBAL_WORK", work_deadline),
        )
        report["effective_timeouts"]["rest_total_cap_source"] = rest_cap_source
        request_limits = (
            limits.model_copy(update={"rest_total_seconds": rest_effective})
            if limits.rest_total_seconds is not None
            else limits
        )
        async with asyncio.timeout_at(rest_deadline) as rest_timeout:
            active_timeout["phase"] = "REST_HEADERS_OR_BODY"
            active_timeout["scopes"] = active_scopes(
                "REST_TOTAL", rest_cap_source, rest_timeout, global_timeout
            )
            reply = await transport.metadata(request_limits)
        fits = retain_metadata(reply)
        if reply.status != 200:
            report["rest"]["status"] = (
                "BLOCKED" if reply.status in (401, 403, 418, 429, 451) else "FAIL"
            )
            report["status"] = report["rest"]["status"]
            report["reason"] = f"HTTP_{reply.status}; no retry or WS fallback"
            return
        if not fits:
            raise ProbeError("rest: response byte limit exceeded")
        if not reply.body_complete:
            raise AccessError("REST_BODY_INCOMPLETE", reply)
        report["metadata"] = inspect_metadata(reply.body)
        report["rest"]["status"] = "PASS"
        stage = "ws"
        report["ws"]["request_started_ns"] = time.time_ns()
        report["logical_calls"]["ws"] += 1
        ws_connect_started = loop.time()
        ws_connect_deadline = min(work_deadline, ws_connect_started + limits.connect_seconds)
        report["effective_timeouts"]["ws_connect_seconds"] = max(
            0.0, ws_connect_deadline - ws_connect_started
        )
        ws_connect_cap_source = deadline_cap_source(
            ws_connect_deadline,
            ("CONNECT", ws_connect_started + limits.connect_seconds),
            ("GLOBAL_WORK", work_deadline),
        )
        report["effective_timeouts"]["ws_connect_cap_source"] = ws_connect_cap_source
        async with asyncio.timeout_at(ws_connect_deadline) as ws_connect_timeout:
            active_timeout["phase"] = "WS_CONNECT"
            active_timeout["scopes"] = active_scopes(
                "CONNECT", ws_connect_cap_source, ws_connect_timeout, global_timeout
            )
            handshake = await transport.connect(limits)
        store(handshake, "ws")
        if handshake.status != 101:
            raise AccessError("WS_HANDSHAKE_REJECTED", handshake)
        while len(report["ws"]["messages"]) < limits.max_messages:
            remaining = limits.max_bytes - int(report["bytes_received"])
            if remaining < limits.max_frame_bytes:
                report["ws"]["status"] = "BLOCKED"
                report["status"] = report["ws"]["status"]
                report["reason"] = "BYTE_BUDGET_REACHED; no additional receive"
                return
            receive_started = loop.time()
            receive_deadline = min(work_deadline, receive_started + limits.idle_seconds)
            report["effective_timeouts"]["ws_receive_seconds"] = max(
                0.0, receive_deadline - receive_started
            )
            ws_receive_cap_source = deadline_cap_source(
                receive_deadline,
                ("IDLE", receive_started + limits.idle_seconds),
                ("GLOBAL_WORK", work_deadline),
            )
            report["effective_timeouts"]["ws_receive_cap_source"] = ws_receive_cap_source
            async with asyncio.timeout_at(receive_deadline) as ws_receive_timeout:
                active_timeout["phase"] = "WS_RECEIVE"
                active_timeout["scopes"] = active_scopes(
                    "IDLE", ws_receive_cap_source, ws_receive_timeout, global_timeout
                )
                body = await transport.receive()
            report["ws"]["frames_seen"] += 1
            fits = account(body)
            report["ws"]["messages"].append(
                {
                    "received_ns": time.time_ns(),
                    "body_hex": body[: limits.max_frame_bytes].hex(),
                    "truncated": not fits or len(body) > limits.max_frame_bytes,
                }
            )
            if not fits or len(body) > limits.max_frame_bytes:
                raise ProbeError("ws: response byte/frame limit exceeded")
            report["ws"]["messages"][-1]["inspection"] = inspect_trade(body, combined=True)
        report["ws"]["status"] = "PASS"
        report["status"] = "PASS"
        report["reason"] = (
            "bounded protocol sample only; quantity unit and continuous budget unverified"
        )

    try:
        # Reserve one second of the total cap for bounded close, not extra network work.
        async with asyncio.timeout_at(work_deadline) as global_timeout:
            await perform(global_timeout)
    except TimeoutError:
        report[stage]["status"] = report["status"] = "BLOCKED"
        report["reason"] = "TIMEOUT; no retry"
        report[stage]["timeout_diagnostics"] = timeout_diagnostic()
    except AccessError as exc:
        report[stage]["status"] = report["status"] = "BLOCKED"
        report["reason"] = exc.reason
        if exc.reply:
            if stage == "rest" and "body_hex" not in report["rest"]:
                retain_metadata(exc.reply)
            else:
                store(exc.reply, stage)
    except ProbeError as exc:
        report[stage]["status"] = report["status"] = "FAIL"
        report["reason"] = str(exc)
    except OSError:
        report[stage]["status"] = report["status"] = "BLOCKED"
        report["reason"] = "NETWORK_ERROR; exception details omitted to exclude credentials"
    finally:
        # Also retain progress when the timeout scope cancels metadata(), while
        # external CancelledError still propagates after bounded cleanup.
        retain_observation()
        attempts = getattr(transport, "request_attempts", None)
        report["transport_attempts"] = attempts
        report["retry_count_scope"] = (
            "APPLICATION_AND_REQUEST_HANDLER"
            if isinstance(attempts, dict)
            else "APPLICATION_ONLY_TRANSPORT_UNOBSERVED"
        )
        observed = attempts if isinstance(attempts, dict) else report["logical_calls"]
        report["retry_count"] = sum(max(0, count - 1) for count in observed.values())
        report["blocked_retry_count"] = getattr(transport, "blocked_reentries", None)
        try:
            async with asyncio.timeout(1):
                await transport.close()
            report["cleanup"] = "PASS"
        except (TimeoutError, OSError, AccessError):
            report["cleanup"] = "BLOCKED"
            if report["status"] == "PASS":
                report["status"] = "BLOCKED"
                report["reason"] = "CLEANUP_INCOMPLETE"
        report["ended_ns"] = time.time_ns()
        report["elapsed_seconds"] = time.monotonic() - started
    return report


def save_probe(report: dict[str, Any], output: Path) -> None:
    # Do not overwrite previous evidence or mix synthetic and live samples.
    report = deepcopy(report)
    output.mkdir(parents=True, exist_ok=False)
    for item, name in [
        (report["rest"], "rest-response"),
        *[(item, f"ws-{index:03}") for index, item in enumerate(report["ws"]["messages"])],
    ]:
        if "body_hex" in item:
            payload = bytes.fromhex(item.pop("body_hex"))
            path = output / f"{name}.bin"
            path.write_bytes(payload)
            item["sample"] = path.name
            item["sha256"] = hashlib.sha256(payload).hexdigest()
    (output / "protocol_probe_report.json").write_text(json.dumps(report, indent=2) + "\n")
    budget = {
        "evidence_kind": report["evidence_kind"],
        "scope": "BTCUSDT um_perpetual metadata + aggTrade",
        "documented_metadata_request_weight": 1,
        "documentation_url": "https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data",
        "observed_rate_limits": report.get("metadata", {}).get("rateLimits"),
        "observed_usage_headers": report["rest"].get("rate_headers", {}),
        "probe_limits": report["limits"],
        "max_retries": 0,
        "observed_transport_attempts": report.get("transport_attempts"),
        "observed_retry_count": report["retry_count"],
        "retry_count_scope": report.get("retry_count_scope"),
        "blocked_retry_count": report.get("blocked_retry_count"),
        "backoff_cap_seconds": 0,
        "status": "UNVERIFIED_FOR_CONTINUOUS_COLLECTION",
        "continuous_collection_enabled": False,
    }
    (output / "endpoint_budget.json").write_text(json.dumps(budget, indent=2) + "\n")
