from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from btc_microstructure.offline import OfflineConfig, fixture_rows
from btc_microstructure.offline_core import Core, NS


def metadata() -> dict[str, str]:
    return {
        "venue": "binance",
        "market_type": "um_perpetual",
        "instrument_id": "binance:um_perpetual:BTCUSDT",
        "source_stream": "um_agg_trade",
        "symbol": "BTCUSDT",
        "metadata_version": "synthetic-r103",
        "quantity_unit": "BTC",
        "unit_evidence_id": "SYNTHETIC:r103",
        "evidence_kind": "SYNTHETIC",
        "price_tick": "0.1",
        "quantity_step": "0.01",
        "minimum_quantity": "0.01",
    }


def config_for(path: Path, *, window_seconds: list[int] | None = None) -> OfflineConfig:
    return OfflineConfig(
        source_path=path.with_suffix(".yaml"),
        grid_seconds=1,
        window_seconds=window_seconds or [5],
        horizon_seconds=[5, 30],
        synthetic_input=path,
        output_root=path.parent / "unused-output",
        run_id="synthetic-r103",
        metadata=metadata(),
        max_events=100,
        max_fixture_bytes=100_000,
        max_output_grid_rows=100,
        min_free_bytes=0,
    )


def raw(
    sequence: int,
    *,
    receive_ns: int = 2 * NS,
    qty: str = "0.10",
    trade_id: int = 1,
    buyer_is_maker: bool = False,
) -> dict[str, Any]:
    return {
        "kind": "RAW",
        "session_id": "s",
        "ingest_sequence": sequence,
        "ts_receive_ns": receive_ns,
        "raw_ref": f"raw:{sequence}",
        "payload": {
            "e": "aggTrade",
            "s": "BTCUSDT",
            "a": trade_id,
            "f": trade_id,
            "l": trade_id,
            "E": receive_ns // 1_000_000,
            "T": receive_ns // 1_000_000,
            "p": "100.0",
            "q": qty,
            "m": buyer_is_maker,
        },
    }


def open_session(sequence: int = 1, receive_ns: int = 1) -> dict[str, Any]:
    return {
        "kind": "SESSION_OPEN",
        "session_id": "s",
        "ingest_sequence": sequence,
        "ts_receive_ns": receive_ns,
    }


def close_session(sequence: int, receive_ns: int) -> dict[str, Any]:
    return {
        "kind": "SESSION_CLOSE",
        "session_id": "s",
        "ingest_sequence": sequence,
        "ts_receive_ns": receive_ns,
    }


def schedule(cutoff_ns: int = 6 * NS) -> dict[str, int]:
    return {"start_ns": cutoff_ns, "end_ns": cutoff_ns, "publish_delay_ns": 1}


def write_fixture(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"events": events, "feature_schedule": schedule()}),
        encoding="utf-8",
    )


def expanded_rows(path: Path, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    write_fixture(path, events)
    return fixture_rows(config_for(path))


def process_rows(config: OfflineConfig, rows: list[dict[str, Any]]) -> Core:
    core = Core(config.core_config(), config.metadata)
    for row in rows:
        core.process(row)
    return core


def test_schedule_preserves_declared_order_when_fixture_array_is_permuted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.json"
    ordered = [open_session(), raw(2, qty="0.10"), raw(3, qty="0.20", trade_id=2)]

    expected = expanded_rows(path, ordered)
    actual = expanded_rows(path, [ordered[0], ordered[2], ordered[1]])

    assert [row["raw_ref"] for row in actual if row["kind"] == "RAW"] == ["raw:2", "raw:3"]
    assert actual == expected


def test_same_id_conflict_keeps_declared_first_raw_when_array_is_permuted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.json"
    rows = expanded_rows(path, [open_session(), raw(3, qty="0.20"), raw(2, qty="0.10")])

    core = process_rows(config_for(path), rows)

    assert [row["raw_ref"] for row in rows if row["kind"] == "RAW"] == ["raw:2", "raw:3"]
    assert core.normalized[0]["raw_ref"] == "raw:2"
    assert core.normalized[0]["quantity_base"] == Decimal("0.10")
    assert core.quarantine[0]["raw_ref"] == "raw:3"
    assert core.quarantine[0]["quality_flags"] == ["DUPLICATE_CONFLICT"]


def test_generated_cutoff_does_not_swap_explicit_session_boundary_events(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.json"
    boundary = 6 * NS
    rows = expanded_rows(
        path,
        [
            open_session(),
            raw(2, receive_ns=2 * NS, qty="0.10"),
            close_session(3, boundary),
            {**open_session(4, boundary), "session_id": "s"},
        ],
    )

    same_time = [row for row in rows if row["ts_receive_ns"] == boundary]

    assert [row["kind"] for row in same_time] == [
        "FEATURE_CUTOFF",
        "SESSION_CLOSE",
        "SESSION_OPEN",
    ]
    assert [row["source_ingest_sequence"] for row in same_time[1:]] == [3, 4]
    assert same_time[0]["input_cursor_source_ingest_sequence"] == 2


def test_cutoff_uses_left_closed_right_open_window_and_traceable_highwater(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.json"
    cutoff_ns = 6 * NS
    rows = expanded_rows(
        path,
        [
            open_session(),
            raw(2, receive_ns=5 * NS, qty="0.10"),
            raw(3, receive_ns=cutoff_ns, qty="0.20", trade_id=2),
        ],
    )
    config = config_for(path)

    cutoff = next(row for row in rows if row["kind"] == "FEATURE_CUTOFF")
    core = process_rows(config, rows)

    assert cutoff["input_cursor_source_ingest_sequence"] == 2
    assert core.features[0]["volume_base"] == Decimal("0.10")
    assert core.features[0]["agg_message_count"] == 1


def test_runtime_highwater_maps_to_original_sequence_after_inserted_controls(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.json"
    events = [open_session(), raw(3, trade_id=2, qty="0.20"), raw(2, qty="0.10")]
    path.write_text(
        json.dumps(
            {
                "events": events,
                "feature_schedule": {
                    "start_ns": NS,
                    "end_ns": 6 * NS,
                    "publish_delay_ns": 1,
                },
            }
        )
    )
    config = config_for(path)
    rows = fixture_rows(config)
    raw_rows = [row for row in rows if row["kind"] == "RAW"]
    assert [row["source_ingest_sequence"] for row in raw_rows] == [2, 3]
    assert [row["ingest_sequence"] for row in raw_rows] == [5, 6]
    cutoff = next(
        row for row in rows if row["kind"] == "FEATURE_CUTOFF" and row["cutoff_ns"] == 3 * NS
    )
    assert cutoff["input_cursor"] == 6
    assert cutoff["input_cursor_source_ingest_sequence"] == 3
    engine = process_rows(config, rows)
    fixed = next(row for row in engine.features if row["feature_cutoff_ns"] == 6 * NS)
    assert fixed["volume_base"] == Decimal("0.30")
    assert fixed["agg_message_count"] == 2


def test_reversed_session_boundary_serialization_keeps_pre_boundary_snapshot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.json"
    source_events = [open_session(), raw(2), close_session(3, 6 * NS), open_session(4, 6 * NS)]
    expected = expanded_rows(path, source_events)
    actual = expanded_rows(
        path, [source_events[3], source_events[2], source_events[1], source_events[0]]
    )
    assert actual == expected
    engine = process_rows(config_for(path), actual)
    assert engine.features[0]["volume_base"] == Decimal("0.10")
    assert engine.features[0]["quality_flags"] == []
    assert engine.gaps == [[6 * NS, None]]
