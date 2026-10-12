from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

import pytest

from btc_microstructure.offline_core import Core, NS


MISSING = object()


def metadata() -> dict[str, str]:
    return {
        "metadata_version": "synthetic-r104",
        "quantity_unit": "BTC",
        "evidence_kind": "SYNTHETIC",
        "unit_evidence_id": "SYNTHETIC:r104-quality",
        "price_tick": "0.1",
        "quantity_step": "0.01",
        "minimum_quantity": "0.01",
    }


def engine(*, windows: tuple[int, ...] = (5, 30)) -> Core:
    return Core({"time_view": "as_received", "window_seconds": list(windows)}, metadata())


def add(engine: Core, kind: str, receive_ns: int, **extra: Any) -> None:
    engine.process(
        {
            "kind": kind,
            "session_id": "s",
            "ingest_sequence": engine.cursor + 1,
            "ts_receive_ns": receive_ns,
            **extra,
        }
    )


def raw_trade(
    engine: Core,
    *,
    receive_s: int,
    agg_id: int,
    quantity: str,
    buyer_is_maker: bool = False,
    quality_flags: Any = MISSING,
    include_quality_flags: bool = True,
    payload_updates: dict[str, Any] | None = None,
) -> None:
    payload = {
        "e": "aggTrade",
        "s": "BTCUSDT",
        "a": agg_id,
        "f": agg_id,
        "l": agg_id,
        "E": receive_s * 1000,
        "T": receive_s * 1000 + 1,
        "p": "100.0",
        "q": quantity,
        "m": buyer_is_maker,
    }
    if payload_updates:
        payload.update(payload_updates)
    row: dict[str, Any] = {
        "payload": json.dumps(payload, separators=(",", ":")),
        "raw_ref": f"raw:{receive_s}:{agg_id}",
        "evidence_kind": "SYNTHETIC",
        "source_time_unit": "ms",
    }
    if include_quality_flags:
        row["quality_flags"] = [] if quality_flags is MISSING else quality_flags
    add(engine, "RAW", receive_s * NS, **row)


def cutoff(engine: Core, *, cutoff_s: int, high: int | None = None) -> None:
    high_water = engine.cursor if high is None else high
    add(engine, "FEATURE_CUTOFF", cutoff_s * NS, cutoff_ns=cutoff_s * NS, input_cursor=high_water)
    add(
        engine,
        "FEATURE_PUBLISHED",
        cutoff_s * NS + 1,
        cutoff_ns=cutoff_s * NS,
        feature_available_ns=cutoff_s * NS + 1,
    )


def feature(engine: Core, *, cutoff_s: int, window_s: int) -> dict[str, Any]:
    return next(
        item
        for item in engine.features
        if item["feature_cutoff_ns"] == cutoff_s * NS and item["window_seconds"] == window_s
    )


def open_engine() -> Core:
    core = engine()
    add(core, "SESSION_OPEN", 0)
    return core


def assert_null_flow(row: dict[str, Any]) -> None:
    assert row["buy_volume_base"] is None
    assert row["sell_volume_base"] is None
    assert row["volume_base"] is None
    assert row["trade_delta_base"] is None
    assert row["trade_imbalance"] is None
    assert row["agg_message_count"] is None


def test_healthy_duplicate_trade_keeps_manual_flow_expected_values() -> None:
    core = open_engine()
    raw_trade(core, receive_s=2, agg_id=1, quantity="0.30")
    raw_trade(core, receive_s=3, agg_id=2, quantity="0.20")
    raw_trade(core, receive_s=4, agg_id=3, quantity="0.10", buyer_is_maker=True)
    raw_trade(
        core,
        receive_s=5,
        agg_id=2,
        quantity="0.20",
        payload_updates={"E": 3000, "T": 3001},
    )
    cutoff(core, cutoff_s=31)

    row = feature(core, cutoff_s=31, window_s=30)
    assert row["buy_volume_base"] == Decimal("0.50")
    assert row["sell_volume_base"] == Decimal("0.10")
    assert row["volume_base"] == Decimal("0.60")
    assert row["trade_delta_base"] == Decimal("0.40")
    assert row["trade_imbalance"] == pytest.approx(2 / 3, rel=1e-10, abs=1e-12)
    assert row["agg_message_count"] == 3
    assert row["quality_flags"] == []


