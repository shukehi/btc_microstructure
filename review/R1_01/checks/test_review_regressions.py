"""Independent spec-derived checks, not a replacement for the submitted suite.

Run from the submitted project root:
  PYTHONPATH=src python -m pytest -q /path/to/test_review_regressions.py

No exchange connections or changes to the submitted project are made.
"""
from pathlib import Path
import subprocess

import pytest
import yaml

from btc_microstructure.cli import main

PROJECT = Path.cwd()


@pytest.fixture
def raw_config():
    return yaml.safe_load((PROJECT / 'config/research.example.yaml').read_text())


@pytest.fixture
def isolated_git(tmp_path):
    root = tmp_path / 'git-ignore-check'
    root.mkdir()
    (root / '.gitignore').write_bytes((PROJECT / '.gitignore').read_bytes())
    subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
    return root


@pytest.mark.parametrize('path', [
    '.env', '.env.production', 'private.key', 'certificate.pem',
    'codex-local-logs/session.log',
])
def test_original_sensitive_ignore_rules_retained(isolated_git, path):
    result = subprocess.run(
        ['git', '-C', str(isolated_git), 'check-ignore', '--no-index', '-q', path],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f'{path} is not ignored; original handoff ignored it'


@pytest.mark.parametrize('field', [
    'storage.source_payload_preserved',
    'quality.unknown_as_null',
    'quality.known_gap_invalidates_flow_window',
    'research.report_all_horizons',
    'research.report_common_event_sample',
    'states.freeze_thresholds_in_validation_and_test',
    'research.split.purge_by_actual_label_intervals',
])
def test_spec_required_guards_cannot_be_disabled(raw_config, tmp_path, capsys, field):
    node = raw_config
    parts = field.split('.')
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = False
    source = tmp_path / 'guard-disabled.yaml'
    source.write_text(yaml.safe_dump(raw_config), encoding='utf-8')
    exit_code = main(['validate-config', str(source)])
    output = capsys.readouterr()
    assert exit_code == 1, (
        f'{field}=false violates the v0.1 specification but was accepted: {output.out}'
    )
    assert field in output.err


def test_control_valid_original_config(capsys):
    assert main(['validate-config', str(PROJECT / 'config/research.example.yaml')]) == 0
    assert 'CONFIG_VALID' in capsys.readouterr().out


def test_control_trading_is_rejected(raw_config, tmp_path, capsys):
    raw_config['project']['automatic_trading'] = True
    source = tmp_path / 'trading.yaml'
    source.write_text(yaml.safe_dump(raw_config), encoding='utf-8')
    assert main(['validate-config', str(source)]) == 1
    assert 'project.automatic_trading' in capsys.readouterr().err


def test_control_venv_ignored(isolated_git):
    result = subprocess.run(
        ['git', '-C', str(isolated_git), 'check-ignore', '--no-index', '-q', '.venv/x'],
        capture_output=True,
    )
    assert result.returncode == 0
