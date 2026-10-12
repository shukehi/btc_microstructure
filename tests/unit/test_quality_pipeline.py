from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import socket
from typing import Any

import pytest
import yaml

from btc_microstructure.config import ConfigError
from btc_microstructure.offline import OfflineConfig, build_features, replay_run, run_synthetic
from btc_microstructure.offline_core import FEATURE_VERSION, NORMALIZATION_VERSION
from btc_microstructure.offline_storage import Store
from conftest import ROOT


def _deny_network(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("network forbidden in R104 quality pipeline tests")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    monkeypatch.setattr(socket.socket, "connect", _deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny_network)


def _load_vertical_fixture() -> dict[str, Any]:
    return json.loads((ROOT / "fixtures/synthetic/vertical_slice/warmed_delta.json").read_text())


def _first_raw_event(fixture: dict[str, Any]) -> dict[str, Any]:
    return next(event for event in fixture["events"] if event["kind"] == "RAW")


def _config_path(tmp_path: Path, fixture: dict[str, Any]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture, sort_keys=True), encoding="utf-8")
    settings = yaml.safe_load((ROOT / "config/offline.synthetic.yaml").read_text())
    settings["synthetic_input"] = str(fixture_path)
    settings["output_root"] = str(tmp_path / "configured-output")
    settings["limits"]["min_free_bytes"] = 0
    path = tmp_path / "offline.yaml"
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    return path


def _config(tmp_path: Path, fixture: dict[str, Any]) -> OfflineConfig:
    return OfflineConfig.load(_config_path(tmp_path, fixture))


def _feature_at(
    root: Path, cutoff_ns: int = 31_000_000_000, window_seconds: int = 30
) -> dict[str, Any]:
    [row] = [
        item
        for item in Store(root).read_table("features")
        if item["feature_cutoff_ns"] == cutoff_ns and item["window_seconds"] == window_seconds
    ]
    return row


def _manifest_paths(root: Path) -> list[Path]:
    return sorted((root / "manifests").glob("manifest-*.json"))


def _set_manifest_versions(
    root: Path, *, feature_version: str, normalization_version: str | None
) -> None:
    for path in _manifest_paths(root):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["run_metadata"]["feature_version"] = feature_version
        if normalization_version is None:
            manifest["run_metadata"].pop("normalization_version", None)
        else:
            manifest["run_metadata"]["normalization_version"] = normalization_version
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _raw_manifest_paths(root: Path) -> list[Path]:
    return sorted((root / "manifests").glob("manifest-raw-*.json"))


def _copy_raw_only(source: Path, destination: Path) -> None:
    destination.mkdir()
    shutil.copytree(source / "raw", destination / "raw")
    (destination / "manifests").mkdir()
    for manifest in _raw_manifest_paths(source):
        shutil.copy2(manifest, destination / "manifests" / manifest.name)
    if (source / "errors").exists():
        shutil.copytree(source / "errors", destination / "errors")


def _raw_file_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for manifest_path in _raw_manifest_paths(root):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_path = root / manifest["path"]
        result[manifest["path"]] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    return result


def test_gap_known_round_trips_through_parquet_and_degrades_data_health(tmp_path: Path) -> None:
    fixture = _load_vertical_fixture()
    _first_raw_event(fixture)["quality_flags"] = ["GAP_KNOWN"]
    config = _config(tmp_path, fixture)
    run_dir = tmp_path / "run"

    summary = run_synthetic(config, run_dir)
    store = Store(run_dir)
    raw = next(row for row in store.read_rows() if row["kind"] == "RAW")
    normalized = store.read_table("normalized_trades")[0]
    feature = _feature_at(run_dir)

    assert summary["status"] == "PASS"
    assert summary["data_health"]["status"] == "DEGRADED"
    assert summary["feature_quality_counts"]["GAP_KNOWN"] >= 1
    assert summary["feature_invalid_rows"] >= 1
    assert raw["quality_flags"] == ["GAP_KNOWN"]
    assert normalized["quality_flags"] == ["GAP_KNOWN"]
    assert normalized["normalization_version"] == NORMALIZATION_VERSION
    assert feature["quality_flags"] == ["GAP_KNOWN"]
    assert feature["data_valid"] is False
    assert feature["volume_base"] is None
    assert feature["trade_delta_base"] is None
    for column in ("buy_volume_base", "sell_volume_base", "trade_imbalance", "agg_message_count"):
        assert feature[column] is None