def test_missing_quality_flags_defaults_to_empty_for_historical_raw() -> None:
    core = open_engine()
    raw_trade(core, receive_s=2, agg_id=1, quantity="0.30", include_quality_flags=False)

    assert core.normalized[0]["quality_flags"] == []


def test_source_sampled_is_advisory_and_does_not_null_trade_window() -> None:
    core = open_engine()
    raw_trade(core, receive_s=2, agg_id=1, quantity="0.30", quality_flags=["SOURCE_SAMPLED"])
    cutoff(core, cutoff_s=31)

    row = feature(core, cutoff_s=31, window_s=30)
    assert core.normalized[0]["quality_flags"] == ["SOURCE_SAMPLED"]
    assert row["quality_flags"] == ["SOURCE_SAMPLED"]
    assert row["volume_base"] == Decimal("0.30")
    assert row["trade_delta_base"] == Decimal("0.30")
    assert row["agg_message_count"] == 1


def test_gap_known_mixed_with_source_sampled_nulls_flow_and_preserves_flags() -> None:
    core = open_engine()
    raw_trade(
        core,
        receive_s=2,
        agg_id=1,
        quantity="0.30",
        quality_flags=["SOURCE_SAMPLED", "GAP_KNOWN"],
    )
    cutoff(core, cutoff_s=31)

    row = feature(core, cutoff_s=31, window_s=30)
    assert core.normalized[0]["quality_flags"] == ["GAP_KNOWN", "SOURCE_SAMPLED"]
    assert row["quality_flags"] == ["GAP_KNOWN", "SOURCE_SAMPLED"]
    assert_null_flow(row)


def test_gap_known_affects_matching_window_only_and_ages_out_by_point_scope() -> None:
    core = open_engine()
    raw_trade(core, receive_s=2, agg_id=1, quantity="0.30", quality_flags=["GAP_KNOWN"])
    cutoff(core, cutoff_s=6)
    cutoff(core, cutoff_s=31)
    cutoff(core, cutoff_s=33)

    assert "GAP_KNOWN" in feature(core, cutoff_s=6, window_s=5)["quality_flags"]
    assert "GAP_KNOWN" in feature(core, cutoff_s=31, window_s=30)["quality_flags"]
    aged_out = feature(core, cutoff_s=33, window_s=30)
    assert aged_out["quality_flags"] == []
    assert aged_out["volume_base"] == Decimal(0)
    assert aged_out["agg_message_count"] == 0


def test_future_quality_receipt_does_not_rewrite_already_published_snapshot() -> None:
    core = open_engine()
    raw_trade(core, receive_s=10, agg_id=7, quantity="0.30")
    cutoff(core, cutoff_s=31)
    before = feature(core, cutoff_s=31, window_s=30).copy()
    raw_trade(
        core,
        receive_s=40,
        agg_id=7,
        quantity="0.30",
        quality_flags=["GAP_KNOWN"],
        payload_updates={"E": 10000, "T": 10001},
    )

    assert feature(core, cutoff_s=31, window_s=30) == before


