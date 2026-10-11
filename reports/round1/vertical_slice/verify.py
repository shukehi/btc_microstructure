"""Capture actual local commands without erasing previous runs (no Binance access)."""

from pathlib import Path
import json
import platform
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
base = root / "reports/round1/vertical_slice"
index = 1
while (base / f"checks-{index:02d}").exists():
    index += 1
out = base / f"checks-{index:02d}"
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
        "ruff",
        [str(root / ".venv/bin/ruff"), "check", "src", "tests", ".github/scripts/offline_smoke.py"],
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
        "integrity",
        [
            "git",
            "diff",
            "--exit-code",
            "origin/main",
            "--",
            "docs",
            "examples/synthetic_cases.json",
            "config/research.example.yaml",
            "review",
            "tasks",
            "CODEX_START_HERE.md",
        ],
    ),
]
results = []
for name, command in checks:
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=180)
    (out / f"{name}.log").write_text(result.stdout + result.stderr)
    results.append({"name": name, "command": command, "exit_code": result.returncode})
    print(name, result.returncode, flush=True)
(out / "commands.json").write_text(json.dumps(results, indent=2) + "\n")
(out / "environment.json").write_text(
    json.dumps({"python": sys.version, "platform": platform.platform()}, indent=2) + "\n"
)
print(out)
sys.exit(int(any(row["exit_code"] for row in results)))
