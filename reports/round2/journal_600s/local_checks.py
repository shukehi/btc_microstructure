"""Execute the unchanged thirteen CI checks with explicit local tool/output paths."""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = Path(".github/scripts/ci_checks.sh")
    checks = []
    for line in source.read_text().splitlines():
        if not line.startswith("run_check "):
            continue
        _, name, *original = shlex.split(line)
        command = list(original)
        if not command[0].startswith(".venv/bin/"):
            raise RuntimeError("unknown CI executable; review adaptation first")
        command[0] = str(args.tools / Path(command[0]).name)
        command = [arg.replace("ci-results/", str(args.output) + "/") for arg in command]
        if name == "offline-e2e":
            command.extend(["--output", str(args.output / "offline-smoke")])
        if name.startswith("pytest"):
            command.append("--basetemp=" + str(args.output / (name + "-tmp")))
        checks.append((name, original, command))
    assert len(checks) == 13, "CI check inventory changed; do not silently omit checks"
    records = []
    with (args.output / "results.tsv").open("x") as results:
        for name, original, command in checks:
            print("RUN " + name, flush=True)
            with (args.output / (name + ".log")).open("x") as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
            records.append(
                {
                    "name": name,
                    "original": original,
                    "actual": command,
                    "exit_code": result.returncode,
                }
            )
            results.write(f"{name}\t{result.returncode}\n")
            results.flush()
            (args.output / "commands.json").write_text(
                json.dumps(
                    {
                        "ci_script_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "linux_network_namespace": "NOT_RUN_LOCAL_MACOS",
                        "checks": records,
                    },
                    indent=2,
                )
                + "\n"
            )
            print(f"EXIT {name} {result.returncode}", flush=True)
    return int(any(record["exit_code"] for record in records))


if __name__ == "__main__":
    sys.exit(main())