def test_later_duplicate_quality_receipt_invalidates_only_observed_overlap() -> None:
    core = open_engine()
    raw_trade(core, receive_s=10, agg_id=7, quantity="0.30")
    raw_trade(
        core,
        receive_s=40,
        agg_id=7,
        quantity="0.30",
        quality_flags=["GAP_KNOWN"],
        payload_updates={"E": 10000, "T": 10001},
    )
    cutoff(core, cutoff_s=41)
    cutoff(core, cutoff_s=71)

    affected = feature(core, cutoff_s=41, window_s=30)
    aged_out = feature(core, cutoff_s=71, window_s=30)
    assert core.quarantine[0]["quality_flags"] == ["DUPLICATE", "GAP_KNOWN"]
    assert affected["quality_flags"] == ["GAP_KNOWN"]
    assert_null_flow(affected)
    assert aged_out["quality_flags"] == []
    assert aged_out["volume_base"] == Decimal(0)


def test_later_duplicate_quality_above_high_water_does_not_invalidate_snapshot() -> None:
    core = open_engine()
    raw_trade(core, receive_s=10, agg_id=7, quantity="0.30")
    high_before_duplicate = core.cursor
    raw_trade(
        core,
        receive_s=20,
        agg_id=7,
        quantity="0.30",
        quality_flags=["GAP_KNOWN"],
        payload_updates={"E": 10000, "T": 10001},
    )
    cutoff(core, cutoff_s=31, high=high_before_duplicate)

    row = feature(core, cutoff_s=31, window_s=30)
    assert row["quality_flags"] == []
    assert row["volume_base"] == Decimal("0.30")
    assert row["agg_message_count"] == 1


def test_conflicting_duplicate_merges_upstream_quality_with_conflict_audit() -> None:
    core = open_engine()
    raw_trade(core, receive_s=10, agg_id=7, quantity="0.30")
    raw_trade(core, receive_s=20, agg_id=7, quantity="0.40", quality_flags=["GAP_KNOWN"])

    assert len(core.normalized) == 1
    assert core.normalized[0]["quantity_base"] == Decimal("0.30")
    assert core.normalized[0]["quality_flags"] == []
    assert core.quarantine[0]["quantity_base"] == Decimal("0.40")
    assert core.quarantine[0]["quality_flags"] == ["DUPLICATE_CONFLICT", "GAP_KNOWN"]


