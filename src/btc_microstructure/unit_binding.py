"""Explicit unit binding gate for the bounded BTCUSDT aggTrade path."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from types import MappingProxyType
from typing import Any, Literal, Mapping

TEST_ONLY_APPROVAL_KIND = "TEST_ONLY_OFFLINE_UNIT_BINDING"
FORMAL_LIVE_APPROVAL_KIND = "FORMAL_LIVE_UNIT_BINDING_APPROVAL"
UNIT_EVIDENCE_ID = "DOC:binance:um_perpetual:BTCUSDT:aggTrade:2026-10-13:v1"
STREAM: Literal["btcusdt@aggTrade"] = "btcusdt@aggTrade"
INSTRUMENT_ID: Literal["binance:um_perpetual:BTCUSDT"] = "binance:um_perpetual:BTCUSDT"


class UnitBindingError(ValueError):
    """Rejected binding decision; message is safe for reports and tests."""


@dataclass(frozen=True)
class UnitBinding:
    unit_evidence_id: str
    evidence_version: str
    evidence_sha256: str
    approval_id: str
    approval_kind: Literal["TEST_ONLY_OFFLINE_UNIT_BINDING", "FORMAL_LIVE_UNIT_BINDING_APPROVAL"]
    runtime_scope: Literal["TEST_ONLY_OFFLINE", "LIVE"]
    instrument_id: str
    venue: Literal["binance"]
    market_type: Literal["um_perpetual"]
    symbol: Literal["BTCUSDT"]
    source_stream: Literal["um_agg_trade"]
    route: Literal["wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade"]
    stream_name: Literal["btcusdt@aggTrade"]
    st_if_present: Literal[1]
    price_unit: Literal["USDT/BTC"]
    quantity_unit: Literal["BTC"]
    normal_quantity_unit: Literal["BTC"]
    notional_unit: Literal["USDT"]
    price_multiplier_decimal: Literal["1"]
    quantity_multiplier_decimal: Literal["1"]
    current_metadata_sha256: str
    price_tick: str
    quantity_step: str
    minimum_quantity: str

    def core_metadata(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                "metadata_version": self.current_metadata_sha256,
                "quantity_unit": self.quantity_unit,
                "evidence_kind": self.runtime_scope,
                "unit_evidence_id": self.unit_evidence_id,
                "price_tick": self.price_tick,
                "quantity_step": self.quantity_step,
                "minimum_quantity": self.minimum_quantity,
            }
        )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_unit_evidence(data: bytes) -> tuple[dict[str, Any], str]:
    parsed = _object_json(data)
    raw_sha = sha256_bytes(data)
    parsed["__trusted_evidence_sha256__"] = raw_sha
    parsed["__trusted_canonical_sha256__"] = _canonical_sha256(parsed)
    return parsed, raw_sha


def resolve_live_unit_binding(
    *,
    evidence: Mapping[str, Any],
    evidence_sha256: str,
    current_metadata: Mapping[str, Any],
    approval: Mapping[str, Any] | None,
    shared_budget_trusted: bool,
    source_stream: str,
    symbol_type_raw: int | None,
) -> UnitBinding:
    """Production gate. The checked R2-03 candidate fails closed by design."""

    _validate_evidence_identity(evidence, evidence_sha256)
    if not shared_budget_trusted:
        raise UnitBindingError("shared_budget: trusted current shared egress budget is required")
    if approval is None:
        raise UnitBindingError("approval: formal live approval is required")
    if approval.get("kind") != FORMAL_LIVE_APPROVAL_KIND:
        raise UnitBindingError("approval.kind: formal live approval is required")
    if evidence.get("review_status") != "APPROVED" or evidence.get("runtime_applied") is not True:
        raise UnitBindingError("evidence: review_status APPROVED and runtime_applied true required")
    binding = _resolve_with_approval(
        evidence=evidence,
        evidence_sha256=evidence_sha256,
        current_metadata=current_metadata,
        approval=approval,
        source_stream=source_stream,
        symbol_type_raw=symbol_type_raw,
        runtime_scope="LIVE",
        expected_kind=FORMAL_LIVE_APPROVAL_KIND,
    )
    return binding


def resolve_test_only_unit_binding(
    *,
    evidence: Mapping[str, Any],
    evidence_sha256: str,
    current_metadata: Mapping[str, Any],
    approval: Mapping[str, Any],
    source_stream: str,
    symbol_type_raw: int | None,
) -> UnitBinding:
    """Offline-only positive path for deterministic tests and fixtures."""

    _validate_evidence_identity(evidence, evidence_sha256)
    return _resolve_with_approval(
        evidence=evidence,
        evidence_sha256=evidence_sha256,
        current_metadata=current_metadata,
        approval=approval,
        source_stream=source_stream,
        symbol_type_raw=symbol_type_raw,
        runtime_scope="TEST_ONLY_OFFLINE",
        expected_kind=TEST_ONLY_APPROVAL_KIND,
    )


def _resolve_with_approval(
    *,
    evidence: Mapping[str, Any],
    evidence_sha256: str,
    current_metadata: Mapping[str, Any],
    approval: Mapping[str, Any],
    source_stream: str,
    symbol_type_raw: int | None,
    runtime_scope: Literal["TEST_ONLY_OFFLINE", "LIVE"],
    expected_kind: str,
) -> UnitBinding:
    if approval.get("kind") != expected_kind:
        raise UnitBindingError(f"approval.kind: expected {expected_kind}")
    approval_id = _string(approval.get("approval_id"), "approval.approval_id")
    _expect_equal(approval.get("unit_evidence_id"), UNIT_EVIDENCE_ID, "approval.unit_evidence_id")
    _expect_equal(approval.get("evidence_version"), "1", "approval.evidence_version")
    _expect_equal(approval.get("evidence_sha256"), evidence_sha256, "approval.evidence_sha256")
    _expect_equal(approval.get("instrument_id"), INSTRUMENT_ID, "approval.instrument_id")
    _expect_equal(approval.get("source_stream"), "um_agg_trade", "approval.source_stream")
    _expect_equal(approval.get("stream_name"), STREAM, "approval.stream_name")
    _expect_equal(approval.get("st_if_present"), 1, "approval.st_if_present")
    _expect_equal(source_stream, STREAM, "source_stream")
    if symbol_type_raw is not None and symbol_type_raw != 1:
        raise UnitBindingError("symbol_type_raw: expected 1 when present")

    fields = _mapping(evidence.get("fields"), "fields")
    _field(fields, "q", unit="BTC", multiplier="1")
    _field(fields, "nq", unit="BTC", multiplier="1")
    _field(fields, "p", unit="USDT/BTC", multiplier="1")
    notional = _mapping(fields.get("notional_quote"), "fields.notional_quote")
    _expect_equal(notional.get("unit"), "USDT", "fields.notional_quote.unit")
    if notional.get("extra_contract_multiplier") is not None:
        raise UnitBindingError("fields.notional_quote.extra_contract_multiplier: expected null")

    metadata = _metadata(current_metadata)
    approval_filters = _mapping(approval.get("filters"), "approval.filters")
    for approval_key, metadata_key in (
        ("tickSize", "price_tick"),
        ("stepSize", "quantity_step"),
        ("minQty", "minimum_quantity"),
    ):
        expected = _decimal_string(
            approval_filters.get(approval_key), f"approval.filters.{approval_key}"
        )
        _expect_equal(metadata[metadata_key], expected, f"metadata.{metadata_key}")
    approval_metadata_sha = approval.get("current_metadata_sha256")
    if approval_metadata_sha is not None:
        _expect_equal(
            approval_metadata_sha,
            metadata["metadata_sha256"],
            "approval.current_metadata_sha256",
        )

    return UnitBinding(
        unit_evidence_id=UNIT_EVIDENCE_ID,
        evidence_version="1",
        evidence_sha256=evidence_sha256,
        approval_id=approval_id,
        approval_kind=expected_kind,  # type: ignore[arg-type]
        runtime_scope=runtime_scope,
        instrument_id=INSTRUMENT_ID,
        venue="binance",
        market_type="um_perpetual",
        symbol="BTCUSDT",
        source_stream="um_agg_trade",
        route="wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade",
        stream_name=STREAM,
        st_if_present=1,
        price_unit="USDT/BTC",
        quantity_unit="BTC",
        normal_quantity_unit="BTC",
        notional_unit="USDT",
        price_multiplier_decimal="1",
        quantity_multiplier_decimal="1",
        current_metadata_sha256=metadata["metadata_sha256"],
        price_tick=metadata["price_tick"],
        quantity_step=metadata["quantity_step"],
        minimum_quantity=metadata["minimum_quantity"],
    )


def _validate_evidence_identity(evidence: Mapping[str, Any], evidence_sha256: str) -> None:
    _expect_equal(
        evidence.get("__trusted_evidence_sha256__"),
        evidence_sha256,
        "__trusted_evidence_sha256__",
    )
    _expect_equal(
        evidence.get("__trusted_canonical_sha256__"),
        _canonical_sha256(evidence),
        "__trusted_canonical_sha256__",
    )
    _expect_equal(
        evidence.get("schema_version"), "quantity-unit-evidence-candidate.1", "schema_version"
    )
    _expect_equal(evidence.get("unit_evidence_id"), UNIT_EVIDENCE_ID, "unit_evidence_id")
    _expect_equal(evidence.get("evidence_version"), "1", "evidence_version")
    _expect_equal(
        evidence.get("documentary_conclusion"),
        "SUFFICIENT_FOR_BTC_MAPPING_WITHIN_CHECKED_IDENTITY",
        "documentary_conclusion",
    )
    if not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64:
        raise UnitBindingError("evidence_sha256: expected sha256 hex")
    instrument = _mapping(evidence.get("instrument"), "instrument")
    expected = {
        "venue": "binance",
        "market": "um_perpetual",
        "instrument_id": INSTRUMENT_ID,
        "symbol": "BTCUSDT",
        "contract_type": "PERPETUAL",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "margin_asset": "USDT",
        "stream": "um_agg_trade",
        "st_if_present": 1,
        "route": "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade",
    }
    for key, value in expected.items():
        _expect_equal(instrument.get(key), value, f"instrument.{key}")


def _metadata(current: Mapping[str, Any]) -> dict[str, str]:
    symbol = current
    if "symbols" in current:
        symbols = current.get("symbols")
        if not isinstance(symbols, list):
            raise UnitBindingError("metadata.symbols: expected list")
        matches = [
            item for item in symbols if isinstance(item, dict) and item.get("symbol") == "BTCUSDT"
        ]
        if len(matches) != 1:
            raise UnitBindingError("metadata.symbols.BTCUSDT: expected exactly one")
        symbol = matches[0]
    for key, expected in (
        ("symbol", "BTCUSDT"),
        ("baseAsset", "BTC"),
        ("quoteAsset", "USDT"),
        ("marginAsset", "USDT"),
        ("contractType", "PERPETUAL"),
        ("status", "TRADING"),
    ):
        _expect_equal(symbol.get(key), expected, f"metadata.{key}")
    filters = _filters(symbol)
    metadata_sha256 = sha256_bytes(
        json.dumps(current, sort_keys=True, separators=(",", ":"), default=str).encode()
    )
    return {
        "metadata_sha256": metadata_sha256,
        "price_tick": filters["tickSize"],
        "quantity_step": filters["stepSize"],
        "minimum_quantity": filters["minQty"],
    }


def _filters(symbol: Mapping[str, Any]) -> dict[str, str]:
    if all(key in symbol for key in ("tickSize", "stepSize", "minQty")):
        return {
            "tickSize": _decimal_string(symbol.get("tickSize"), "metadata.tickSize"),
            "stepSize": _decimal_string(symbol.get("stepSize"), "metadata.stepSize"),
            "minQty": _decimal_string(symbol.get("minQty"), "metadata.minQty"),
        }
    filters = symbol.get("filters")
    if not isinstance(filters, list):
        raise UnitBindingError("metadata.filters: expected list")
    by_type: dict[Any, Any] = {}
    for item in filters:
        if not isinstance(item, dict):
            continue
        kind = item.get("filterType")
        if kind in by_type:
            raise UnitBindingError(f"metadata.filters.{kind}: duplicate filter")
        by_type[kind] = item
    price_filter = _mapping(by_type.get("PRICE_FILTER"), "metadata.filters.PRICE_FILTER")
    lot_filter = _mapping(by_type.get("LOT_SIZE"), "metadata.filters.LOT_SIZE")
    return {
        "tickSize": _decimal_string(price_filter.get("tickSize"), "metadata.tickSize"),
        "stepSize": _decimal_string(lot_filter.get("stepSize"), "metadata.stepSize"),
        "minQty": _decimal_string(lot_filter.get("minQty"), "metadata.minQty"),
    }


def _field(fields: Mapping[str, Any], name: str, *, unit: str, multiplier: str) -> None:
    field = _mapping(fields.get(name), f"fields.{name}")
    _expect_equal(field.get("unit"), unit, f"fields.{name}.unit")
    _expect_equal(field.get("multiplier_decimal"), multiplier, f"fields.{name}.multiplier_decimal")


def _object_json(data: bytes) -> dict[str, Any]:
    def reject(_value: str) -> None:
        raise UnitBindingError("json: nonfinite number")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise UnitBindingError("json: duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(data, parse_constant=reject, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise UnitBindingError(
            "json: invalid object encoding or duplicate/nonfinite fields"
        ) from exc
    if not isinstance(value, dict):
        raise UnitBindingError("json: expected object")
    return value


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise UnitBindingError(f"{field}: expected object")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise UnitBindingError(f"{field}: expected nonempty string")
    return value


def _decimal_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise UnitBindingError(f"{field}: expected decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise UnitBindingError(f"{field}: invalid decimal string") from exc
    if not number.is_finite() or number <= 0:
        raise UnitBindingError(f"{field}: expected finite positive decimal")
    return value


def _expect_equal(actual: Any, expected: Any, field: str) -> None:
    if actual != expected or type(actual) is not type(expected):
        raise UnitBindingError(f"{field}: expected {expected!r}")


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    public = {key: item for key, item in value.items() if not str(key).startswith("__trusted_")}
    return sha256_bytes(json.dumps(public, sort_keys=True, separators=(",", ":")).encode())