def test_source_sampled_preserves_values_but_mixed_gap_invalidates(tmp_path: Path) -> None:
    sampled_fixture = _load_vertical_fixture()
    _first_raw_event(sampled_fixture)["quality_flags"] = ["SOURCE_SAMPLED"]
    sampled_config = _config(tmp_path / "sampled", sampled_fixture)
    sampled_dir = tmp_path / "sampled-run"

    sampled_summary = run_synthetic(sampled_config, sampled_dir)
    sampled_feature = _feature_at(sampled_dir)

    assert sampled_summary["data_health"]["status"] == "PASS"
    assert sampled_feature["data_valid"] is True
    assert sampled_feature["quality_flags"] == ["SOURCE_SAMPLED"]
    assert sampled_feature["volume_base"] == Decimal("0.600000000000000000")
    assert sampled_feature["trade_delta_base"] == Decimal("0.400000000000000000")

    mixed_fixture = deepcopy(sampled_fixture)
    _first_raw_event(mixed_fixture)["quality_flags"] = ["SOURCE_SAMPLED", "GAP_KNOWN"]
    mixed_config = _config(tmp_path / "mixed", mixed_fixture)
    mixed_dir = tmp_path / "mixed-run"

    mixed_summary = run_synthetic(mixed_config, mixed_dir)
    mixed_feature = _feature_at(mixed_dir)

    assert mixed_summary["data_health"]["status"] == "DEGRADED"
    assert mixed_feature["data_valid"] is False
    assert mixed_feature["quality_flags"] == ["GAP_KNOWN", "SOURCE_SAMPLED"]
    assert mixed_feature["volume_base"] is None
    assert mixed_feature["trade_delta_base"] is None


def test_collect_replay_twice_and_raw_only_build_keep_gap_nulls_and_raw_bytes(
    tmp_path: Path,
) -> None:
    fixture = _load_vertical_fixture()
    _first_raw_event(fixture)["quality_flags"] = ["GAP_KNOWN"]
    config = _config(tmp_path, fixture)
    run_dir = tmp_path / "run"
    replay_one = tmp_path / "replay-one"
    replay_two = tmp_path / "replay-two"
    raw_only = tmp_path / "raw-only"
    build_dir = tmp_path / "build"

    run_synthetic(config, run_dir)
    before_hashes = _raw_file_hashes(run_dir)

    first_summary = replay_run(config, run_dir, replay_one)
    second_summary = replay_run(config, run_dir, replay_two)
    _copy_raw_only(run_dir, raw_only)
    build_summary = build_features(config, raw_only, build_dir)

    assert first_summary["status"] == "PASS"
    assert second_summary["status"] == "PASS"
    assert build_summary["status"] == "PASS"
    for root in (run_dir, replay_one, replay_two, build_dir):
        feature = _feature_at(root)
        assert feature["quality_flags"] == ["GAP_KNOWN"]
        assert feature["data_valid"] is False
        assert feature["volume_base"] is None
        assert feature["trade_delta_base"] is None
    assert _raw_file_hashes(run_dir) == before_hashes
    assert _raw_file_hashes(raw_only) == before_hashes


