from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

import yaml

from btc_microstructure.cli import main
from btc_microstructure.offline import OfflineConfig, fixture_rows
from btc_microstructure.offline_storage import Store
from conftest import ROOT


def write_offline_config(tmp_path: Path, **updates: Any) -> Path:
    raw = yaml.safe_load((ROOT / "config/offline.synthetic.yaml").read_text())
    raw["synthetic_input"] = str(ROOT / "fixtures/synthetic/vertical_slice/warmed_delta.json")
    raw["output_root"] = str(tmp_path / "configured-output")
    for key, value in updates.items():
        node = raw
        parts = key.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    path = tmp_path / "offline.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def feature_at(rows: list[dict[str, Any]], cutoff_ns: int, window_seconds: int) -> dict[str, Any]:
    [row] = [
        item
        for item in rows
        if item["feature_cutoff_ns"] == cutoff_ns and item["window_seconds"] == window_seconds
    ]
    return row


def test_validate_offline_rejects_live_mode_before_any_transport(tmp_path, capsys):
    config_path = write_offline_config(tmp_path, input_mode="live")

    assert main(["validate-offline", str(config_path)]) == 1

    output = capsys.readouterr()
    assert "input_mode" in output.err
    assert "live collection is not enabled" in output.err


def test_synthetic_schedule_expands_every_configured_grid_second(tmp_path):
    config = OfflineConfig.load(write_offline_config(tmp_path))

    rows = fixture_rows(config)
    cutoffs = [row for row in rows if row["kind"] == "FEATURE_CUTOFF"]
    publishes = [row for row in rows if row["kind"] == "FEATURE_PUBLISHED"]

    assert len(cutoffs) == 35
    assert len(publishes) == 35
    assert [row["cutoff_ns"] for row in cutoffs[:3]] == [
        1_000_000_000,
        2_000_000_000,
        3_000_000_000,
    ]
    assert rows == sorted(rows, key=lambda row: row["ingest_sequence"])
    assert [row["ts_receive_ns"] for row in rows] == sorted(row["ts_receive_ns"] for row in rows)


def test_cli_collect_and_replay_round_trip_with_parquet_outputs(tmp_path):
    config_path = write_offline_config(tmp_path, **{"limits.min_free_bytes": 0})
    run_dir = tmp_path / "run"
    replay_dir = tmp_path / "replay"

    assert main(["validate-offline", str(config_path)]) == 0
    assert main(["collect", str(config_path), "--mode", "synthetic", "--output", str(run_dir)]) == 0
    assert (
        main(["replay", str(config_path), "--input", str(run_dir), "--output", str(replay_dir)])
        == 0
    )

    run_store = Store(run_dir)
    replay_store = Store(replay_dir)
    normalized = run_store.read_table("normalized_trades")
    quarantine = run_store.read_table("quarantine")
    features = run_store.read_table("features")
    replay_summary = json.loads((replay_dir / "summary.json").read_text())

    assert len(normalized) == 3
    assert len(quarantine) == 1
    assert quarantine[0]["quality_flags"] == ["DUPLICATE"]
    assert len(features) == 70
    row_30s = feature_at(features, 31_000_000_000, 30)
    assert row_30s["buy_volume_base"] == Decimal("0.500000000000000000")
    assert row_30s["sell_volume_base"] == Decimal("0.100000000000000000")
    assert row_30s["volume_base"] == Decimal("0.600000000000000000")
    assert row_30s["trade_delta_base"] == Decimal("0.400000000000000000")
    assert row_30s["trade_imbalance"] == 2 / 3
    assert row_30s["agg_message_count"] == 3
    assert row_30s["quality_flags"] == []
    assert replay_store.read_table("features") == features
    assert replay_summary["status"] == "PASS"
    assert replay_summary["feature_available_time_source"] == "FEATURE_PUBLISHED_LOG"
    assert isinstance(replay_summary["replay_elapsed_ns"], int)
    assert replay_summary["replay_elapsed_ns"] >= 0


