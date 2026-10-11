from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from btc_microstructure.offline_core import Core, NS


def ts(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * NS)


def metadata(**updates):
    data = {
        "metadata_version": "synthetic-btc-v1",
        "quantity_unit": "BTC",
        "evidence_kind": "SYNTHETIC",
        "unit_evidence_id": "SYNTHETIC:manual-btc",
        "price_tick": "0.10",
        "quantity_step": "0.01",
        "minimum_quantity": "0.01",
    }
    data.update(updates)
    return data


def core(*, windows=(30,), metadata_updates=None, config_updates=None):
    config = {"time_view": "as_received", "window_seconds": list(windows)}
    if config_updates:
        config.update(config_updates)
    return Core(config, metadata(**(metadata_updates or {})))


def open_session(engine, seq=1, now=0, session="s1"):
    engine.process(
        {
            "kind": "SESSION_OPEN",
            "session_id": session,
            "ingest_sequence": seq,
            "ts_receive_ns": now,
        }
    )


def raw_trade(
    *,
    seq,
    receive_ns,
    agg_id=1,
    quantity="0.30",
    price="100.00",
    buyer_is_maker=False,
    first_id=10,
    last_id=11,
    session="s1",
    evidence_kind="SYNTHETIC",
    source_time_unit="ms",
    event_time=1000,
    trade_time=1001,
    nq=None,
):
    payload = {
        "e": "aggTrade",
        "s": "BTCUSDT",
        "a": agg_id,
        "p": price,
        "q": quantity,
        "f": first_id,
        "l": last_id,
        "m": buyer_is_maker,
        "E": event_time,
        "T": trade_time,
    }
    if nq is not None:
        payload["nq"] = nq
    return {
        "kind": "RAW",
        "session_id": session,
        "ingest_sequence": seq,
        "ts_receive_ns": receive_ns,
        "payload": json.dumps(payload, separators=(",", ":")),
        "evidence_kind": evidence_kind,
        "source_time_unit": source_time_unit,
        "source_origin": "archive",
    }


def cutoff(engine, *, seq, now, cutoff_ns, high):
    engine.process(
        {
            "kind": "FEATURE_CUTOFF",
            "ingest_sequence": seq,
            "ts_receive_ns": now,
            "cutoff_ns": cutoff_ns,
            "input_cursor": high,
        }
    )


def publish(engine, *, seq, available_ns, cutoff_ns):
    engine.process(
        {
            "kind": "FEATURE_PUBLISHED",
            "ingest_sequence": seq,
            "ts_receive_ns": available_ns,
            "cutoff_ns": cutoff_ns,
            "feature_available_ns": available_ns,
        }
    )


def first_feature(engine, window=30):
    return next(row for row in engine.features if row["window_seconds"] == window)


def test_normalizes_m_false_as_buy_and_m_true_as_sell() -> None:
    engine = core()
    open_session(engine, now=0)

    engine.process(raw_trade(seq=2, receive_ns=31 * NS, agg_id=1, buyer_is_maker=False))
    engine.process(raw_trade(seq=3, receive_ns=32 * NS, agg_id=2, buyer_is_maker=True))

    assert [row["aggressor_side"] for row in engine.normalized] == ["BUY", "SELL"]
    assert [row["signed_quantity_base"] for row in engine.normalized] == [
        Decimal("0.30"),
        Decimal("-0.30"),
    ]


def test_preserves_decimal_q_and_nq_without_double_counting() -> None:
    engine = core()
    open_session(engine, now=0)
    engine.process(raw_trade(seq=2, receive_ns=31 * NS, quantity="0.30", nq="0.20"))
    cutoff(engine, seq=3, now=61 * NS, cutoff_ns=61 * NS, high=2)
    publish(engine, seq=4, available_ns=62 * NS, cutoff_ns=61 * NS)

    feature = first_feature(engine)
    trade = engine.normalized[0]
    assert trade["quantity_base"] == Decimal("0.30")
    assert trade["normal_quantity_base"] == Decimal("0.20")
    assert feature["volume_base"] == Decimal("0.30")
    assert feature["agg_message_count"] == 1


