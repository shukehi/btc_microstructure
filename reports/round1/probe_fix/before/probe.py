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
    idle_seconds: float = Field(default=5.0, gt=0, le=10, allow_inf_nan=False)
    max_messages: int = Field(default=20, ge=1, le=20)
    max_bytes: int = Field(default=2097152, ge=2048, le=2097152)
    max_frame_bytes: int = Field(default=65536, ge=512, le=65536)


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes = b""


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
    report: dict[str, Any] = {
        "schema_version": "probe-0.1",
        "evidence_kind": evidence_kind,
        "started_ns": time.time_ns(),
        "limits": limits.model_dump(),
        "status": "NOT_RUN",
        "rest": {"status": "NOT_RUN", "method": "GET", "url": REST_URL, "params": {}},
        "ws": {
            "status": "NOT_RUN",
            "url": WS_URL,
            "subscription": "URL combined stream",
            "messages": [],
            "frames_seen": 0,
        },
        "bytes_received": 0,
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

    def account(body: bytes) -> bool:
        remaining = limits.max_bytes - int(report["bytes_received"])
        report["bytes_received"] += len(body)
        return len(body) <= remaining

    async def perform() -> None:
        nonlocal stage
        report["rest"]["request_started_ns"] = time.time_ns()
        async with asyncio.timeout(limits.connect_seconds):
            reply = await transport.metadata(limits)
        store(reply, "rest")
        fits = account(reply.body)
        report["rest"]["body_hex"] = reply.body[: limits.max_bytes].hex()
        report["rest"]["truncated"] = not fits
        if reply.status != 200:
            report["rest"]["status"] = (
                "BLOCKED" if reply.status in (401, 403, 418, 429, 451) else "FAIL"
            )
            report["status"] = report["rest"]["status"]
            report["reason"] = f"HTTP_{reply.status}; no retry or WS fallback"
            return
        if not fits:
            raise ProbeError("rest: response byte limit exceeded")
        report["metadata"] = inspect_metadata(reply.body)
        report["rest"]["status"] = "PASS"
        stage = "ws"
        report["ws"]["request_started_ns"] = time.time_ns()
        async with asyncio.timeout(limits.connect_seconds):
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
            async with asyncio.timeout(limits.idle_seconds):
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

    started = time.monotonic()
    try:
        # Reserve one second of the total cap for bounded close, not extra network work.
        async with asyncio.timeout(limits.total_seconds - 1):
            await perform()
    except TimeoutError:
        report[stage]["status"] = report["status"] = "BLOCKED"
        report["reason"] = "TIMEOUT; no retry"
    except AccessError as exc:
        report[stage]["status"] = report["status"] = "BLOCKED"
        report["reason"] = exc.reason
        if exc.reply:
            store(exc.reply, stage)
    except ProbeError as exc:
        report[stage]["status"] = report["status"] = "FAIL"
        report["reason"] = str(exc)
    except OSError:
        report[stage]["status"] = report["status"] = "BLOCKED"
        report["reason"] = "NETWORK_ERROR; exception details omitted to exclude credentials"
    finally:
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
        "backoff_cap_seconds": 0,
        "status": "UNVERIFIED_FOR_CONTINUOUS_COLLECTION",
        "continuous_collection_enabled": False,
    }
    (output / "endpoint_budget.json").write_text(json.dumps(budget, indent=2) + "\n")
