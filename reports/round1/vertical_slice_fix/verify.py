"""Record locked offline regression commands in a new evidence directory each run."""

import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from packaging.utils import canonicalize_name

root = Path(__file__).resolve().parents[3]
base = root / "reports/round1/vertical_slice_fix"
index = 1
while (base / f"after-{index:02d}").exists():
    index += 1
out = base / f"after-{index:02d}"
out.mkdir()
checks = [
    ("default", [sys.executable, "-m", "pytest", "-q", f"--junitxml={out}/default.xml"]),
    (
        "independent01",
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "review/R1_01/checks/test_review_regressions.py",
            f"--junitxml={out}/independent01.xml",
        ],
    ),
    (
        "independent02",
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "review/R1_02/checks/test_r1_02_regressions.py",
            f"--junitxml={out}/independent02.xml",
        ],
    ),
    (
        "independent03",
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "review/R1_03/checks/test_r1_03_review.py",
            f"--junitxml={out}/independent03.xml",
        ],
    ),
    (
        "ruff",
        [
            str(root / ".venv/bin/ruff"),
            "check",
            "src",
            "tests",
            ".github/scripts/offline_smoke.py",
            "review/R1_03/checks/test_r1_03_review.py",
        ],
    ),
    (
        "format",
        [
            str(root / ".venv/bin/ruff"),
            "format",
            "--check",
            "src",
            "tests",
            ".github/scripts/offline_smoke.py",
            "review/R1_03/checks/test_r1_03_review.py",
        ],
    ),
    ("mypy", [str(root / ".venv/bin/mypy"), "src", ".github/scripts/offline_smoke.py"]),
    ("pip-check", [sys.executable, "-m", "pip", "check"]),
    (
        "config",
        [
            sys.executable,
            "-m",
            "btc_microstructure",
            "validate-config",
            "config/research.example.yaml",
        ],
    ),
    (
        "offline-config",
        [
            sys.executable,
            "-m",
            "btc_microstructure",
            "validate-offline",
            "config/offline.synthetic.yaml",
        ],
    ),
    (
        "offline-e2e",
        [
            sys.executable,
            ".github/scripts/offline_smoke.py",
            "--output",
            f"data/r103-fix-smoke-{index:02d}",
        ],
    ),
    (
        "integrity",
        [
            "git",
            "diff",
            "--exit-code",
            "03ce9c7ea273cf0ab42915a012e10ba7e6193c24",
            "--",
            "docs",
            "examples",
            "config",
            "tasks",
            "CODEX_START_HERE.md",
            "requirements-dev.lock",
            "pyproject.toml",
            "review/R1_01",
            "review/R1_02",
            "reports/round1/vertical_slice",
            "reports/round1/03_vertical_slice.md",
            "tests/unit/test_offline_core.py",
            "tests/unit/test_offline_storage.py",
            "tests/unit/test_offline_pipeline.py",
            "tests/unit/test_offline_scenarios.py",
        ],
    ),
]
results = []
for name, command in checks:
    run = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=180)
    (out / f"{name}.log").write_text(run.stdout + run.stderr)
    results.append({"name": name, "argv": command, "cwd": str(root), "exit_code": run.returncode})
    print(name, run.returncode, flush=True)
(out / "commands.json").write_text(json.dumps(results, indent=2) + "\n")
versions = {}
for line in (root / "requirements-dev.lock").read_text().splitlines():
    if "==" in line and not line.startswith("#"):
        name, expected = line.split("==")
        actual = importlib.metadata.version(name)
        versions[canonicalize_name(name)] = {"expected": expected, "actual": actual}
assert all(item["actual"] == item["expected"] for item in versions.values())
(out / "locked-versions.json").write_text(json.dumps(versions, indent=2) + "\n")
print(out)
sys.exit(int(any(result["exit_code"] for result in results)))