def test_legacy_delta1_derived_replay_rejected_but_raw_build_recomputes_current_version(
    tmp_path: Path,
) -> None:
    fixture = _load_vertical_fixture()
    _first_raw_event(fixture)["quality_flags"] = ["GAP_KNOWN"]
    config = _config(tmp_path, fixture)
    run_dir = tmp_path / "run"
    replay_dir = tmp_path / "replay-legacy"
    raw_only = tmp_path / "raw-only-legacy"
    build_dir = tmp_path / "build-from-legacy-raw"

    run_synthetic(config, run_dir)
    _set_manifest_versions(run_dir, feature_version="delta.1", normalization_version=None)

    with pytest.raises(ConfigError, match="build-features for delta.2-quality"):
        replay_run(config, run_dir, replay_dir)

    _copy_raw_only(run_dir, raw_only)
    source_hashes = _raw_file_hashes(raw_only)
    summary = build_features(config, raw_only, build_dir)
    built_feature = _feature_at(build_dir)
    built_manifest = json.loads(next(iter(_manifest_paths(build_dir))).read_text(encoding="utf-8"))

    assert summary["status"] == "PASS"
    assert summary["input_root"] == str(raw_only)
    assert built_feature["feature_version"] == FEATURE_VERSION
    assert built_feature["volume_base"] is None
    assert _raw_file_hashes(raw_only) == source_hashes
    assert built_manifest["run_metadata"]["feature_version"] == FEATURE_VERSION
    assert built_manifest["run_metadata"]["normalization_version"] == NORMALIZATION_VERSION
    assert built_manifest["run_metadata"]["build_from"] == str(raw_only)


def test_unknown_quality_and_unknown_manifest_versions_do_not_become_healthy(
    tmp_path: Path,
) -> None:
    fixture = _load_vertical_fixture()
    _first_raw_event(fixture)["quality_flags"] = ["NOT_A_DOCUMENTED_FLAG"]
    config = _config(tmp_path, fixture)
    run_dir = tmp_path / "illegal-quality"

    run_synthetic(config, run_dir)
    store = Store(run_dir)
    raw = next(row for row in store.read_rows() if row["kind"] == "RAW")
    quarantine = store.read_table("quarantine")[0]
    feature = _feature_at(run_dir)

    assert raw["quality_flags"] == ["NOT_A_DOCUMENTED_FLAG"]
    assert quarantine["quality_flags"] == ["SOURCE_SCHEMA_CHANGED"]
    assert quarantine["upstream_quality_flags_json"] == '["NOT_A_DOCUMENTED_FLAG"]'
    assert quarantine["normalization_version"] == NORMALIZATION_VERSION
    assert feature["data_valid"] is False
    assert "SOURCE_SCHEMA_CHANGED" in feature["quality_flags"]

    raw_only = tmp_path / "unknown-version-raw"
    _copy_raw_only(run_dir, raw_only)
    _set_manifest_versions(raw_only, feature_version="delta.unknown", normalization_version=None)
    with pytest.raises(ConfigError, match="manifest feature version mismatch"):
        build_features(config, raw_only, tmp_path / "unknown-version-build")


def test_sampled_advisory_during_warmup_is_not_counted_as_non_warmup_failure(
    tmp_path: Path,
) -> None:
    fixture = _load_vertical_fixture()
    first = _first_raw_event(fixture)
    first["ts_receive_ns"] = 2_000_000_000
    first["payload"]["E"] = first["payload"]["T"] = 2000
    first["quality_flags"] = ["SOURCE_SAMPLED"]
    config = _config(tmp_path, fixture)
    root = tmp_path / "early-sampled-run"
    summary = run_synthetic(config, root)
    warm = _feature_at(root, cutoff_ns=3_000_000_000)
    assert warm["data_valid"] is False
    assert set(warm["quality_flags"]) == {"WARMUP", "SOURCE_SAMPLED"}
    assert summary["feature_invalid_rows"] > 0
    assert summary["data_health"]["non_warmup_invalid_feature_rows"] == 0
    assert summary["data_health"]["status"] == "PASS"
