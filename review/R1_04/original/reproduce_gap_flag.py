"""R104-01: original raw GAP_KNOWN must not become a healthy flow feature.

Run in the reviewed checkout with its locked Python. Writes only a fresh,
explicitly synthetic evidence directory under data/. Exit 1 is the observed
contract counterexample, not a failure of the reproduction harness.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import tempfile

import yaml

from btc_microstructure.offline import OfflineConfig, run_synthetic
from btc_microstructure.offline_storage import Store


def denied(*args, **kwargs):
    raise AssertionError("network forbidden in R1-04 reproduction")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-parent", type=Path, default=Path("data/r104-gap-probes"))
    args = parser.parse_args()
    for name in ("create_connection", "getaddrinfo"):
        setattr(socket, name, denied)
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    original = Path("fixtures/synthetic/vertical_slice/warmed_delta.json")
    config_source = Path("config/offline.synthetic.yaml")
    fixture = json.loads(original.read_text())
    event = next(e for e in fixture["events"] if e["kind"] == "RAW")
    event["quality_flags"] = ["GAP_KNOWN"]
    args.output_parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="attempt-", dir=args.output_parent))
    fixture_path = output / "synthetic-gap.json"
    fixture_path.write_text(json.dumps(fixture, indent=2) + "\n")
    settings = deepcopy(yaml.safe_load(config_source.read_text()))
    settings["synthetic_input"] = str(fixture_path.resolve())
    settings["output_root"] = str((output / "run").resolve())
    config_path = output / "offline.yaml"
    config_path.write_text(yaml.safe_dump(settings))
    config = OfflineConfig.load(config_path)
    summary = run_synthetic(config, output / "run")
    store = Store(output / "run")
    raw = next(r for r in store.read_rows() if r["kind"] == "RAW")
    normalized = store.read_table("normalized_trades")[0]
    feature = next(
        r
        for r in store.read_table("features")
        if r["feature_cutoff_ns"] == 31_000_000_000 and r["window_seconds"] == 30
    )
    result = {
        "reviewed_commit": "0b65be4933e9c36abedf784e608b832c56cee131",
        "evidence_kind": "SYNTHETIC",
        "original_fixture_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "trigger": "add quality_flags=[GAP_KNOWN] to first synthetic RAW only",
        "expected": {
            "normalized_flag_contains": "GAP_KNOWN",
            "feature_flag_contains": "GAP_KNOWN",
            "trade_delta_base": None,
            "volume_base": None,
        },
        "actual": {
            "collect_status": summary["status"],
            "raw_flags": raw["quality_flags"],
            "normalized_flags": normalized["quality_flags"],
            "feature_flags": feature["quality_flags"],
            "trade_delta_base": str(feature["trade_delta_base"]),
            "volume_base": str(feature["volume_base"]),
        },
        "output": str(output),
    }
    print(json.dumps(result, indent=2), flush=True)
    assert feature["trade_delta_base"] is None, (
        "GAP_KNOWN must invalidate the affected flow window (docs/01 quality contract)"
    )


if __name__ == "__main__":
    main()
