"""Acceptance tooling must guard real child processes and select this checkout."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("mode", ["spawn", "pytest"])
def test_acceptance_import_and_network_guard_in_children(tmp_path: Path, mode: str) -> None:
    root = Path(__file__).resolve().parents[2]
    tools = root / "reports/round2/journal_600s"
    evidence = tmp_path / "imports.jsonl"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(root / "src"), str(tools)]),
        "R204_OFFLINE_GUARD": "1",
        "R204_EXPECTED_SOURCE": str(root / "src"),
        "R204_IMPORT_EVIDENCE": str(evidence),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    script = tools / "environment_probe.py"
    command = [sys.executable, "-B", str(script)]
    if mode == "pytest":
        command = [sys.executable, "-B", "-m", "pytest", "-q", "-s", str(script)]
    result = subprocess.run(command, env=env, cwd=root, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in evidence.read_text().splitlines()]
    assert rows
    assert all(Path(row["package_origin"]).is_relative_to(root / "src") for row in rows)
    assert all(row["guard"] == "INET_SOCKET_DNS_DENIED_UNIX_IPC_ALLOWED" for row in rows)
    if mode == "spawn":
        values = json.loads(result.stdout)
        assert values["parent"]["pid"] != values["spawn"]["pid"]
        assert all(all(row["network_denied"].values()) for row in values.values())
    else:
        assert '"inet_socket": true, "dns": true' in result.stdout


def test_acceptance_wrong_source_fails_before_project_import(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(root / "src"), str(root / "reports/round2/journal_600s")]
        ),
        "R204_OFFLINE_GUARD": "1",
        "R204_EXPECTED_SOURCE": str(tmp_path / "wrong-source"),
    }
    result = subprocess.run(
        [sys.executable, "-B", "-c", "raise AssertionError('reached')"],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 92
    assert "reached" not in result.stderr
