"""Record R104 quality checks without replacing previous evidence directories."""

import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
base = root / "reports/round1/quality_propagation_fix"
index = 1
while (base / f"after-{index:02d}").exists():
    index += 1
out = base / f"after-{index:02d}"
out.mkdir()
checks = [("default", [sys.executable, "-m", "pytest", "-q", f"--junitxml={out}/default.xml"])]
for label, path in [
    ("independent01", "review/R1_01/checks/test_review_regressions.py"),
    ("independent02", "review/R1_02/checks/test_r1_02_regressions.py"),
    ("independent03", "review/R1_03/checks/test_r1_03_review.py"),
    ("independent04", "review/R1_04/checks/test_r104_quality_review.py"),
]:
    checks.append(
        (label, [sys.executable, "-m", "pytest", "-q", path, f"--junitxml={out}/{label}.xml"])
    )
paths = [
    "src",
    "tests",
    ".github/scripts/offline_smoke.py",
    "review/R1_03/checks/test_r1_03_review.py",
    "review/R1_04/checks/test_r104_quality_review.py",
]
checks += [
    (
        "original-parquet",
        [
            sys.executable,
            "review/R1_04/original/reproduce_gap_flag.py",
            "--output-parent",
            f"data/r104-quality-final-{index:02d}",
        ],
    ),
    ("ruff", [str(root / ".venv/bin/ruff"), "check", *paths]),
    ("format", [str(root / ".venv/bin/ruff"), "format", "--check", *paths]),
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
            f"data/r104-quality-smoke-{index:02d}",
        ],
    ),
    (
        "integrity",
        [
            "git",
            "diff",
            "--exit-code",
            "0b65be4933e9c36abedf784e608b832c56cee131",
            "--",
            "docs",
            "config",
            "examples",
            "requirements-dev.lock",
            "pyproject.toml",
            ".github/workflows",
            "src/btc_microstructure/offline_storage.py",
            "tests/unit/test_offline_core.py",
            "tests/unit/test_offline_pipeline.py",
            "tests/unit/test_offline_scenarios.py",
            "tests/unit/test_offline_storage.py",
            "tests/unit/test_r103_ordering.py",
            "tests/unit/test_r103_recovery.py",
            "review/R1_01",
            "review/R1_02",
            "review/R1_03",
            "reports/round1/03_vertical_slice.md",
            "reports/round1/03_vertical_slice_fix.md",
        ],
    ),
]
results = []
for name, argv in checks:
    run = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=180)
    (out / f"{name}.log").write_text(run.stdout + run.stderr)
    results.append({"check": name, "argv": argv, "cwd": str(root), "exit_code": run.returncode})
    print(name, run.returncode, flush=True)
(out / "commands.json").write_text(json.dumps(results, indent=2) + "\n")
versions = {}
for line in (root / "requirements-dev.lock").read_text().splitlines():
    if "==" in line:
        name, expected = line.split("==")
        actual = importlib.metadata.version(name)
        versions[name] = {"expected": expected, "actual": actual}
assert all(v["expected"] == v["actual"] for v in versions.values())
(out / "locked-versions.json").write_text(json.dumps(versions, indent=2) + "\n")
print(out)
sys.exit(int(any(r["exit_code"] for r in results)))
