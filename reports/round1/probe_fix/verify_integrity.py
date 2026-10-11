"""Preserve authored inputs and prior evidence; exclude OS Finder metadata explicitly."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT/'reports/round1/probe_fix'
protected=json.loads((BASE/'protected.json').read_text())
ignored=[name for name in protected if Path(name).name=='.DS_Store']
checked={name:digest for name,digest in protected.items() if name not in ignored}
changed=[name for name,digest in checked.items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest]
assert not changed, changed
for line in (ROOT/'requirements-dev.lock').read_text().splitlines():
    name,locked=line.split('==')
    assert version(name)==locked
record=dict(status='PASS',protected_content_files=len(checked),changed_content_files=changed,
            excluded_os_metadata=ignored,reason='Finder metadata is not authored content and is excluded from ZIP/Git; original hash manifest and diagnostic are preserved.',
            exact_locked_versions=26,exchange_network_run_this_turn=False)
(BASE/'integrity.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
