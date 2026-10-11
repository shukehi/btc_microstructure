"""Actual CLI + Parquet acceptance, run inside CI's network namespace."""

import argparse
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import shutil
import sys

from btc_microstructure.offline_storage import Store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("ci-results/offline-smoke"))
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    config = "config/offline.synthetic.yaml"
    commands = [
        ("validate", ["validate-offline", config]),
        ("collect", ["collect", config, "--mode", "synthetic", "--output", str(root / "collect")]),
        (
            "replay1",
            ["replay", config, "--input", str(root / "collect"), "--output", str(root / "replay1")],
        ),
        (
            "replay2",
            ["replay", config, "--input", str(root / "collect"), "--output", str(root / "replay2")],
        ),
        (
            "build",
            [
                "build-features",
                config,
                "--input",
                str(root / "raw-only"),
                "--output",
                str(root / "build"),
            ],
        ),
    ]
    results = []
    for name, tail in commands:
        if name == "build":
            shutil.copytree(root / "collect" / "raw", root / "raw-only" / "raw")
            (root / "raw-only" / "manifests").mkdir()
            for manifest in (root / "collect" / "manifests").glob("manifest-raw-*.json"):
                shutil.copy2(manifest, root / "raw-only" / "manifests" / manifest.name)
        command = [sys.executable, "-m", "btc_microstructure", *tail]
        run = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (root / f"{name}.log").write_text(run.stdout + run.stderr)
        results.append({"command": command, "exit_code": run.returncode})
        (root / "commands.json").write_text(json.dumps(results, indent=2) + "\n")
        print(name, run.returncode, run.stdout, run.stderr, flush=True)
        if run.returncode:
            return run.returncode
    source = Store(root / "collect")
    trades = source.read_table("normalized_trades")
    features = source.read_table("features")
    fixed = next(
        row
        for row in features
        if row["feature_cutoff_ns"] == 31_000_000_000 and row["window_seconds"] == 30
    )
    assert len(trades) == 3
    assert len([row for row in source.read_rows() if row["kind"] == "RAW"]) == 4
    assert fixed["buy_volume_base"] == Decimal("0.50")
    assert fixed["sell_volume_base"] == Decimal("0.10")
    assert fixed["volume_base"] == Decimal("0.60")
    assert fixed["trade_delta_base"] == Decimal("0.40")
    assert abs(fixed["trade_imbalance"] - 2 / 3) < 1e-12
    assert fixed["agg_message_count"] == 3
    assert fixed["quality_flags"] == []
    assert len(features) == 70
    assert len({row["feature_cutoff_ns"] for row in features}) == 35
    assert features[0]["feature_available_ns"] != features[0]["feature_cutoff_ns"]
    for name in ("replay1", "replay2"):
        summary = json.loads((root / name / "summary.json").read_text())
        assert summary["original_logical_sha256"] == summary["replay_logical_sha256"]
    rebuilt = Store(root / "build")
    assert rebuilt.read_table("features") == features
    assert rebuilt.read_table("normalized_trades") == trades
    assert rebuilt.read_table("quarantine") == source.read_table("quarantine")
    print(
        "PASS: fixed Decimal values, raw duplicates, 1s grid, Parquet readback, two replays + build"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
