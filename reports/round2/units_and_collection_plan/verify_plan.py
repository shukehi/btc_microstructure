"""Offline document/plan/A2 consistency checks; never downloads or probes."""

import socket
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform


def deny_network(*args, **kwargs):
    raise AssertionError("R2-03 verifier has no network permission")


socket.getaddrinfo = deny_network
socket.create_connection = deny_network
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("nonfinite JSON value")

    return json.loads(path.read_text(), object_pairs_hook=unique, parse_constant=nonfinite)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local-rest", type=Path)
    parser.add_argument("--contract-pdf", type=Path)
    parser.add_argument("--clearing-pdf", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    here = Path(__file__).resolve().parent
    unit = load(here / "quantity_unit_evidence.json")
    plan = load(here / "bounded_collection_plan.json")
    sources = load(here / "sources.json")
    source_ids = {s["id"] for s in sources["sources"]}
    assert digest(root / unit["source_registry"]["path"]) == unit["source_registry"]["sha256"]
    assert len(source_ids) == len(sources["sources"])
    assert unit["runtime_applied"] is False
    assert unit["review_status"] == "PENDING_INDEPENDENT_REVIEW"
    assert unit["instrument"]["market"] == "um_perpetual"
    assert unit["instrument"]["instrument_id"] == plan["instrument"]
    assert [unit["fields"][f]["unit"] for f in ("q", "nq", "p", "notional_quote")] == [
        "BTC",
        "BTC",
        "USDT/BTC",
        "USDT",
    ]
    assert all(unit["fields"][f]["multiplier_decimal"] == "1" for f in ("q", "nq", "p"))
    for step in unit["reasoning"]:
        assert set(step["sources"]) <= source_ids | {"A2"}
    for ref in unit["A2_references"].values():
        assert digest(root / ref["path"]) == ref["sha256"]
    sample_dir = root / "reports/round2/protocol_readiness/a2/20261012-plan-a2-attempt01"
    report = load(sample_dir / "protocol_probe_report.json")
    budget = load(sample_dir / "endpoint_budget.json")
    manifest = load(sample_dir / "publication-manifest.json")
    for name, expected in manifest["files"].items():
        assert digest(sample_dir / name) == expected["sha256"]
        assert (sample_dir / name).stat().st_size == expected["bytes"]
    frames = report["ws"]["messages"]
    assert len(frames) == 20
    sizes = []
    for frame in frames:
        path = sample_dir / frame["sample"]
        assert digest(path) == frame["sha256"]
        obj = load(path)
        assert obj["stream"] == "btcusdt@aggTrade"
        data = obj["data"]
        assert data["s"] == "BTCUSDT" and data["st"] == 1
        assert type(data["m"]) is bool
        assert all(isinstance(data[k], str) for k in ("p", "q", "nq"))
        p, q, nq = (Decimal(data[k]) for k in ("p", "q", "nq"))
        assert p.is_finite() and p > 0 and q.is_finite() and q > 0
        assert nq.is_finite() and 0 <= nq <= q
        assert frame["inspection"]["quantity_unit"] == "UNKNOWN"
        sizes.append(path.stat().st_size)
    assert sum(sizes) == 4082 and Decimal(sum(sizes)) / 20 == Decimal("204.1")
    assert report["metadata"]["quantity_unit"] == "UNKNOWN"
    assert budget["status"] == "UNVERIFIED_FOR_CONTINUOUS_COLLECTION"
    assert budget["continuous_collection_enabled"] is False
    local_rest = {"status": "NOT_RUN", "reason": "optional local original not supplied"}
    if args.local_rest:
        assert digest(args.local_rest) == unit["A2_full_REST_sha256"]
        original = load(args.local_rest)
        selected = [s for s in original["symbols"] if s.get("symbol") == "BTCUSDT"]
        assert len(selected) == 1
        excerpt = load(sample_dir / "rest-btcusdt-excerpt.json")
        assert all(selected[0][k] == v for k, v in excerpt["symbol"].items())
        assert original["rateLimits"] == budget["observed_rate_limits"]
        local_rest = {
            "status": "PASS",
            "bytes": args.local_rest.stat().st_size,
            "sha256": digest(args.local_rest),
            "full_original_unchanged": True,
        }
    documents = {}
    for source_id, path in (("S01", args.contract_pdf), ("S02", args.clearing_pdf)):
        if path:
            spec = next(s for s in sources["sources"] if s["id"] == source_id)
            assert digest(path) == spec["sha256"]
            documents[source_id] = {"status": "PASS", "sha256": digest(path)}
        else:
            documents[source_id] = {"status": "NOT_RUN", "reason": "optional local PDF omitted"}
    assert not plan["network_authorized"] and not plan["runtime_applied"]
    assert not plan["continuous_collection_enabled"]
    times = plan["time_seconds"]
    assert times["startup"] + times["observation"] + times["shutdown"] == times["total"] == 660
    assert sum(times["startup_parts"].values()) == times["startup"]
    assert sum(times["shutdown_parts"].values()) == times["shutdown"]
    assert plan["network"]["rest_requests"] * plan["network"]["rest_documented_weight_each"] == 1
    assert plan["network"]["retry"] == plan["network"]["reconnect"] == 0
    assert plan["network"]["ws_handshakes"] == plan["network"]["url_subscriptions"] == 1
    assert plan["network"]["json_subscription_messages"] == 0
    store, receive, queue = plan["storage"], plan["receive"], plan["queue"]
    assert sum(store["partition_bytes"].values()) == store["run_total_bytes"] == 512 * 1048576
    assert store["start_free_bytes"] - store["run_total_bytes"] >= store["stop_free_bytes"]
    assert store["stop_free_bytes"] < store["warn_free_bytes"] < store["start_free_bytes"]
    count, payload = receive["max_messages"], receive["max_ws_payload_bytes"]
    # Independent worst-case base64 bound: sum ceil(n_i/3) <= floor((S+2N)/3).
    raw_bound = 4 * ((payload + 2 * count) // 3) + count * store["raw_envelope_overhead_max_bytes"]
    assert raw_bound < store["partition_bytes"]["ws_raw_envelopes"]
    single_bound = (
        4 * ((receive["max_message_bytes"] + 2) // 3) + store["raw_envelope_overhead_max_bytes"]
    )
    assert single_bound < queue["encoded_single_record_bytes"] <= queue["encoded_bytes"]
    assert (
        store["max_control_record_bytes"] * plan["state"]["max_control_inputs"]
        <= store["partition_bytes"]["control"]
    )
    assert (
        store["max_manifests"] * store["max_manifest_bytes"]
        <= store["partition_bytes"]["manifests_indexes"]
    )
    assert (
        store["max_one_Parquet_temp_bytes"] + store["raw_segment_or_temp_bytes"]
        < store["partition_bytes"]["temporary_orphan_quarantine_files"]
    )
    assert (
        store["terminal_audit_max_records"] * store["terminal_audit_record_bytes"]
        == store["terminal_audit_reserve_bytes"]
    )
    assert (
        store["terminal_audit_reserve_bytes"] < store["partition_bytes"]["diagnostics_and_receipts"]
    )
    send = plan["send_control"]
    assert (
        send["normal_rolling_1s_max"] + send["reserved_close_slots"] == send["rolling_any_1s_max"]
    )
    assert send["normal_total_max"] + send["reserved_close_slots"] == send["total_max"]
    assert send["total_max"] * send["max_payload_each"] == send["payload_bytes_total_max"]
    assert (
        plan["state"]["max_windows"] * plan["state"]["max_cutoffs"]
        == plan["state"]["max_feature_rows"]
    )
    assert (
        plan["state"]["max_raw_inputs"] + plan["state"]["max_control_inputs"]
        == plan["state"]["max_total_inputs"]
    )
    assert plan["probe_unchanged"]["total_allowed_seconds"] == [2, 60]
    assert plan["probe_unchanged"]["max_messages"] == 20
    # Illustrative artificial amount arithmetic only, not A2 features or research.
    assert Decimal("100") * Decimal("0.30") == Decimal("30.00")
    output = {
        "status": "PASS",
        "exit_code": 0,
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "baseline": sources["baseline"],
        "new_cli_session": sources["new_cli_session"],
        "python": platform.python_version(),
        "platform": platform.system() + "/" + platform.machine(),
        "script_sha256": digest(Path(__file__)),
        "inputs_sha256": {
            name: digest(here / name)
            for name in (
                "sources.json",
                "quantity_unit_evidence.json",
                "bounded_collection_plan.json",
            )
        },
        "market_requests": 0,
        "scope": "JSON/references/A2 byte hashes/native fields/budget arithmetic; documentary inference separately read, not algorithmically proven",
        "A2_manifest_files": len(manifest["files"]),
        "A2_ws_samples": len(frames),
        "A2_ws_bytes": sum(sizes),
        "A2_mean_bytes": "204.1",
        "A2_local_REST": local_rest,
        "local_official_PDF_hashes": documents,
        "arithmetic": {
            "total_seconds": 660,
            "own_REST_weight": 1,
            "reserve_example": 240,
            "own_fraction_of_A2_minute_snapshot": "1/2400",
            "raw_base64_plus_envelope_upper_bytes": raw_bound,
            "single_raw_envelope_upper_bytes": single_bound,
            "run_bytes": store["run_total_bytes"],
            "size_example_100000_at_A2_mean": str(Decimal("204.1") * count),
            "not_peak_or_future_forecast": True,
        },
        "future_implementation_600s_and_24h": "NOT_RUN",
        "historical_unit_and_budget_preserved": True,
    }
    with args.output.open("x") as f:
        f.write(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "status": "PASS",
                "sample_count": 20,
                "raw_upper_bytes": raw_bound,
                "run_bytes": store["run_total_bytes"],
                "market_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
