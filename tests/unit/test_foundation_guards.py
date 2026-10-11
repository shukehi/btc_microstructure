"""R1-01 A/B regressions derived from the independent review and v0.1 contracts."""

import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError
import yaml

from btc_microstructure.config import config_digest
from btc_microstructure.config_models import ResearchConfig

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = [
    "storage.source_payload_preserved",
    "quality.unknown_as_null",
    "quality.known_gap_invalidates_flow_window",
    "research.report_all_horizons",
    "research.report_common_event_sample",
    "states.freeze_thresholds_in_validation_and_test",
    "research.split.purge_by_actual_label_intervals",
]


def set_field(data, field, value):
    parts = field.split(".")
    node = data
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


@pytest.mark.parametrize("field", REQUIRED)
@pytest.mark.parametrize("value", [False, 0, 1, "false", "true"])
def test_required_guards_reject_false_and_non_booleans(raw_config, field, value):
    set_field(raw_config, field, value)
    with pytest.raises(ValidationError) as caught:
        ResearchConfig.model_validate(raw_config)
    errors = caught.value.errors()
    assert any(".".join(e["loc"]) == field for e in errors)
    assert any(
        "must be true" in e["msg"] if value is False else e["type"] == "bool_type" for e in errors
    )


@pytest.mark.parametrize("field", REQUIRED)
def test_guard_cli_process_rejects_with_full_field_path(raw_config, tmp_path, field):
    set_field(raw_config, field, False)
    source = tmp_path / "disabled-guard.yaml"
    source.write_text(yaml.safe_dump(raw_config))
    result = subprocess.run(
        [sys.executable, "-m", "btc_microstructure", "validate-config", str(source)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert field in result.stderr
    assert "must be true" in result.stderr
    assert "CONFIG_VALID" not in result.stdout


@pytest.fixture
def isolated_git(tmp_path):
    root = tmp_path / "ignore-check"
    root.mkdir()
    (root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    return root


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".env", 0),
        (".env.production", 0),
        ("nested/.env.private", 0),
        ("private.key", 0),
        ("nested/certificate.pem", 0),
        ("codex-local-logs/session.log", 0),
        (".venv/x", 0),
        ("build/x", 0),
        ("dist/x", 0),
        ("data/raw.json", 0),
        (".env.example", 1),
        ("nested/.env.example", 1),
        ("config/research.example.yaml", 1),
    ],
)
def test_sensitive_ignore_rules_use_git(isolated_git, path, expected):
    result = subprocess.run(
        ["git", "-C", str(isolated_git), "check-ignore", "--no-index", "-q", path],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == expected, result.stderr


def test_valid_sample_digest_unchanged(raw_config):
    config = ResearchConfig.model_validate(raw_config)
    assert (
        config_digest(config) == "b037004b544ca834855821374bd65520a535b509c0b952d9c3f37a97a30d3b43"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "btc_microstructure",
            "validate-config",
            str(ROOT / "config/research.example.yaml"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["sha256"] == config_digest(config)


def test_optional_state_guard_only_applies_when_enabled(raw_config):
    raw_config["states"]["enabled"] = False
    raw_config["states"]["freeze_thresholds_in_validation_and_test"] = False
    config = ResearchConfig.model_validate(raw_config)
    assert config.states.enabled is False
    assert config.states.freeze_thresholds_in_validation_and_test is False
    raw_config["states"]["freeze_thresholds_in_validation_and_test"] = 1
    with pytest.raises(ValidationError) as caught:
        ResearchConfig.model_validate(raw_config)
    assert caught.value.errors()[0]["type"] == "bool_type"


def test_engineering_defaults_remain_configurable(raw_config):
    raw_config["states"]["absorption"]["require_oi_expansion"] = True
    raw_config["states"]["absorption"]["require_l2_confirmation"] = True
    raw_config["collector"]["protocol_probe_required"] = False
    raw_config["research"]["horizon_seconds"] = [5, 30, 300]
    config = ResearchConfig.model_validate(raw_config)
    assert config.states.absorption.require_oi_expansion is True
    assert config.states.absorption.require_l2_confirmation is True
    assert config.collector.protocol_probe_required is False
    assert config.research.horizon_seconds == [5, 30, 300]
    assert config.features.grid_seconds == 1
    assert config.features.trade_lookbacks_seconds == [1, 5, 30, 60, 300, 900]
