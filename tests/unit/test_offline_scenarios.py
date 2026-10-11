from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from btc_microstructure.config import ConfigError
from btc_microstructure.offline import OfflineConfig, replay_run, run_synthetic
from btc_microstructure.offline_core import Core
from btc_microstructure.offline_storage import Store


NS = 1_000_000_000


def payload(
    *,
    agg_id: int,
    quantity: str = "0.10",
    price: str = "100.0",
    buyer_is_maker: bool = False,
    event_ms: int = 30_100,
    trade_ms: int = 30_090,
) -> dict[str, Any]:
    return {
        "e": "aggTrade",
        "E": event_ms,
        "T": trade_ms,
        "s": "BTCUSDT",
        "a": agg_id,
        "p": price,
        "q": quantity,
        "f": agg_id * 10,
        "l": agg_id * 10,
        "m": buyer_is_maker,
    }


def raw_event(
    *,
    seq: int,
    receive_ns: int,
    agg_id: int,
    session_id: str = "syn-a",
    payload_value: Any | None = None,
    **updates: Any,
) -> dict[str, Any]:
    return {
        "kind": "RAW",
        "session_id": session_id,
        "ingest_sequence": seq,
        "raw_ref": f"fixture:{seq}",
        "ts_receive_ns": receive_ns,
        "source_time_unit": "ms",
        "payload": payload_value
        if payload_value is not None
        else payload(agg_id=agg_id, **updates),
    }


def fixture(
    events: list[dict[str, Any]], feature_schedule: dict[str, int] | None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "fixture_kind": "synthetic_offline_scenarios",
        "description": "unit-test synthetic fixture; not live Binance evidence",
        "events": events,
    }
    if feature_schedule is not None:
        result["feature_schedule"] = feature_schedule
    return result


