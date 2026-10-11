"""R1-01 delivery evidence refresh; runs only local/offline checks."""
import datetime
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/round1"
invalid = OUT / "invalid-grid.yaml"
original = (ROOT / "config/research.example.yaml").read_text()
assert original.count("  grid_seconds: 1\n") == 1
invalid.write_text(original.replace("  grid_seconds: 1\n", "  grid_seconds: 0\n"))
commands = [
    ("os", ["sw_vers"], 0),
    ("python", [".venv/bin/python", "--version"], 0),
    ("dependencies", [".venv/bin/python", "-m", "pip", "list", "--format=json"], 0),
    ("git-status", ["git", "status", "--short", "--branch"], 0),
    ("pytest", [".venv/bin/python", "-m", "pytest", "-q", "--junitxml=reports/round1/delivery-pytest.xml"], 0),
    ("cli-valid", [".venv/bin/python", "-m", "btc_microstructure", "validate-config", "config/research.example.yaml"], 0),
    ("cli-invalid", [".venv/bin/python", "-m", "btc_microstructure", "validate-config", "reports/round1/invalid-grid.yaml"], 1),
    ("cli-script", [".venv/bin/btc-microstructure", "validate-config", "config/research.example.yaml"], 0),
    ("ruff", [".venv/bin/ruff", "check", "src", "tests"], 0),
    ("format", [".venv/bin/ruff", "format", "--check", "src", "tests"], 0),
    ("mypy", [".venv/bin/mypy", "src"], 0),
    ("pip-check", [".venv/bin/python", "-m", "pip", "check"], 0),
    ("preservation", ["shasum", "-a", "256", "-c", "reports/round1/preserved.sha256"], 0),
    ("implementation", ["shasum", "-a", "256", "-c", "reports/round1/implementation.sha256"], 0),
]
records = []
for name, command, expected in commands:
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    status = "PASS" if result.returncode == expected else "FAIL"
    record = dict(name=name, command=command, cwd=str(ROOT), started_utc=started,
                  exit_code=result.returncode, expected_exit_code=expected, status=status,
                  log=f"reports/round1/delivery-{name}.log")
    (ROOT / record["log"]).write_text(
        json.dumps(record, ensure_ascii=False) + "\n--- stdout ---\n" + result.stdout
        + "\n--- stderr ---\n" + result.stderr)
    records.append(record)
    print(f"{name}: {status}, exit={result.returncode}", flush=True)
root = ET.parse(OUT / "delivery-pytest.xml").getroot()
suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
total = sum(int(s.get("tests", "0")) for s in suites)
failed = sum(int(s.get("failures", "0")) + int(s.get("errors", "0")) for s in suites)
skipped = sum(int(s.get("skipped", "0")) for s in suites)
summary = dict(commands=records, tests=dict(passed=total-failed-skipped, failed=failed, skipped=skipped))
(OUT / "delivery-commands.json").write_text(json.dumps(summary, indent=2) + "\n")
# Missing Git metadata is an explicitly reported limitation, not a software failure.
raise SystemExit(int(any(r["status"] == "FAIL" and r["name"] != "git-status" for r in records)))