def test_build_features_recomputes_from_raw_when_derived_tables_are_missing(tmp_path):
    config_path = write_offline_config(tmp_path, **{"limits.min_free_bytes": 0})
    run_dir = tmp_path / "run"
    raw_only = tmp_path / "raw-only"
    build_dir = tmp_path / "build"

    assert main(["collect", str(config_path), "--mode", "synthetic", "--output", str(run_dir)]) == 0
    raw_only.mkdir()
    shutil.copytree(run_dir / "raw", raw_only / "raw")
    (raw_only / "manifests").mkdir()
    for manifest in (run_dir / "manifests").glob("manifest-raw-*.json"):
        shutil.copy2(manifest, raw_only / "manifests" / manifest.name)
    for child in ("errors",):
        source = run_dir / child
        if source.exists():
            shutil.copytree(source, raw_only / child)

    assert (
        main(
            [
                "build-features",
                str(config_path),
                "--input",
                str(raw_only),
                "--output",
                str(build_dir),
            ]
        )
        == 0
    )

    source_store = Store(run_dir)
    build_store = Store(build_dir)
    assert build_store.read_table("features") == source_store.read_table("features")
    summary = json.loads((build_dir / "summary.json").read_text())
    assert summary["status"] == "PASS"
    assert isinstance(summary["build_elapsed_ns"], int)
    assert summary["build_elapsed_ns"] >= 0


def test_unknown_unit_keeps_quantities_null_and_does_not_upgrade_live_capability(tmp_path):
    config_path = write_offline_config(
        tmp_path,
        **{
            "metadata.quantity_unit": "UNKNOWN",
            "metadata.unit_evidence_id": None,
            "limits.min_free_bytes": 0,
        },
    )
    run_dir = tmp_path / "run"

    assert main(["collect", str(config_path), "--mode", "synthetic", "--output", str(run_dir)]) == 0

    store = Store(run_dir)
    normalized = store.read_table("normalized_trades")
    features = store.read_table("features")
    row_30s = feature_at(features, 31_000_000_000, 30)

    assert normalized[0]["quantity_base"] is None
    assert "UNIT_UNKNOWN" in normalized[0]["quality_flags"]
    assert row_30s["volume_base"] is None
    assert "UNIT_UNKNOWN" in row_30s["quality_flags"]


def test_replay_reports_fail_when_durable_uncertain_gap_exists(tmp_path):
    config_path = write_offline_config(tmp_path, **{"limits.min_free_bytes": 0})
    run_dir = tmp_path / "run"
    replay_dir = tmp_path / "replay"

    assert main(["collect", str(config_path), "--mode", "synthetic", "--output", str(run_dir)]) == 0
    errors = run_dir / "errors" / "storage_events.jsonl"
    errors.parent.mkdir(parents=True, exist_ok=True)
    errors.write_text(
        json.dumps({"event_type": "UNCERTAIN_GAP", "reason": "TEST_UNCERTAIN_TAIL"}) + "\n",
        encoding="utf-8",
    )

    assert (
        main(["replay", str(config_path), "--input", str(run_dir), "--output", str(replay_dir)])
        == 1
    )

    replay_summary = json.loads((replay_dir / "summary.json").read_text())
    assert replay_summary["status"] == "FAIL"
    assert replay_summary["recovery_events"][0]["reason"] == "TEST_UNCERTAIN_TAIL"


def test_replay_rejects_unsupported_schema_even_when_manifest_hash_matches(tmp_path, capsys):
    config_path = write_offline_config(tmp_path, **{"limits.min_free_bytes": 0})
    run_dir = tmp_path / "run"
    replay_dir = tmp_path / "replay"

    assert main(["collect", str(config_path), "--mode", "synthetic", "--output", str(run_dir)]) == 0
    manifest_path = next((run_dir / "manifests").glob("manifest-raw-*.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_path = run_dir / manifest["path"]
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["schema_version"] = "unsupported-r1-03-schema"
    raw_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n")
    manifest["sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    assert (
        main(["replay", str(config_path), "--input", str(run_dir), "--output", str(replay_dir)])
        == 1
    )

    output = capsys.readouterr()
    assert "unsupported raw/control schema_version" in output.err


def test_operational_limits_are_part_of_config_digest(tmp_path):
    default_config = OfflineConfig.load(write_offline_config(tmp_path))
    changed_config = OfflineConfig.load(
        write_offline_config(tmp_path, **{"limits.max_output_grid_rows": 999})
    )

    assert default_config.digest() != changed_config.digest()


def test_limits_unknown_keys_and_fake_metadata_units_are_rejected(tmp_path, capsys):
    assert (
        main(["validate-offline", str(write_offline_config(tmp_path, **{"limits.typo": 1}))]) == 1
    )
    output = capsys.readouterr()
    assert "limits: unknown keys typo" in output.err

    assert (
        main(
            [
                "validate-offline",
                str(write_offline_config(tmp_path, **{"metadata.quantity_unit": "CONTRACTS"})),
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    assert "metadata.quantity_unit" in output.err

    assert (
        main(
            [
                "validate-offline",
                str(write_offline_config(tmp_path, **{"metadata.price_tick": "NaN"})),
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    assert "metadata.price_tick" in output.err