@pytest.mark.parametrize(
    "invalid_flags",
    [
        None,
        "GAP_KNOWN",
        ["NOT_A_DOCS_FLAG"],
        [7],
    ],
)
def test_invalid_quality_flags_are_quarantined_with_original_flag_json(
    invalid_flags: Any,
) -> None:
    core = open_engine()
    raw_trade(core, receive_s=2, agg_id=1, quantity="0.30", quality_flags=invalid_flags)

    quarantined = core.quarantine[0]
    assert core.normalized == []
    assert quarantined["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]
    assert quarantined["upstream_quality_flags_json"] == json.dumps(invalid_flags)
    assert quarantined["payload"] == core.history[1]["payload"]
    assert "quality_flags" in quarantined["reason"]


def test_invalid_quality_flags_and_payload_error_preserve_both_raw_evidence_fields() -> None:
    core = open_engine()
    raw_trade(
        core,
        receive_s=2,
        agg_id=1,
        quantity="0.30",
        quality_flags=["NOT_A_DOCS_FLAG"],
        payload_updates={"q": 0.30},
    )

    quarantined = core.quarantine[0]
    assert quarantined["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]
    assert quarantined["upstream_quality_flags_json"] == '["NOT_A_DOCS_FLAG"]'
    assert quarantined["payload"] == core.history[1]["payload"]
    assert "quality_flags" in quarantined["reason"]


def test_quality_left_boundary_is_included_and_right_boundary_is_excluded() -> None:
    core = open_engine()
    raw_trade(core, receive_s=2, agg_id=1, quantity="0.30")
    raw_trade(core, receive_s=31, agg_id=2, quantity="0.20", quality_flags=["GAP_KNOWN"])
    cutoff(core, cutoff_s=31)
    cutoff(core, cutoff_s=61)
    cutoff(core, cutoff_s=62)
    assert feature(core, cutoff_s=31, window_s=30)["quality_flags"] == []
    assert feature(core, cutoff_s=31, window_s=30)["volume_base"] == Decimal("0.30")
    assert_null_flow(feature(core, cutoff_s=61, window_s=30))
    assert feature(core, cutoff_s=61, window_s=30)["quality_flags"] == ["GAP_KNOWN"]
    assert feature(core, cutoff_s=62, window_s=30)["volume_base"] == Decimal(0)


def test_future_quality_counterfactual_keeps_past_features_identical() -> None:
    def run(future_flags: list[str]) -> Core:
        core = open_engine()
        raw_trade(core, receive_s=10, agg_id=7, quantity="0.30")
        cutoff(core, cutoff_s=31)
        raw_trade(
            core,
            receive_s=40,
            agg_id=7,
            quantity="0.30",
            quality_flags=future_flags,
            payload_updates={"E": 10000, "T": 10001},
        )
        cutoff(core, cutoff_s=41)
        return core

    healthy, degraded = run([]), run(["GAP_KNOWN"])
    assert feature(healthy, cutoff_s=31, window_s=30) == feature(degraded, cutoff_s=31, window_s=30)
    assert feature(healthy, cutoff_s=41, window_s=30)["volume_base"] == Decimal(0)
    assert_null_flow(feature(degraded, cutoff_s=41, window_s=30))


def test_rebuild_raw_prefix_preserves_cross_session_quality_without_recount(tmp_path) -> None:
    from copy import deepcopy
    from btc_microstructure.offline_storage import Store

    core = open_engine()
    raw_trade(core, receive_s=10, agg_id=7, quantity="0.30")
    original = deepcopy(core.history[-1])
    add(core, "SESSION_CLOSE", 11 * NS)
    add(core, "GAP_CLOSE", 12 * NS)
    add(core, "SESSION_OPEN", 12 * NS, session_id="s2")
    store = Store(tmp_path, max_age_ns=60 * NS)
    for row in core.history:
        store.append(row)
    store.close()
    restored = engine()
    for row in Store(tmp_path).read_rows():
        restored.process(row)
    restored.process(
        {
            **original,
            "session_id": "s2",
            "ingest_sequence": restored.cursor + 1,
            "ts_receive_ns": 20 * NS,
            "quality_flags": ["GAP_KNOWN"],
        }
    )
    cutoff(restored, cutoff_s=42)
    cutoff(restored, cutoff_s=51)
    assert len(restored.normalized) == 1
    assert restored.normalized[0]["quality_flags"] == []
    assert restored.quarantine[0]["quality_flags"] == ["DUPLICATE", "GAP_KNOWN"]
    assert_null_flow(feature(restored, cutoff_s=42, window_s=30))
    assert feature(restored, cutoff_s=42, window_s=5)["volume_base"] == Decimal(0)
    assert feature(restored, cutoff_s=51, window_s=30)["volume_base"] == Decimal(0)


@pytest.mark.parametrize("include_receipt_at_cutoff", [False, True])
def test_duplicate_quality_at_cutoff_uses_high_water_for_existing_economic_event(
    include_receipt_at_cutoff: bool,
) -> None:
    core = open_engine()
    raw_trade(core, receive_s=2, agg_id=7, quantity="0.30")
    old_high = core.cursor
    raw_trade(
        core,
        receive_s=31,
        agg_id=7,
        quantity="0.30",
        quality_flags=["GAP_KNOWN"],
        payload_updates={"E": 2000, "T": 2001},
    )
    cutoff(core, cutoff_s=31, high=core.cursor if include_receipt_at_cutoff else old_high)
    row = feature(core, cutoff_s=31, window_s=30)
    if include_receipt_at_cutoff:
        assert_null_flow(row)
        assert row["quality_flags"] == ["GAP_KNOWN"]
    else:
        assert row["volume_base"] == Decimal("0.30")
        assert row["agg_message_count"] == 1
        assert row["quality_flags"] == []
