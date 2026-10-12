from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
import tempfile

from btc_microstructure.offline import OfflineConfig, fixture_rows
from btc_microstructure.offline_core import Core, NS


def metadata():
    return {
        "venue": "binance",
        "market_type": "um_perpetual",
        "instrument_id": "binance:um_perpetual:BTCUSDT",
        "source_stream": "um_agg_trade",
        "symbol": "BTCUSDT",
        "metadata_version": "synthetic-review-gap",
        "quantity_unit": "BTC",
        "unit_evidence_id": "SYNTHETIC:review",
        "evidence_kind": "SYNTHETIC",
        "price_tick": "0.1",
        "quantity_step": "0.01",
        "minimum_quantity": "0.01",
    }


def raw(seq: int, qty: str, maker: bool, flags: list[str] | None = None) -> dict:
    return {
        "kind": "RAW",
        "session_id": "s",
        "ingest_sequence": seq,
        "ts_receive_ns": 40 * NS + seq,
        "raw_ref": f"raw:{seq}",
        "quality_flags": flags or [],
        "payload": {
            "e": "aggTrade",
            "s": "BTCUSDT",
            "a": seq,
            "f": seq,
            "l": seq,
            "E": 31000,
            "T": 31000,
            "p": "100.0",
            "q": qty,
            "m": maker,
        },
    }


def config(path: Path) -> OfflineConfig:
    return OfflineConfig(
        source_path=path.with_suffix(".yaml"),
        grid_seconds=1,
        window_seconds=[30],
        horizon_seconds=[5, 30],
        synthetic_input=path,
        output_root=path.parent / "unused-output",
        run_id="r104-gap-flag",
        metadata=metadata(),
        max_events=100,
        max_fixture_bytes=100_000,
        max_output_grid_rows=100,
        min_free_bytes=0,
    )


def run(flags: list[str] | None) -> dict:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "fixture.json"
        path.write_text(
            json.dumps(
                {
                    "events": [
                        {"kind": "SESSION_OPEN", "session_id": "s", "ingest_sequence": 1, "ts_receive_ns": 0},
                        raw(2, "0.30", False, flags),
                        raw(3, "0.10", True, None),
                    ],
                    "feature_schedule": {"start_ns": 62 * NS, "end_ns": 62 * NS, "publish_delay_ns": 1},
                }
            )
        )
        cfg = config(path)
        engine = Core(cfg.core_config(), cfg.metadata)
        rows = fixture_rows(cfg)
        for row in rows:
            engine.process(row)
        feature = engine.features[0]
        return {
            "input_raw_flags": flags or [],
            "normalized_first_flags": engine.normalized[0]["quality_flags"],
            "feature_quality_flags": feature["quality_flags"],
            "feature_volume_base": feature["volume_base"],
            "feature_trade_delta_base": feature["trade_delta_base"],
            "feature_agg_message_count": feature["agg_message_count"],
        }

print(json.dumps({"healthy_control": run(None), "gap_flagged_raw": run(["GAP_KNOWN"])}, default=str, sort_keys=True, indent=2))