def config_path(
    tmp_path: Path,
    fixture_payload: dict[str, Any],
    *,
    grid_seconds: int = 1,
    window_seconds: list[int] | None = None,
    horizon_seconds: list[int] | None = None,
    **updates: Any,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture_file = tmp_path / "fixture.json"
    fixture_file.write_text(json.dumps(fixture_payload, sort_keys=True), encoding="utf-8")
    raw: dict[str, Any] = {
        "input_mode": "synthetic",
        "source_stream": "um_agg_trade",
        "time_view": "as_received",
        "grid_seconds": grid_seconds,
        "window_seconds": window_seconds or [30],
        "horizon_seconds": horizon_seconds or [30],
        "synthetic_input": str(fixture_file),
        "output_root": str(tmp_path / "configured-output"),
        "run_id": "scenario-test",
        "metadata": {
            "venue": "binance",
            "market_type": "um_perpetual",
            "instrument_id": "binance:um_perpetual:BTCUSDT",
            "source_stream": "um_agg_trade",
            "symbol": "BTCUSDT",
            "metadata_version": "synthetic-btc-v1",
            "quantity_unit": "BTC",
            "unit_evidence_id": "SYNTHETIC:manual-btc",
            "evidence_kind": "SYNTHETIC",
            "price_tick": "0.10",
            "quantity_step": "0.01",
            "minimum_quantity": "0.01",
        },
        "limits": {
            "max_events": 1000,
            "max_fixture_bytes": 1000000,
            "max_output_grid_rows": 1000,
        },
    }
    for path, value in updates.items():
        node = raw
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    path = tmp_path / "offline.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def load_config(tmp_path: Path, fixture_payload: dict[str, Any], **kwargs: Any) -> OfflineConfig:
    return OfflineConfig.load(config_path(tmp_path, fixture_payload, **kwargs))


def feature_at(
    rows: list[dict[str, Any]], cutoff_ns: int, window_seconds: int = 30
) -> dict[str, Any]:
    [row] = [
        item
        for item in rows
        if item["feature_cutoff_ns"] == cutoff_ns and item["window_seconds"] == window_seconds
    ]
    return row


def simple_schedule(
    *, start: int = 31 * NS, end: int = 31 * NS, delay: int = 1_000_000
) -> dict[str, int]:
    return {"start_ns": start, "end_ns": end, "grid_ns": NS, "publish_delay_ns": delay}


def test_raw_is_committed_before_core_raw_crash(tmp_path, monkeypatch) -> None:
    data = fixture(
        [
            {
                "kind": "SESSION_OPEN",
                "session_id": "syn-a",
                "ingest_sequence": 1,
                "ts_receive_ns": 1,
            },
            raw_event(seq=2, receive_ns=31 * NS, agg_id=1, quantity="0.10"),
        ],
        None,
    )
    config = load_config(tmp_path, data)
    original = Core.process

    def crash_on_raw(self: Core, row: dict[str, Any]) -> None:
        if row["kind"] == "RAW":
            raise RuntimeError("synthetic core crash after raw commit")
        original(self, row)

    monkeypatch.setattr(Core, "process", crash_on_raw)

    with pytest.raises(RuntimeError, match="synthetic core crash"):
        run_synthetic(config, tmp_path / "run")

    committed_rows = Store(tmp_path / "run").read_rows()
    assert [row["kind"] for row in committed_rows] == ["SESSION_OPEN", "RAW"]
    assert json.loads(committed_rows[1]["payload"]) == payload(agg_id=1, quantity="0.10")


def test_malformed_json_raw_is_quarantined_and_persisted(tmp_path) -> None:
    data = fixture(
        [
            {
                "kind": "SESSION_OPEN",
                "session_id": "syn-a",
                "ingest_sequence": 1,
                "ts_receive_ns": 1,
            },
            raw_event(seq=2, receive_ns=31 * NS, agg_id=1, payload_value="{"),
        ],
        simple_schedule(start=32 * NS, end=32 * NS),
    )
    config = load_config(tmp_path, data)

    run_synthetic(config, tmp_path / "run")

    store = Store(tmp_path / "run")
    raw_rows = store.read_rows()
    quarantine = store.read_table("quarantine")
    features = store.read_table("features")
    malformed = next(row for row in raw_rows if row["kind"] == "RAW")
    assert malformed["payload"] == "{"
    assert quarantine[0]["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]
    assert quarantine[0]["payload"] == "{"
    assert feature_at(features, 32 * NS)["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]


def test_close_without_sleep_flushes_finite_rows(tmp_path) -> None:
    store = Store(tmp_path / "store")
    store.append(
        {"kind": "SESSION_OPEN", "ingest_sequence": 1, "ts_receive_ns": 10, "quality_flags": []}
    )

    store.close()

    assert Store(tmp_path / "store").read_rows() == [
        {"kind": "SESSION_OPEN", "ingest_sequence": 1, "ts_receive_ns": 10, "quality_flags": []}
    ]


def test_restarted_core_from_store_deduplicates_duplicate_id_across_restart(tmp_path) -> None:
    first = fixture(
        [
            {
                "kind": "SESSION_OPEN",
                "session_id": "syn-a",
                "ingest_sequence": 1,
                "ts_receive_ns": 1,
            },
            raw_event(seq=2, receive_ns=31 * NS, agg_id=9, quantity="0.10"),
        ],
        None,
    )
    config = load_config(tmp_path, first)
    run_synthetic(config, tmp_path / "first")
    recovered_rows = Store(tmp_path / "first").read_rows()
    recovered = Core(config.core_config(), config.metadata)
    for row in recovered_rows:
        recovered.process(row)
    recovered.process(
        {
            "kind": "SESSION_CLOSE",
            "session_id": "syn-a",
            "ingest_sequence": 3,
            "ts_receive_ns": 32 * NS,
        }
    )
    recovered.process(
        {"kind": "GAP_CLOSE", "session_id": "syn-a", "ingest_sequence": 4, "ts_receive_ns": 33 * NS}
    )
    recovered.process(
        {
            "kind": "SESSION_OPEN",
            "session_id": "syn-b",
            "ingest_sequence": 5,
            "ts_receive_ns": 34 * NS,
        }
    )
    row = raw_event(seq=6, receive_ns=35 * NS, agg_id=9, quantity="0.10", session_id="syn-b")
    row["payload"] = json.dumps(row["payload"], sort_keys=True, separators=(",", ":"))
    row.update(
        {
            "schema_version": recovered_rows[1]["schema_version"],
            "adapter_version": recovered_rows[1]["adapter_version"],
            "venue": recovered_rows[1]["venue"],
            "market_type": recovered_rows[1]["market_type"],
            "instrument_id": recovered_rows[1]["instrument_id"],
            "source_stream": recovered_rows[1]["source_stream"],
            "source_origin": "live_ws",
            "metadata_version": config.metadata["metadata_version"],
            "evidence_kind": "SYNTHETIC",
            "quality_flags": [],
        }
    )
    recovered.process(row)

    assert len(recovered.normalized) == 1
    assert recovered.quarantine[-1]["quality_flags"] == ["DUPLICATE"]


def test_missing_publication_keeps_pending_available_null_and_replay_unverified(tmp_path) -> None:
    data = fixture(
        [
            {
                "kind": "SESSION_OPEN",
                "session_id": "syn-a",
                "ingest_sequence": 1,
                "ts_receive_ns": 1,
            },
            raw_event(seq=2, receive_ns=31 * NS, agg_id=1, quantity="0.10"),
            {
                "kind": "FEATURE_CUTOFF",
                "session_id": "syn-a",
                "ingest_sequence": 3,
                "ts_receive_ns": 32 * NS,
                "cutoff_ns": 32 * NS,
                "input_cursor": 2,
            },
        ],
        None,
    )
    config = load_config(tmp_path, data)

    run_synthetic(config, tmp_path / "run")
    replay_summary = replay_run(config, tmp_path / "run", tmp_path / "replay")

    features = Store(tmp_path / "run").read_table("features")
    assert features[0]["feature_available_ns"] is None
    assert features[0]["publication_status"] == "UNVERIFIED"
    assert replay_summary["feature_available_time_source"] == "UNVERIFIED_NO_PUBLISH_LOG"
    assert replay_summary["status"] == "PASS"


def test_future_horizon_change_uses_same_raw_and_same_feature_values_with_new_hash(
    tmp_path,
) -> None:
    data = fixture(
        [
            {
                "kind": "SESSION_OPEN",
                "session_id": "syn-a",
                "ingest_sequence": 1,
                "ts_receive_ns": 1,
            },
            raw_event(seq=2, receive_ns=31 * NS, agg_id=1, quantity="0.10"),
        ],
        simple_schedule(start=32 * NS, end=32 * NS),
    )
    first = load_config(tmp_path / "first", data, horizon_seconds=[30])
    second = load_config(tmp_path / "second", data, horizon_seconds=[5, 30, 300])

    run_synthetic(first, tmp_path / "run-first")
    run_synthetic(second, tmp_path / "run-second")

    first_store = Store(tmp_path / "run-first")
    second_store = Store(tmp_path / "run-second")
    first_raw = first_store.read_rows()
    second_raw = second_store.read_rows()
    first_feature = feature_at(first_store.read_table("features"), 32 * NS)
    second_feature = feature_at(second_store.read_table("features"), 32 * NS)
    assert [row["kind"] for row in first_raw] == [row["kind"] for row in second_raw]
    assert [row.get("payload") for row in first_raw] == [row.get("payload") for row in second_raw]
    assert first_feature["volume_base"] == second_feature["volume_base"] == Decimal("0.10")
    assert (
        first_feature["trade_delta_base"] == second_feature["trade_delta_base"] == Decimal("0.10")
    )
    assert first_feature["config_hash"] != second_feature["config_hash"]


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("source_stream", "spot_agg_trade", "source_stream"),
        ("time_view", "event_time", "time_view"),
        ("grid_seconds", True, "grid_seconds"),
        ("metadata.unit_evidence_id", True, "metadata.unit_evidence_id"),
    ],
)
def test_offline_config_rejects_unsupported_source_timeview_and_fake_booleans(
    tmp_path, path: str, value: Any, message: str
) -> None:
    data = fixture(
        [{"kind": "SESSION_OPEN", "session_id": "syn-a", "ingest_sequence": 1, "ts_receive_ns": 1}],
        None,
    )

    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path, data, **{path: value})


