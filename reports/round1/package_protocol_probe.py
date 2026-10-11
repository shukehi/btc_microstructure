"""Package reviewed foundation text artifacts, excluding runtime/private material."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'protocol_probe_review.zip'
REPORTS = ROOT / 'reports/round1'
MANIFEST = REPORTS / 'probe_package_manifest.json'
AUDIT = REPORTS / 'probe-packaging-audit.json'
ROOT_FILES = ['.gitignore', 'AGENTS.md', 'CODEX_START_HERE.md', 'COMPLETE_SPEC.md',
              'HANDOFF_MANIFEST.json', 'MANIFEST.json', 'README.md', 'VALIDATION_REPORT.md',
              'pyproject.toml', 'requirements-dev.lock']
RULES = {'src': {'.py'}, 'tests': {'.py'}, 'docs': {'.md'}, 'tasks': {'.md'},
         'examples': {'.json'}, 'reports/round1': {'.md', '.log', '.json', '.xml', '.sha256', '.py', '.yaml', '.txt', '.diff', '.bin'},
         'review/R1_01': {'.md', '.txt', '.py', '.json', '.log', '.xml'},
         'fixtures/synthetic/probe': {'.md', '.json'},
         'fixtures/protocol/r1_02_run01': {'.json', '.bin'}}
EXCLUDED = {'.venv', '.git', '.omx', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
            'build', 'dist', 'data', 'logs', 'node_modules'}
PATTERNS = [re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
            re.compile(r'\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b'),
            re.compile(r'https?://[^\s/:]+:[^\s/@]+@'),
            re.compile(r'(?i)(?:api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token|password)\s*[=:]\s*[\x22\x27]?[A-Za-z0-9_+/=-]{20,}')]


def selected_files():
    files = [ROOT / name for name in ROOT_FILES] + [ROOT / 'config/research.example.yaml']
    for directory, suffixes in RULES.items():
        for path in (ROOT / directory).rglob('*'):
            rel = path.relative_to(ROOT)
            if path.is_symlink() or any(p in EXCLUDED or p.endswith('.egg-info') for p in rel.parts):
                continue
            if path.is_file() and path.suffix in suffixes and path not in (MANIFEST, AUDIT):
                files.append(path)
    return sorted(set(files))


files = selected_files()
assert all(path.is_file() and not path.is_symlink() for path in files)
assert sum(p.stat().st_size for p in files) < 20_000_000
findings = []
for path in files:
    assert path.stat().st_size < 2_000_000, f'oversized artifact: {path.relative_to(ROOT)}'
    assert not path.name.startswith('.env')
    content = path.read_text(encoding='utf-8')
    # Exact synthetic test sentinel only; archive bytes and test expectations stay intact.
    content = content.replace('https://user:password@example.com', 'https://synthetic-test.example.com')
    for number, line in enumerate(content.splitlines(), 1):
        if any(pattern.search(line) for pattern in PATTERNS):
            findings.append(dict(path=str(path.relative_to(ROOT)), line=number))
if findings:
    # Never print or persist possible credential values.
    raise SystemExit('Review required for potential credentials at: ' + json.dumps(findings))
AUDIT.write_text(json.dumps(dict(
    status='PASS', selection='explicit root-file and text-directory allowlist',
    scanned_files=len(files), detected_secret_patterns=0, redactions=[],
    synthetic_false_positive='Exact user:password@example.com fixture URL allowed for screening only; original test and log bytes preserved.',
    redaction_note='No sensitive values detected in selected artifacts; no log redactions made.',
    excluded=['.venv', '.git', '.omx', 'caches', 'build/dist', '*.egg-info', '.DS_Store',
              'data/logs', '.env*', 'unlisted root files', 'symlinks', 'large files'],
    limitation='Pattern screening and allowlist inspection are not a guarantee against every possible secret.'
), indent=2) + '\n')
files.append(AUDIT)
entries = [dict(path=str(p.relative_to(ROOT)), size=p.stat().st_size,
                sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(files)]
MANIFEST.write_text(json.dumps(dict(schema_version=1, files=entries,
                                  note='Manifest excludes itself; all listed files verified against ZIP bytes.'),
                               indent=2) + '\n')
files.append(MANIFEST)
with zipfile.ZipFile(OUT, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(files):
        archive.write(path, arcname=str(path.relative_to(ROOT)))
with zipfile.ZipFile(OUT) as archive:
    assert archive.testzip() is None
    assert len(archive.namelist()) == len(files)
    for entry in entries:
        assert hashlib.sha256(archive.read(entry['path'])).hexdigest() == entry['sha256']
    assert archive.read(str(MANIFEST.relative_to(ROOT))) == MANIFEST.read_bytes()
    assert not any(set(Path(n).parts) & EXCLUDED for n in archive.namelist())
digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
(ROOT / 'protocol_probe_review.zip.sha256').write_text(f'{digest}  protocol_probe_review.zip\n')
receipt = dict(status='PASS', archive=str(OUT), size_bytes=OUT.stat().st_size,
               file_count=len(files), sha256=digest, crc='PASS', payload_hashes='PASS',
               secret_scan='PASS (allowlist + pattern screening)',
               manifest='reports/round1/probe_package_manifest.json')
(ROOT / 'protocol_probe_review.zip.verify.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt, indent=2))
