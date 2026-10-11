"""Capture real R1-01 fix verification output without changing test expectations."""
import datetime
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'reports/round1/fix'
phase = sys.argv[1]
assert phase in ('before', 'after')
target = OUT / phase
target.mkdir(exist_ok=True)
if phase == 'before' and (target / 'commands.json').exists():
    raise SystemExit('Refusing to overwrite original pre-fix failure evidence')
commands = [
    ('environment', ['.venv/bin/python', '-c', 'import sys,platform; print(sys.version); print(platform.platform()); print(platform.machine())']),
    ('os', ['sw_vers']),
    ('dependencies', ['.venv/bin/python', '-m', 'pip', 'list', '--format=json']),
    ('baseline', ['.venv/bin/python', '-m', 'pytest', '-q', 'tests/unit/test_config.py', f'--junitxml=reports/round1/fix/{phase}/baseline.xml']),
    ('independent', ['.venv/bin/python', '-m', 'pytest', '-q', 'review/R1_01/checks/test_review_regressions.py', f'--junitxml=reports/round1/fix/{phase}/independent.xml']),
]
if phase == 'after':
    commands += [
        ('full', ['.venv/bin/python', '-m', 'pytest', '-q', f'--junitxml=reports/round1/fix/{phase}/full.xml']),
        ('ruff', ['.venv/bin/ruff', 'check', 'src', 'tests']),
        ('format', ['.venv/bin/ruff', 'format', '--check', 'src', 'tests']),
        ('mypy', ['.venv/bin/mypy', 'src']),
        ('pip-check', ['.venv/bin/python', '-m', 'pip', 'check']),
        ('cli-valid', ['.venv/bin/python', '-m', 'btc_microstructure', 'validate-config', 'config/research.example.yaml']),
        ('preservation', ['shasum', '-a', '256', '-c', 'reports/round1/preserved.sha256']),
    ]
records = []
for name, command in commands:
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    record = dict(command=command, cwd=str(ROOT), exit_code=result.returncode,
                  status='PASS' if result.returncode == 0 else 'FAIL', started_utc=started,
                  log=str((target / f'{name}.log').relative_to(ROOT)))
    if name in ('baseline', 'independent', 'full'):
        suite = ET.parse(target / f'{name}.xml').getroot().find('testsuite')
        assert suite is not None
        failures = int(suite.get('failures', '0')) + int(suite.get('errors', '0'))
        skips = int(suite.get('skipped', '0'))
        record['tests'] = dict(passed=int(suite.get('tests', '0'))-failures-skips, failed=failures, skipped=skips)
    (ROOT / record['log']).write_text(json.dumps(record) + '\n--- stdout ---\n' + result.stdout + '\n--- stderr ---\n' + result.stderr)
    records.append(record)
    print(name, record['status'], record['exit_code'], record.get('tests', ''), flush=True)
(target / 'commands.json').write_text(json.dumps(records, indent=2) + '\n')
if phase == 'before':
    assert records[-1]['tests'] == dict(passed=3, failed=12, skipped=0), 'review reproduction differs'
else:
    assert all(record['exit_code'] == 0 for record in records), 'verification failed; read complete logs'
