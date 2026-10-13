from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

import pytest

from btc_microstructure.offline_core import (
    BOUND_ADAPTER_VERSION,
    BOUND_FEATURE_VERSION,
    BOUND_NORMALIZATION_VERSION,
    Core,
    NS,
)
from btc_microstructure.unit_binding import TEST_ONLY_APPROVAL_KIND, UNIT_EVIDENCE_ID, UnitBinding


def unit_binding_fixture(**updates: Any) -> UnitBinding:
    data: dict[str, Any] = {
        "unit_evidence_id": UNIT_EVIDENCE_ID,
        "evidence_version": "test-v1",
        "evidence_sha256": "a" * 64,
        "approval_id": "TEST_ONLY:r2-04:unit-binding",
        "approval_kind": TEST_ONLY_APPROVAL_KIND,
        "runtime_scope": "TEST_ONLY_OFFLINE",
        "instrument_id": "binance:um_perpetual:BTCUSDT",
        "venue": "binance",
        "market_type": "um_perpetual",
        "symbol": "BTCUSDT",
        "source_stream": "um_agg_trade",
        "route": "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade",
        "stream_name": "btcusdt@aggTrade",
        "st_if_present": 1,
        "price_unit": "USDT/BTC",
        "quantity_unit": "BTC",
        "normal_quantity_unit": "BTC",
        "notional_unit": "USDT",
        "price_multiplier_decimal": "1",
        "quantity_multiplier_decimal": "1",
        "current_metadata_sha256": "b" * 64,
        "price_tick": "0.10",
        "quantity_step": "0.001",
        "minimum_quantity": "0.001",
    }
    data.update(updates)
    return UnitBinding(**data)


def live_core(
    *, binding: UnitBinding | None = None, metadata: dict[str, Any] | None = None
) -> Core:
    config: dict[str, Any] = {"time_view": "as_received", "window_seconds": [30]}
    if metadata is None and binding is not None:
        metadata = dict(binding.core_metadata())
    if metadata is None:
        metadata = {
            "metadata_version": "pending-live-metadata",
            "quantity_unit": "BTC",
            "evidence_kind": "LIVE",
            "unit_evidence_id": UNIT_EVIDENCE_ID,
            "price_tick": "0.10",
            "quantity_step": "0.001",
            "minimum_quantity": "0.001",
        }
    return Core(config, metadata, binding=binding)


def open_session(engine: Core) -> None:
    engine.process(
        {
            "kind": "SESSION_OPEN",
            "session_id": "s-live",
            "ingest_sequence": 1,
            "ts_receive_ns": 0,
        }
    )


