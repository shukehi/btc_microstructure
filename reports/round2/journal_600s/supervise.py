"""Independent bounded process-group watchdog for one offline scenario."""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    free = shutil.disk_usage(args.output).free
    # Reserve all four planned 512MiB runs plus 1GiB validation evidence and
    # the 2.5GiB startup floor. This is stricter admission, not a raised cap.
    if free < int(5.5 * 1024**3):
        raise SystemExit("NOT_RUN: insufficient conservative multi-run disk margin")
    began = time.monotonic()
    minimum = free
    samples = []
    reason = None
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    with (args.output / "scenario.log").open("x") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        while process.poll() is None:
            elapsed = time.monotonic() - began
            free = shutil.disk_usage(args.output).free
            minimum = min(minimum, free)
            current = 0
            for line in subprocess.check_output(
                ["ps", "-axo", "pgid=,rss="], text=True
            ).splitlines():
                fields = line.split()
                if len(fields) == 2 and int(fields[0]) == process.pid:
                    current += int(fields[1]) * 1024
            samples.append({"elapsed": elapsed, "disk_free": free, "group_rss": current})
            if elapsed >= 660:
                reason = "SUPERVISOR_660S_DEADLINE"
            elif free < 2 * 1024**3:
                reason = "SUPERVISOR_DISK_HARD_FLOOR"
            elif current > 512 * 1024**2:
                reason = "SUPERVISOR_GROUP_RSS_LIMIT"
            if reason:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(1)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(1)
                break
            time.sleep(0.1)
    result = {
        "exit_code": process.returncode,
        "stop_reason": reason,
        "elapsed_seconds": time.monotonic() - began,
        "disk_free_start": samples[0]["disk_free"] if samples else free,
        "disk_free_min": minimum,
        "group_rss_peak": max((s["group_rss"] for s in samples), default=0),
        "samples": len(samples),
        "sample_target_seconds": 0.1,
        "scope": "controller_and_replays; OS scheduling is not hard realtime",
    }
    (args.output / "watchdog.json").write_text(json.dumps(result, indent=2) + "\n")
    with (args.output / "resource-samples.jsonl").open("x") as handle:
        for row in samples:
            handle.write(json.dumps(row) + "\n")
    print(json.dumps(result), flush=True)
    return 1 if reason else process.returncode


if __name__ == "__main__":
    sys.exit(main())
