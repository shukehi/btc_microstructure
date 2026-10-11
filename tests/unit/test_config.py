from copy import deepcopy
from decimal import Decimal
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError
import yaml

from btc_microstructure.capabilities import pending_capabilities
from btc_microstructure.cli import main
from btc_microstructure.config import ConfigError, config_digest, load_config
from btc_microstructure.config_models import ResearchConfig
from conftest import ROOT


def changed(raw, path, value):
    result = deepcopy(raw)
    parts = path.split(".")
    node = result
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return result


def test_original_sample_complete(raw_config):
    config = load_config(ROOT / "config/research.example.yaml")
    assert config.model_dump() == raw_config
    assert config.instruments.perpetual.oi_unit == "UNKNOWN"
    assert config.instruments.perpetual.unit_evidence_id is None
    assert config.cost_analysis.position_quantity_btc is None
    assert config.collector.protocol_probe_completed is False
    assert all(item.status == "UNVERIFIED" for item in pending_capabilities(config))


def test_cfg_01_independent_horizons(raw_config):
    original = ResearchConfig.model_validate(raw_config)
    config = ResearchConfig.model_validate(
        changed(raw_config, "research.horizon_seconds", [5, 30, 300])
    )
    assert config.research.horizon_seconds == [5, 30, 300]
    assert config.features.grid_seconds == 1
    assert config.features == original.features
    assert config.features.trade_lookbacks_seconds == [1, 5, 30, 60, 300, 900]
    slower = ResearchConfig.model_validate(changed(raw_config, "features.grid_seconds", 2))
    assert slower.research == original.research
    assert slower.features.trade_lookbacks_seconds == original.features.trade_lookbacks_seconds
    longer = ResearchConfig.model_validate(
        changed(raw_config, "features.trade_lookbacks_seconds", [7, 45])
    )
    assert longer.features.grid_seconds == 1
    assert longer.research == original.research


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        ("research.horizon_seconds", [5, 5], "duplicates"),
        ("research.horizon_seconds", [], "at least 1"),
        ("research.horizon_seconds", [0], "greater than 0"),
        ("features.trade_lookbacks_seconds", [-1], "greater than 0"),
        ("features.ofi_lookbacks_seconds", [1, 1], "duplicates"),
        ("features.oi_lookbacks_seconds", [30, 30], "duplicates"),
        ("features.grid_seconds", 0, "greater than 0"),
        ("collector.oi_poll_seconds", 0, "greater than 0"),
        ("features.time_view", "typo", "Input should be"),
        ("project.mode", "live_trading", "Input should be"),
        ("project.automatic_trading", True, "must be false"),
        ("project.automatic_trading", "false", "valid boolean"),
        ("states.enabled", "yes", "valid boolean"),
        ("collector.queue_max_records", True, "valid integer"),
        ("collector.queue_warning_fraction", 1.0, "less than 1"),
        ("collector.queue_warning_fraction", float("nan"), "finite"),
        ("features.depth_bps", [10000], "less than 10000"),
        ("features.depth_bps", [5, 5], "duplicates"),
        ("research.split.shuffle", True, "must be false"),
        ("project.timezone", "Not/AZone", "unknown timezone"),
        ("storage.normalized_format", "csv", "Input should be"),
        ("features.window_boundary", "closed", "Input should be"),
        ("cost_analysis.execution_model", None, "Input should be"),
        ("instruments.perpetual.oi_unit", "guessed", "Input should be"),
        ("endpoints.um_rest", "https://user:password@example.com", "public https"),
        ("endpoints.spot_ws", "http://example.com", "public wss"),
    ],
)
def test_cfg_02_invalid_values(raw_config, path, value, reason):
    with pytest.raises(ValidationError) as caught:
        ResearchConfig.model_validate(changed(raw_config, path, value))
    errors = caught.value.errors()
    assert any(".".join(map(str, e["loc"])).startswith(path) and reason in e["msg"] for e in errors)


def all_models(node, prefix=""):
    yield prefix
    for key, value in node.items():
        if isinstance(value, dict):
            yield from all_models(value, f"{prefix}.{key}" if prefix else key)


def test_unknown_key_rejected_at_every_model(raw_config):
    for prefix in all_models(raw_config):
        path = f"{prefix}.typo" if prefix else "typo"
        with pytest.raises(ValidationError) as caught:
            ResearchConfig.model_validate(changed(raw_config, path, 123))
        assert any(
            ".".join(e["loc"]) == path and e["type"] == "extra_forbidden"
            for e in caught.value.errors()
        )


def complete_cost(raw):
    data = deepcopy(raw)
    data["cost_analysis"].update(
        enabled=True,
        position_quantity_btc="1.000",
        entry_fee_rate="0.001",
        exit_fee_rate="0.001",
        entry_latency_ms=0,
        exit_latency_ms=10,
        extra_slippage_bps="0",
    )
    return data


