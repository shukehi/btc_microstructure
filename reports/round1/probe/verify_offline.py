"""R1-02 offline verification only; never enables real exchange access."""
import datetime
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'reports/round1/probe'
index = 1
while (BASE / f'offline_{index:02}').exists():
    index += 1
OUT = BASE / f'offline_{index:02}'
OUT.mkdir()
relative = str(OUT.relative_to(ROOT))
commands = [
    ('os', ['sw_vers'], 0),
    ('python', ['.venv/bin/python', '--version'], 0),
    ('dependencies', ['.venv/bin/python', '-m', 'pip', 'list', '--format=json'], 0),
    ('install-local', ['.venv/bin/python', '-m', 'pip', 'install', '--no-build-isolation', '--no-deps', '-e', '.'], 0),
    ('original106', ['.venv/bin/python', '-m', 'pytest', '-q', 'tests/unit/test_config.py', 'tests/unit/test_foundation_guards.py', f'--junitxml={relative}/original106.xml'], 0),
    ('default', ['.venv/bin/python', '-m', 'pytest', '-q', f'--junitxml={relative}/default.xml'], 0),
    ('independent15', ['.venv/bin/python', '-m', 'pytest', '-q', 'review/R1_01/checks/test_review_regressions.py', f'--junitxml={relative}/independent15.xml'], 0),
    ('ruff', ['.venv/bin/ruff', 'check', 'src', 'tests'], 0),
    ('format', ['.venv/bin/ruff', 'format', '--check', 'src', 'tests'], 0),
    ('mypy', ['.venv/bin/mypy', 'src'], 0),
    ('pip-check', ['.venv/bin/python', '-m', 'pip', 'check'], 0),
    ('preservation', ['shasum', '-a', '256', '-c', 'reports/round1/preserved.sha256'], 0),
    ('validate-config', ['.venv/bin/python', '-m', 'btc_microstructure', 'validate-config', 'config/research.example.yaml'], 0),
    ('network-disabled', ['.venv/bin/python', '-m', 'btc_microstructure', 'probe', 'config/research.example.yaml', '--output', relative+'/network-disabled'], 2),
]
records=[]
for name, command, expected in commands:
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    r=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=120)
    record=dict(name=name,command=command,cwd=str(ROOT),started_utc=started,
                exit_code=r.returncode,expected_exit_code=expected,
                status='PASS' if r.returncode==expected else 'FAIL',log=relative+'/'+name+'.log')
    if name in ('original106','default','independent15'):
        s=ET.parse(OUT/(name+'.xml')).getroot().find('testsuite')
        assert s is not None
        failed=int(s.get('failures','0'))+int(s.get('errors','0'))
        skipped=int(s.get('skipped','0'))
        record['tests']=dict(passed=int(s.get('tests','0'))-failed-skipped,failed=failed,skipped=skipped)
    (ROOT/record['log']).write_text(json.dumps(record)+'\n--- stdout ---\n'+r.stdout+'\n--- stderr ---\n'+r.stderr)
    records.append(record)
    print(name,record['status'],r.returncode,record.get('tests',''),flush=True)
(OUT/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
print('Evidence directory:',relative)
sys.exit(int(any(r['status']=='FAIL' for r in records)))