def live_trade(
    *,
    seq: int,
    receive_s: int,
    agg_id: int,
    quantity: str,
    buyer_is_maker: bool = False,
    nq: str | None = None,
    evidence_kind: str = "TEST_ONLY_OFFLINE",
    metadata_version: str = "b" * 64,
    raw_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = {
        "e": "aggTrade",
        "s": "BTCUSDT",
        "a": agg_id,
        "p": "100.00",
        "q": quantity,
        "f": agg_id,
        "l": agg_id,
        "m": buyer_is_maker,
        "E": receive_s * 1000,
        "T": receive_s * 1000 + 1,
        "st": 1,
    }
    if nq is not None:
        raw["nq"] = nq
    if raw_updates:
        raw.update(raw_updates)
    return {
        "kind": "RAW",
        "session_id": "s-live",
        "ingest_sequence": seq,
        "ts_receive_ns": receive_s * NS,
        "venue": "binance",
        "market_type": "um_perpetual",
        "instrument_id": "binance:um_perpetual:BTCUSDT",
        "source_stream": "um_agg_trade",
        "source_origin": "live_ws",
        "source_time_unit": "ms",
        "metadata_version": metadata_version,
        "evidence_kind": evidence_kind,
        "raw_ref": f"raw:{seq}",
        "payload": json.dumps(raw, separators=(",", ":")),
    }


def cutoff(engine: Core, *, seq: int, cutoff_s: int, high: int) -> None:
    engine.process(
        {
            "kind": "FEATURE_CUTOFF",
            "ingest_sequence": seq,
            "ts_receive_ns": cutoff_s * NS,
            "cutoff_ns": cutoff_s * NS,
            "input_cursor": high,
        }
    )


def publish(engine: Core, *, seq: int, cutoff_s: int, available_s: int) -> None:
    engine.process(
        {
            "kind": "FEATURE_PUBLISHED",
            "ingest_sequence": seq,
            "ts_receive_ns": available_s * NS,
            "cutoff_ns": cutoff_s * NS,
            "feature_available_ns": available_s * NS,
        }
    )


def test_live_rows_fail_closed_without_explicit_unit_binding() -> None:
    engine = live_core(binding=None)
    open_session(engine)

    engine.process(live_trade(seq=2, receive_s=31, agg_id=1, quantity="0.30"))
    cutoff(engine, seq=3, cutoff_s=61, high=2)
    publish(engine, seq=4, cutoff_s=61, available_s=62)

    assert engine.normalized[0]["quantity_unit"] == "UNKNOWN"
    assert engine.normalized[0]["quantity_base"] is None
    assert engine.features[0]["quality_flags"] == ["UNIT_UNKNOWN"]
    assert engine.features[0]["volume_base"] is None
    assert engine.features[0]["publication_status"] == "RECORDED_SYNTHETIC"


def test_test_only_binding_enables_live_decimal_flow_with_provenance() -> None:
    binding = unit_binding_fixture()
    engine = live_core(binding=binding)
    open_session(engine)

    engine.process(live_trade(seq=2, receive_s=31, agg_id=1, quantity="0.30", nq="0.20"))
    engine.process(live_trade(seq=3, receive_s=32, agg_id=2, quantity="0.20"))
    engine.process(live_trade(seq=4, receive_s=33, agg_id=3, quantity="0.10", buyer_is_maker=True))
    cutoff(engine, seq=5, cutoff_s=61, high=4)
    publish(engine, seq=6, cutoff_s=61, available_s=62)

    first = engine.normalized[0]
    feature = engine.features[0]
    assert first["quantity_base"] == Decimal("0.30")
    assert first["normal_quantity_base"] == Decimal("0.20")
    assert first["notional_quote"] == Decimal("30.0000")
    assert engine.normalized[1]["normal_quantity_base"] is None
    assert feature["buy_volume_base"] == Decimal("0.50")
    assert feature["sell_volume_base"] == Decimal("0.10")
    assert feature["volume_base"] == Decimal("0.60")
    assert feature["trade_delta_base"] == Decimal("0.40")
    assert feature["trade_imbalance"] == pytest.approx(2 / 3, rel=1e-10, abs=1e-12)
    assert feature["agg_message_count"] == 3
    assert feature["quality_flags"] == []
    assert first["adapter_version"] == BOUND_ADAPTER_VERSION
    assert first["normalization_version"] == BOUND_NORMALIZATION_VERSION
    assert feature["feature_version"] == BOUND_FEATURE_VERSION
    assert feature["dataset_revision"] == "test-only-live:TEST_ONLY:r2-04:unit-binding"
    assert feature["source_scope"] == "um_agg_trade:test_only_offline"
    assert feature["publication_status"] == "RECORDED_TEST_ONLY"
    assert feature["feature_available_ns"] == 62 * NS
    assert feature["unit_binding_id"] == UNIT_EVIDENCE_ID
    assert feature["unit_approval_id"] == "TEST_ONLY:r2-04:unit-binding"


def test_core_accepts_binding_only_through_explicit_keyword_object() -> None:
    binding = unit_binding_fixture()
    with pytest.raises(ValueError, match="binding=UnitBinding"):
        Core(
            {"time_view": "as_received", "window_seconds": [30], "unit_binding": {}},
            dict(binding.core_metadata()),
        )

    with pytest.raises(ValueError, match="only TEST_ONLY_OFFLINE"):
        live_core(binding=unit_binding_fixture(runtime_scope="LIVE"))


def test_binding_identity_mismatch_keeps_live_quantities_unknown() -> None:
    engine = live_core(binding=unit_binding_fixture())
    open_session(engine)

    engine.process(
        live_trade(
            seq=2,
            receive_s=31,
            agg_id=1,
            quantity="0.30",
            metadata_version="different-metadata",
        )
    )
    cutoff(engine, seq=3, cutoff_s=61, high=2)
    publish(engine, seq=4, cutoff_s=61, available_s=62)

    assert engine.normalized[0]["quantity_unit"] == "UNKNOWN"
    assert engine.normalized[0]["quantity_base"] is None
    assert engine.features[0]["quality_flags"] == ["UNIT_UNKNOWN"]


def test_live_symbol_type_mismatch_is_quarantined_with_original_payload() -> None:
    engine = live_core(binding=unit_binding_fixture())
    open_session(engine)
    row = live_trade(seq=2, receive_s=31, agg_id=1, quantity="0.30", raw_updates={"st": 2})

    engine.process(row)

    assert engine.normalized == []
    assert engine.quarantine[0]["payload"] == row["payload"]
    assert engine.quarantine[0]["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]
    assert "st: unsupported source" in engine.quarantine[0]["reason"]