@pytest.mark.parametrize(
    "field",
    [
        "position_quantity_btc",
        "entry_fee_rate",
        "exit_fee_rate",
        "entry_latency_ms",
        "exit_latency_ms",
        "extra_slippage_bps",
    ],
)
def test_cost_01_missing_required(raw_config, field):
    raw = complete_cost(raw_config)
    raw["cost_analysis"][field] = None
    with pytest.raises(ValidationError, match=field):
        ResearchConfig.model_validate(raw)


def test_exact_cost_and_zero_are_explicit(raw_config):
    raw = complete_cost(raw_config)
    config = ResearchConfig.model_validate(raw)
    assert config.cost_analysis.position_quantity_btc == Decimal("1.000")
    assert config.cost_analysis.entry_fee_rate == Decimal("0.001")
    assert config.cost_analysis.extra_slippage_bps == Decimal("0")
    raw["cost_analysis"]["entry_fee_rate"] = 0
    assert ResearchConfig.model_validate(raw).cost_analysis.entry_fee_rate == Decimal(0)


@pytest.mark.parametrize("value", [0.1, True, "NaN", "Infinity", "-1", "bad"])
def test_cost_rejects_inexact_or_invalid(raw_config, value):
    with pytest.raises(ValidationError) as caught:
        ResearchConfig.model_validate(changed(raw_config, "cost_analysis.entry_fee_rate", value))
    assert caught.value.errors()[0]["loc"] == ("cost_analysis", "entry_fee_rate")


def test_digest_order_and_parameters(raw_config):
    config = ResearchConfig.model_validate(raw_config)

    def reverse(node):
        return (
            {k: reverse(v) for k, v in reversed(list(node.items()))}
            if isinstance(node, dict)
            else node
        )

    assert config_digest(config) == config_digest(
        ResearchConfig.model_validate(reverse(raw_config))
    )
    assert len(config_digest(config)) == 64
    for path, value in [
        ("research.horizon_seconds", [5, 30, 300]),
        ("features.grid_seconds", 2),
        ("features.trade_lookbacks_seconds", [5, 60]),
    ]:
        assert config_digest(config) != config_digest(
            ResearchConfig.model_validate(changed(raw_config, path, value))
        )
    cost = complete_cost(raw_config)
    equivalent = changed(cost, "cost_analysis.position_quantity_btc", "1")
    assert config_digest(ResearchConfig.model_validate(cost)) == config_digest(
        ResearchConfig.model_validate(equivalent)
    )


def test_yaml_and_paths(tmp_path, raw_config):
    source = tmp_path / "config.yaml"
    source.write_text("project: {}\nproject: {}\n")
    with pytest.raises(ConfigError, match="duplicate key"):
        load_config(source)
    source.write_text("project: [\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(source)
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing")
    for path, value in [
        ("storage.root", "\0bad"),
        ("storage.root", "https://example.com"),
        ("storage.root", str(source / "data")),
        ("collector.endpoint_budget_file", "missing.json"),
    ]:
        source.write_text(yaml.safe_dump(changed(raw_config, path, value)))
        with pytest.raises(ConfigError, match=path):
            load_config(source)
    source.write_text(yaml.safe_dump(raw_config))
    load_config(source)
    assert not (tmp_path / "data").exists()


def test_cli_offline_and_private_output(tmp_path, raw_config, capsys, monkeypatch):
    monkeypatch.setenv("SECRET_TEST_SENTINEL", "do-not-print-this")
    assert main(["validate-config", str(ROOT / "config/research.example.yaml")]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["status"] == "CONFIG_VALID"
    assert "do-not-print-this" not in out.out
    path = tmp_path / "invalid.yaml"
    path.write_text(
        yaml.safe_dump(changed(raw_config, "project.automatic_trading", "do-not-print-this"))
    )
    assert main(["validate-config", str(path)]) == 1
    out = capsys.readouterr()
    assert "project.automatic_trading" in out.err
    assert "valid boolean" in out.err
    assert "do-not-print-this" not in out.err


@pytest.mark.parametrize(("valid", "code"), [(True, 0), (False, 1)])
def test_module_cli_exit_code(tmp_path, raw_config, valid, code):
    source = tmp_path / "config.yaml"
    source.write_text(
        yaml.safe_dump(raw_config if valid else changed(raw_config, "features.grid_seconds", 0))
    )
    process = subprocess.run(
        [sys.executable, "-m", "btc_microstructure", "validate-config", str(source)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == code
    if valid:
        assert json.loads(process.stdout)["status"] == "CONFIG_VALID"
    else:
        assert "features.grid_seconds" in process.stderr
        assert "greater than 0" in process.stderr


def test_declared_probe_does_not_verify_capabilities(raw_config):
    config = ResearchConfig.model_validate(
        changed(raw_config, "collector.protocol_probe_completed", True)
    )
    assert all(item.status == "UNVERIFIED" for item in pending_capabilities(config))
    with pytest.raises(ValidationError, match="unit_evidence_id"):
        ResearchConfig.model_validate(changed(raw_config, "instruments.perpetual.oi_unit", "BTC"))