def test_quarantines_invalid_payload_and_marks_window_unavailable() -> None:
    engine = core()
    open_session(engine, now=0)
    bad = raw_trade(seq=2, receive_ns=31 * NS)
    bad["payload"] = '{"e":"aggTrade","s":"BTCUSDT","a":1,"p":"100.00","q":0.30}'
    engine.process(bad)
    cutoff(engine, seq=3, now=61 * NS, cutoff_ns=61 * NS, high=2)
    publish(engine, seq=4, available_ns=62 * NS, cutoff_ns=61 * NS)

    assert engine.quarantine[0]["payload"] == bad["payload"]
    assert engine.quarantine[0]["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]
    assert first_feature(engine)["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]


def test_unknown_unit_keeps_base_values_null() -> None:
    engine = core(metadata_updates={"quantity_unit": "UNKNOWN", "unit_evidence_id": None})
    open_session(engine, now=0)
    engine.process(raw_trade(seq=2, receive_ns=31 * NS))
    cutoff(engine, seq=3, now=61 * NS, cutoff_ns=61 * NS, high=2)
    publish(engine, seq=4, available_ns=62 * NS, cutoff_ns=61 * NS)

    assert engine.normalized[0]["quantity_unit"] == "UNKNOWN"
    assert engine.normalized[0]["quantity_base"] is None
    assert first_feature(engine)["quality_flags"] == ["UNIT_UNKNOWN"]
    assert first_feature(engine)["volume_base"] is None


def test_mismatched_row_evidence_does_not_use_synthetic_metadata() -> None:
    engine = core()
    open_session(engine, now=0)
    engine.process(raw_trade(seq=2, receive_ns=31 * NS, evidence_kind="LIVE"))
    cutoff(engine, seq=3, now=61 * NS, cutoff_ns=61 * NS, high=2)
    publish(engine, seq=4, available_ns=62 * NS, cutoff_ns=61 * NS)

    assert engine.normalized[0]["quantity_unit"] == "UNKNOWN"
    assert first_feature(engine)["quality_flags"] == ["UNIT_UNKNOWN"]


def test_duplicate_same_economic_key_across_sessions_is_not_counted_twice() -> None:
    engine = core()
    open_session(engine, seq=1, now=0, session="s1")
    engine.process(raw_trade(seq=2, receive_ns=31 * NS, agg_id=7, session="s1"))
    engine.process(
        {
            "kind": "SESSION_CLOSE",
            "session_id": "s1",
            "ingest_sequence": 3,
            "ts_receive_ns": 32 * NS,
        }
    )
    engine.process(
        {"kind": "GAP_CLOSE", "session_id": "s1", "ingest_sequence": 4, "ts_receive_ns": 33 * NS}
    )
    open_session(engine, seq=5, now=34 * NS, session="s2")
    engine.process(raw_trade(seq=6, receive_ns=35 * NS, agg_id=7, session="s2"))

    assert len(engine.normalized) == 1
    assert engine.quarantine[-1]["quality_flags"] == ["DUPLICATE"]


def test_conflicting_same_economic_key_across_sessions_is_quarantined() -> None:
    engine = core()
    open_session(engine, seq=1, now=0, session="s1")
    engine.process(raw_trade(seq=2, receive_ns=31 * NS, agg_id=7, quantity="0.30", session="s1"))
    engine.process(
        {
            "kind": "SESSION_CLOSE",
            "session_id": "s1",
            "ingest_sequence": 3,
            "ts_receive_ns": 32 * NS,
        }
    )
    engine.process(
        {"kind": "GAP_CLOSE", "session_id": "s1", "ingest_sequence": 4, "ts_receive_ns": 33 * NS}
    )
    open_session(engine, seq=5, now=34 * NS, session="s2")
    engine.process(raw_trade(seq=6, receive_ns=35 * NS, agg_id=7, quantity="0.40", session="s2"))

    assert len(engine.normalized) == 1
    assert engine.quarantine[-1]["quality_flags"] == ["DUPLICATE_CONFLICT"]
    assert engine.conflicts == [(35 * NS, 31 * NS)]


def test_window_includes_left_boundary_and_excludes_right_boundary() -> None:
    engine = core()
    open_session(engine, now=0)
    engine.process(raw_trade(seq=2, receive_ns=30 * NS, agg_id=1, quantity="0.10"))
    engine.process(raw_trade(seq=3, receive_ns=60 * NS, agg_id=2, quantity="0.20"))
    cutoff(engine, seq=4, now=60 * NS, cutoff_ns=60 * NS, high=3)
    publish(engine, seq=5, available_ns=61 * NS, cutoff_ns=60 * NS)

    assert first_feature(engine)["volume_base"] == Decimal("0.10")
    assert first_feature(engine)["agg_message_count"] == 1


def test_cutoff_high_water_excludes_later_same_timestamp_records() -> None:
    engine = core()
    open_session(engine, now=0)
    engine.process(raw_trade(seq=2, receive_ns=31 * NS, agg_id=1, quantity="0.10"))
    engine.process(raw_trade(seq=3, receive_ns=31 * NS, agg_id=2, quantity="0.20"))
    cutoff(engine, seq=4, now=61 * NS, cutoff_ns=61 * NS, high=2)
    publish(engine, seq=5, available_ns=62 * NS, cutoff_ns=61 * NS)

    assert first_feature(engine)["volume_base"] == Decimal("0.10")
    assert first_feature(engine)["input_cursor"] == 2


def test_windows_remain_valid_across_utc_midnight() -> None:
    start = ts("2026-10-11T23:59:20")
    trade_one = ts("2026-10-11T23:59:45")
    trade_two = ts("2026-10-11T23:59:58")
    cut = ts("2026-10-12T00:00:01")
    engine = core(windows=(30, 5))
    open_session(engine, now=start)
    engine.process(raw_trade(seq=2, receive_ns=trade_one, agg_id=1, quantity="0.10"))
    engine.process(raw_trade(seq=3, receive_ns=trade_two, agg_id=2, quantity="0.20"))
    cutoff(engine, seq=4, now=cut, cutoff_ns=cut, high=3)
    publish(engine, seq=5, available_ns=cut + NS, cutoff_ns=cut)

    assert first_feature(engine, 30)["volume_base"] == Decimal("0.30")
    assert first_feature(engine, 5)["volume_base"] == Decimal("0.20")


def test_warmup_window_outputs_null_values() -> None:
    engine = core()
    open_session(engine, now=10 * NS)
    cutoff(engine, seq=2, now=20 * NS, cutoff_ns=20 * NS, high=1)
    publish(engine, seq=3, available_ns=21 * NS, cutoff_ns=20 * NS)

    feature = first_feature(engine)
    assert feature["quality_flags"] == ["WARMUP"]
    assert feature["volume_base"] is None


def test_complete_healthy_empty_window_outputs_zero_volume_and_null_imbalance() -> None:
    engine = core()
    open_session(engine, now=0)
    cutoff(engine, seq=2, now=60 * NS, cutoff_ns=60 * NS, high=1)
    publish(engine, seq=3, available_ns=61 * NS, cutoff_ns=60 * NS)

    feature = first_feature(engine)
    assert feature["quality_flags"] == []
    assert feature["volume_base"] == Decimal(0)
    assert feature["trade_imbalance"] is None


def test_known_gap_keeps_reconnected_window_unavailable_until_gap_ages_out() -> None:
    engine = core()
    open_session(engine, now=0)
    engine.process({"kind": "GAP_OPEN", "ingest_sequence": 2, "ts_receive_ns": 10 * NS})
    engine.process({"kind": "GAP_CLOSE", "ingest_sequence": 3, "ts_receive_ns": 20 * NS})
    cutoff(engine, seq=4, now=25 * NS, cutoff_ns=25 * NS, high=3)
    publish(engine, seq=5, available_ns=26 * NS, cutoff_ns=25 * NS)
    cutoff(engine, seq=6, now=60 * NS, cutoff_ns=60 * NS, high=5)
    publish(engine, seq=7, available_ns=61 * NS, cutoff_ns=60 * NS)

    assert first_feature(engine)["quality_flags"] == ["WARMUP", "GAP_KNOWN"]
    assert engine.features[-1]["quality_flags"] == []


def test_source_time_precision_is_converted_without_inventing_extra_precision() -> None:
    engine = core()
    open_session(engine, now=0)
    engine.process(
        raw_trade(
            seq=2,
            receive_ns=31 * NS,
            source_time_unit="us",
            event_time=1_234_567,
            trade_time=1_234_568,
        )
    )

    assert engine.normalized[0]["ts_event_ns"] == 1_234_567_000
    assert engine.normalized[0]["ts_trade_ns"] == 1_234_568_000
    assert engine.normalized[0]["source_time_unit"] == "us"


def test_ms_and_us_time_units_can_represent_the_same_event_time() -> None:
    millisecond = core()
    microsecond = core()
    open_session(millisecond, now=0)
    open_session(microsecond, now=0)

    millisecond.process(
        raw_trade(
            seq=2, receive_ns=31 * NS, source_time_unit="ms", event_time=1234, trade_time=1235
        )
    )
    microsecond.process(
        raw_trade(
            seq=2,
            receive_ns=31 * NS,
            source_time_unit="us",
            event_time=1_234_000,
            trade_time=1_235_000,
        )
    )

    assert millisecond.normalized[0]["ts_event_ns"] == microsecond.normalized[0]["ts_event_ns"]
    assert millisecond.normalized[0]["ts_trade_ns"] == microsecond.normalized[0]["ts_trade_ns"]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ('{"e":"aggTrade","s":"BTCUSDT","a":1,"p":"100.00","q":"0.30","m":"false"}', "m:"),
        ("{", "json:"),
        (
            '{"e":"aggTrade","e":"aggTrade","s":"BTCUSDT","a":1,"p":"100.00","q":"0.30","m":false}',
            "json:",
        ),
        ('{"e":"aggTrade","s":"BTCUSDT","a":1,"p":"NaN","q":"0.30","m":false}', "p:"),
    ],
)
def test_invalid_json_and_non_strict_protocol_values_are_quarantined(
    payload: str, reason: str
) -> None:
    engine = core()
    open_session(engine, now=0)
    bad = raw_trade(seq=2, receive_ns=31 * NS)
    bad["payload"] = payload

    engine.process(bad)

    assert engine.quarantine[0]["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]
    assert reason in engine.quarantine[0]["reason"]


def test_normal_quantity_larger_than_total_quantity_is_quarantined() -> None:
    engine = core()
    open_session(engine, now=0)

    engine.process(raw_trade(seq=2, receive_ns=31 * NS, quantity="0.30", nq="0.31"))

    assert engine.normalized == []
    assert engine.quarantine[0]["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]
    assert "nq: exceeds q" in engine.quarantine[0]["reason"]


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"price": "100.05"}, "price_tick"),
        ({"quantity": "0.305"}, "quantity_step"),
    ],
)
def test_off_tick_or_off_step_trade_is_quarantined(updates: dict[str, str], reason: str) -> None:
    engine = core()
    open_session(engine, now=0)

    engine.process(raw_trade(seq=2, receive_ns=31 * NS, **updates))

    assert engine.normalized == []
    assert engine.quarantine[0]["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]
    assert reason in engine.quarantine[0]["reason"]


def test_high_precision_decimal_uses_exact_decimal_not_float() -> None:
    engine = core(
        metadata_updates={
            "price_tick": "0.000000000000000001",
            "quantity_step": "0.000000000000000001",
            "minimum_quantity": "0.000000000000000001",
        }
    )
    open_session(engine, now=0)

    engine.process(
        raw_trade(
            seq=2,
            receive_ns=31 * NS,
            price="1234567890.123456789123456789",
            quantity="0.123456789123456789",
        )
    )
    cutoff(engine, seq=3, now=61 * NS, cutoff_ns=61 * NS, high=2)
    publish(engine, seq=4, available_ns=62 * NS, cutoff_ns=61 * NS)

    assert engine.normalized[0]["price_quote"] == Decimal("1234567890.123456789123456789")
    assert engine.normalized[0]["quantity_base"] == Decimal("0.123456789123456789")
    assert first_feature(engine)["volume_base"] == Decimal("0.123456789123456789")


def test_source_symbol_market_or_stream_mismatch_is_quarantined() -> None:
    cases = [
        {"market_type": "spot"},
        {"source_stream": "spot_agg_trade"},
        {"payload": json.dumps({"e": "aggTrade", "s": "ETHUSDT"})},
    ]
    for index, updates in enumerate(cases, start=1):
        engine = core()
        open_session(engine, now=0)
        row = raw_trade(seq=2, receive_ns=31 * NS, agg_id=index)
        row.update(updates)

        engine.process(row)

        assert engine.normalized == []
        assert engine.quarantine[0]["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]


def test_future_raw_events_do_not_change_prior_high_water_feature() -> None:
    engine = core()
    open_session(engine, now=0)
    engine.process(raw_trade(seq=2, receive_ns=31 * NS, agg_id=1, quantity="0.10"))
    cutoff(engine, seq=3, now=61 * NS, cutoff_ns=61 * NS, high=2)
    publish(engine, seq=4, available_ns=62 * NS, cutoff_ns=61 * NS)
    before = first_feature(engine).copy()
    engine.process(raw_trade(seq=5, receive_ns=63 * NS, agg_id=2, quantity="0.90"))

    assert first_feature(engine) == before


def test_research_horizon_changes_do_not_change_past_feature_values() -> None:
    def run(horizons):
        engine = core(config_updates={"horizon_seconds": horizons})
        open_session(engine, now=0)
        engine.process(raw_trade(seq=2, receive_ns=31 * NS, agg_id=1, quantity="0.30"))
        engine.process(
            raw_trade(seq=3, receive_ns=32 * NS, agg_id=2, quantity="0.10", buyer_is_maker=True)
        )
        cutoff(engine, seq=4, now=61 * NS, cutoff_ns=61 * NS, high=3)
        publish(engine, seq=5, available_ns=62 * NS, cutoff_ns=61 * NS)
        return {
            key: first_feature(engine)[key]
            for key in (
                "buy_volume_base",
                "sell_volume_base",
                "volume_base",
                "trade_delta_base",
                "agg_message_count",
            )
        }

    assert run([30]) == run([5, 30, 300])


def test_future_conflict_does_not_rewrite_prior_publication_but_invalidates_overlap() -> None:
    engine = core()
    open_session(engine, now=0)
    engine.process(raw_trade(seq=2, receive_ns=31 * NS, agg_id=7, quantity="0.30"))
    cutoff(engine, seq=3, now=61 * NS, cutoff_ns=61 * NS, high=2)
    publish(engine, seq=4, available_ns=62 * NS, cutoff_ns=61 * NS)
    before = first_feature(engine).copy()
    engine.process(raw_trade(seq=5, receive_ns=63 * NS, agg_id=7, quantity="0.40"))
    cutoff(engine, seq=6, now=65 * NS, cutoff_ns=65 * NS, high=5)
    publish(engine, seq=7, available_ns=66 * NS, cutoff_ns=65 * NS)
    cutoff(engine, seq=8, now=94 * NS, cutoff_ns=94 * NS, high=7)
    publish(engine, seq=9, available_ns=95 * NS, cutoff_ns=94 * NS)

    assert first_feature(engine) == before
    assert engine.features[1]["quality_flags"] == ["DUPLICATE_CONFLICT"]
    assert engine.features[1]["volume_base"] is None
    assert engine.features[2]["quality_flags"] == []
    assert engine.features[2]["volume_base"] == Decimal(0)


def test_future_conflict_above_high_water_does_not_invalidate_snapshot_quality() -> None:
    engine = core()
    open_session(engine, now=0)
    engine.process(raw_trade(seq=2, receive_ns=31 * NS, agg_id=7, quantity="0.30"))
    engine.process(raw_trade(seq=3, receive_ns=32 * NS, agg_id=7, quantity="0.40"))
    cutoff(engine, seq=4, now=61 * NS, cutoff_ns=61 * NS, high=2)
    publish(engine, seq=5, available_ns=62 * NS, cutoff_ns=61 * NS)

    feature = first_feature(engine)
    assert feature["quality_flags"] == []
    assert feature["volume_base"] == Decimal("0.30")


def test_feature_available_time_requires_logged_publication() -> None:
    engine = core()
    open_session(engine, now=0)
    cutoff(engine, seq=2, now=60 * NS, cutoff_ns=60 * NS, high=1)

    pending = engine.pending_features[0]
    assert pending["feature_available_ns"] is None
    assert pending["publication_status"] == "UNVERIFIED"


def test_rejects_publication_before_cutoff_or_prior_receive_time() -> None:
    engine = core()
    open_session(engine, now=0)
    engine.process(raw_trade(seq=2, receive_ns=31 * NS))
    cutoff(engine, seq=3, now=61 * NS, cutoff_ns=61 * NS, high=2)

    with pytest.raises(ValueError, match="invalid historical publication|CLOCK_UNCERTAIN"):
        publish(engine, seq=4, available_ns=60 * NS, cutoff_ns=61 * NS)
