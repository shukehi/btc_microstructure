"""Verify fixed archive/sidecar and current requested deliverables; no network."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[3]
archive=ROOT/'protocol_probe_review_fixed.zip'
sidecar=ROOT/'protocol_probe_review_fixed.zip.sha256'
assert archive.is_file() and sidecar.is_file()
assert hashlib.sha256(archive.read_bytes()).hexdigest()==sidecar.read_text().split()[0]
required=['reports/round1/02_protocol_probe_fix.md','src/btc_microstructure/probe.py','src/btc_microstructure/probe_transport.py','tests/unit/test_probe_attempts.py','tests/unit/test_probe_interruptions.py','reports/round1/probe_fix/before/independent02.log','reports/round1/probe_fix/after_02/default.log','review/R1_02/R1_02_Independent_Review.md']
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert len(z.namelist())==len(set(z.namelist()))
    manifest=json.loads(z.read('reports/round1/probe_fixed_package_manifest.json'))
    for item in manifest['files']:
        assert hashlib.sha256(z.read(item['path'])).hexdigest()==item['sha256']
    for name in required:
        assert z.read(name)==(ROOT/name).read_bytes()
    forbidden={'.venv','.git','.omx','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','build','dist','data'}
    for name in z.namelist():
        path=Path(name)
        assert not path.is_absolute() and '..' not in path.parts
        assert not set(path.parts)&forbidden
        assert path.name!='.DS_Store' and not path.name.startswith('.env')
print('PASS: requested report, ZIP and SHA-256 exist; CRC, payload hashes, current code/tests and exclusions verified')