def test_two_second_grid_uses_exact_cutoff_and_publication_delay(tmp_path) -> None:
    data = fixture(
        [
            {
                "kind": "SESSION_OPEN",
                "session_id": "syn-a",
                "ingest_sequence": 1,
                "ts_receive_ns": 1,
            },
            raw_event(seq=2, receive_ns=32 * NS - 1, agg_id=1, quantity="0.10"),
            raw_event(seq=3, receive_ns=32 * NS, agg_id=2, quantity="0.20"),
        ],
        {"start_ns": 32 * NS, "end_ns": 34 * NS, "grid_ns": 2 * NS, "publish_delay_ns": 5},
    )
    config = load_config(tmp_path, data, grid_seconds=2)

    run_synthetic(config, tmp_path / "run")

    features = Store(tmp_path / "run").read_table("features")
    row_32 = feature_at(features, 32 * NS)
    row_34 = feature_at(features, 34 * NS)
    assert row_32["volume_base"] == Decimal("0.10")
    assert row_32["feature_available_ns"] == 32 * NS + 5
    assert row_34["volume_base"] == Decimal("0.30")
    assert row_34["feature_available_ns"] == 34 * NS + 5


def test_aggregate_id_jump_does_not_fabricate_gap_quality(tmp_path) -> None:
    data = fixture(
        [
            {
                "kind": "SESSION_OPEN",
                "session_id": "syn-a",
                "ingest_sequence": 1,
                "ts_receive_ns": 1,
            },
            raw_event(seq=2, receive_ns=31 * NS, agg_id=1, quantity="0.10"),
            raw_event(seq=3, receive_ns=32 * NS, agg_id=1000, quantity="0.20"),
        ],
        simple_schedule(start=33 * NS, end=33 * NS),
    )
    config = load_config(tmp_path, data)

    run_synthetic(config, tmp_path / "run")

    feature = feature_at(Store(tmp_path / "run").read_table("features"), 33 * NS)
    assert feature["quality_flags"] == []
    assert feature["volume_base"] == Decimal("0.30")
    assert feature["agg_message_count"] == 2
