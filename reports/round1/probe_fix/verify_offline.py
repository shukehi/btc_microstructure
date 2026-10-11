"""R102-A/B verification. All tests offline; probe invocation has NO network flag."""
import datetime
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'reports/round1/probe_fix'
index = 1
while (BASE/f'after_{index:02}').exists():
    index += 1
OUT = BASE/f'after_{index:02}'
OUT.mkdir()
relative = str(OUT.relative_to(ROOT))
assert sys.version_info[:2] == (3, 12)
assert version('aiohttp') == '3.12.15'
commands = [
    ('environment', ['.venv/bin/python','-c','import platform,sys,aiohttp; print(platform.platform()); print(sys.version); print(aiohttp.__version__)'], 0),
    ('dependencies', ['.venv/bin/python','-m','pip','list','--format=json'], 0),
    ('original166', ['.venv/bin/python','-m','pytest','-q','tests/unit/test_config.py','tests/unit/test_foundation_guards.py','tests/unit/test_probe.py','tests/unit/test_probe_transport.py',f'--junitxml={relative}/original166.xml'], 0),
    ('added', ['.venv/bin/python','-m','pytest','-q','tests/unit/test_probe_attempts.py','tests/unit/test_probe_interruptions.py',f'--junitxml={relative}/added.xml'], 0),
    ('default', ['.venv/bin/python','-m','pytest','-q',f'--junitxml={relative}/default.xml'], 0),
    ('independent01', ['.venv/bin/python','-m','pytest','-q','review/R1_01/checks/test_review_regressions.py',f'--junitxml={relative}/independent01.xml'], 0),
    ('independent02', ['.venv/bin/python','-m','pytest','-q','review/R1_02/checks/test_r1_02_regressions.py',f'--junitxml={relative}/independent02.xml'], 0),
    ('config', ['.venv/bin/python','-m','btc_microstructure','validate-config','config/research.example.yaml'], 0),
    ('network-disabled', ['.venv/bin/python','-m','btc_microstructure','probe','config/research.example.yaml','--output',relative+'/network-disabled'], 2),
    ('ruff', ['.venv/bin/ruff','check','src','tests'], 0),
    ('format', ['.venv/bin/ruff','format','--check','src','tests'], 0),
    ('mypy', ['.venv/bin/mypy','src'], 0),
    ('pip-check', ['.venv/bin/python','-m','pip','check'], 0),
    ('original-specs', ['shasum','-a','256','-c','reports/round1/preserved.sha256'], 0),
]
records=[]
for name,command,expected in commands:
    r=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=120)
    record=dict(name=name,command=command,cwd=str(ROOT),exit_code=r.returncode,
                expected_exit_code=expected,status='PASS' if r.returncode==expected else 'FAIL',
                time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                log=relative+'/'+name+'.log')
    if name in ('original166','added','default','independent01','independent02'):
        suite=ET.parse(OUT/(name+'.xml')).getroot().find('testsuite')
        assert suite is not None
        failed=int(suite.get('failures','0'))+int(suite.get('errors','0'))
        skipped=int(suite.get('skipped','0'))
        record['tests']=dict(passed=int(suite.get('tests','0'))-failed-skipped,failed=failed,skipped=skipped)
    (ROOT/record['log']).write_text(json.dumps(record)+'\n--- stdout ---\n'+r.stdout+'\n--- stderr ---\n'+r.stderr)
    records.append(record)
    print(name,record['status'],r.returncode,record.get('tests',''),flush=True)
(OUT/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
sys.exit(int(any(r['status']=='FAIL' for r in records)))
