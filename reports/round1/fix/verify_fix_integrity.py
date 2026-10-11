"""Verify protected inputs, exact dependency versions and every guard CLI."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'reports/round1/fix'
protected = json.loads((OUT / 'protected.json').read_text())
observed = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in protected}
assert observed == protected
locks = {}
for line in (ROOT / 'requirements-dev.lock').read_text().splitlines():
    name, expected = line.split('==')
    actual = version(name)
    locks[name] = dict(expected=expected, actual=actual)
    assert actual == expected, name
fields = [
    'storage.source_payload_preserved', 'quality.unknown_as_null',
    'quality.known_gap_invalidates_flow_window', 'research.report_all_horizons',
    'research.report_common_event_sample', 'states.freeze_thresholds_in_validation_and_test',
    'research.split.purge_by_actual_label_intervals',
]
fixtures = OUT / 'fixtures'
fixtures.mkdir(exist_ok=True)
records = []
for field in fields:
    raw = yaml.safe_load((ROOT / 'config/research.example.yaml').read_text())
    parts = field.split('.')
    node = raw
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = False
    source = fixtures / (field + '.yaml')
    source.write_text(yaml.safe_dump(raw))
    command = ['.venv/bin/python', '-m', 'btc_microstructure', 'validate-config', str(source.relative_to(ROOT))]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
    passed = result.returncode == 1 and field in result.stderr and 'must be true' in result.stderr
    records.append(dict(field=field, command=command, cwd=str(ROOT), exit_code=result.returncode,
                        expected_exit_code=1, status='PASS' if passed else 'FAIL',
                        stdout=result.stdout, stderr=result.stderr))
    assert passed, field
(OUT / 'after/cli-guards.json').write_text(json.dumps(records, indent=2)+'\n')
(OUT / 'after/cli-guards.log').write_text('\n'.join(
    json.dumps({k:v for k,v in r.items() if k not in ('stdout','stderr')})
    + '\n--- stdout ---\n'+r['stdout']+'\n--- stderr ---\n'+r['stderr'] for r in records))
result = dict(status='PASS', protected_file_count=len(protected), protected_hashes_unchanged=True,
              exact_locked_dependencies=locks, guard_cli_passed=len(records), redactions=[])
(OUT / 'after/integrity.json').write_text(json.dumps(result,indent=2)+'\n')
print(f'PASS: {len(protected)} protected files unchanged; {len(locks)} exact dependency versions; 7 CLI guards rejected')
