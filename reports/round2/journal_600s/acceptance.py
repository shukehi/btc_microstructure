"""One guarded TEST_ONLY scenario with physical Journal, oracle and two replays.

Run only under the opt-in sitecustomize guard and the independent supervisor.
This tool does not authorize another scenario or a network connection.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "unit"))

from bounded_fakes import agg_trade, metadata_body  # noqa: E402
from test_bounded_live import make_test_only_approval, tree_digest  # noqa: E402
from btc_microstructure import bounded_live  # noqa: E402
from btc_microstructure.bounded_transport import MetadataReply, WsEvent  # noqa: E402
from btc_microstructure.journal_prototype import (  # noqa: E402
    TestOnlyJournalProcessWriter,
    recover_test_only_journal,
    replay_test_only_storage,
)
from btc_microstructure.offline_core import NS  # noqa: E402


def distribution(values: list[float]) -> dict[str, Any]:
    values = sorted(values)
    return {
        "count": len(values),
        **{
            name: values[min(len(values) - 1, max(0, int(len(values) * q + 0.999999) - 1))]
            if values
            else None
            for name, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99), ("max", 1))
        },
    }


class ScenarioClock:
    def __init__(self, real: bool) -> None:
        self.real = real
        self.value = 1_800_000_000 * NS

    def wall_ns(self) -> int:
        return time.time_ns() if self.real else self.value

    def monotonic_ns(self) -> int:
        return time.monotonic_ns() if self.real else self.value


class ArtificialTransport:
    TEST_ONLY = True

    def __init__(self, clock: ScenarioClock, seconds: int, rate: int) -> None:
        self.clock, self.seconds, self.rate = clock, seconds, rate
        self.sent = 0
        self.start: int | None = None
        self.next: int | None = None
        self.close_started: int | None = None
        self.closed_at: int | None = None
        self.delivery_lateness: list[float] = []

    async def metadata(self) -> MetadataReply:
        return MetadataReply(200, {}, metadata_body())

    async def connect(self) -> None:
        self.start = self.clock.monotonic_ns()
        self.next = self.start + NS // self.rate

    async def poll(self, timeout_ns: int) -> WsEvent:
        assert self.next is not None
        end = self.clock.monotonic_ns() + timeout_ns
        target = min(self.next, end)
        if self.clock.real:
            # Return early on watchdog-only polls. Never pretend that sleeping
            # advanced the clock by an exact amount; OS times remain untouched.
            wait = (target - self.clock.monotonic_ns()) / NS
            if wait > 0.005:
                await asyncio.sleep(wait - 0.005)
            if self.clock.monotonic_ns() < self.next:
                return WsEvent("tick")
        else:
            self.clock.value = target
        if self.sent >= self.seconds * self.rate or self.next > end:
            return WsEvent("tick")
        scheduled = self.next
        self.sent += 1
        self.next += NS // self.rate
        self.delivery_lateness.append((self.clock.monotonic_ns() - scheduled) / NS)
        stamp = self.clock.wall_ns() // 1_000_000
        # Generate each payload only when it is actually delivered.
        return WsEvent(
            "message",
            agg_trade(
                agg_id=self.sent,
                quantity="0.001",
                buyer_is_maker=bool(self.sent % 2),
                event_time=stamp,
                trade_time=stamp,
            ),
        )

    async def send_control(self, kind: str, payload: bytes = b"") -> None:
        self.close_started = self.clock.monotonic_ns()

    async def close(self) -> None:
        self.closed_at = self.clock.monotonic_ns()


class ObservedWriter(TestOnlyJournalProcessWriter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.raw_sequences: dict[str, int] = {}
        self.command_samples: list[float] = []
        self.queue_records_peak = self.queue_bytes_peak = 0
        self.close_began: int | None = None
        self.close_done: int | None = None
        super().__init__(*args, **kwargs)

    def append(self, row: dict[str, Any]) -> None:
        super().append(row)
        self.queue_records_peak = max(self.queue_records_peak, len(self.rows))
        self.queue_bytes_peak = max(self.queue_bytes_peak, self.encoded_bytes)
        if row["kind"] == "RAW":
            self.raw_sequences[row["payload_sha256"]] = row["ingest_sequence"]

    def _command(self, request: dict[str, Any]) -> dict[str, Any]:
        began = time.monotonic_ns()
        try:
            return super()._command(request)
        finally:
            self.command_samples.append((time.monotonic_ns() - began) / NS)

    def close(self) -> None:
        self.close_began = time.monotonic_ns()
        try:
            super().close()
        finally:
            self.close_done = time.monotonic_ns()


def oracle(rows: list[dict[str, Any]], features: list[dict[str, Any]]) -> dict[str, Any]:
    """Independent direct RAW arithmetic; no Core/window/replay helper calls."""
    raw = [row for row in rows if row["kind"] == "RAW"]
    cutoffs = {row["cutoff_ns"]: row for row in rows if row["kind"] == "FEATURE_CUTOFF"}
    publications = {row["cutoff_ns"]: row for row in rows if row["kind"] == "FEATURE_PUBLISHED"}
    failures = []
    valid = warmup = 0
    latencies = []
    for feature in features:
        t = feature["feature_cutoff_ns"]
        control = cutoffs[t]
        published = publications[t]
        selected = [
            r
            for r in raw
            if t - 30 * NS <= r["ts_receive_ns"] < t
            and r["ingest_sequence"] <= control["input_cursor"]
        ]
        buy = sell = Decimal(0)
        for row in selected:
            payload = base64.b64decode(row["payload_base64"])
            if hashlib.sha256(payload).hexdigest() != row["payload_sha256"]:
                failures.append("RAW_HASH")
            message = json.loads(payload)
            quantity = Decimal(message["q"])
            if message["m"]:
                sell += quantity
            else:
                buy += quantity
        causal = (
            control["input_cursor"] < control["ingest_sequence"] < published["ingest_sequence"]
            and published["compute_done_observed_ns"]
            <= published["feature_available_ns"]
            == published["ts_receive_ns"]
            and feature["feature_available_ns"] == published["feature_available_ns"]
        )
        if not causal:
            failures.append("PUBLICATION_CAUSALITY")
        if feature["quality_flags"]:
            warmup += 1
            if feature["quality_flags"] != ["WARMUP"] or feature["volume_base"] is not None:
                failures.append("QUALITY_NULL")
        else:
            valid += 1
            volume = buy + sell
            expected = [
                len(selected),
                buy,
                sell,
                volume,
                buy - sell,
                float((buy - sell) / volume) if volume else None,
            ]
            actual = [
                feature[k]
                for k in (
                    "agg_message_count",
                    "buy_volume_base",
                    "sell_volume_base",
                    "volume_base",
                    "trade_delta_base",
                    "trade_imbalance",
                )
            ]
            if actual != expected:
                failures.append("RAW_ARITHMETIC")
        latencies.append((published["feature_available_ns"] - t) / NS)
    return {
        "pass": not failures and bool(features),
        "failure_counts": dict(Counter(failures)),
        "checked_features": len(features),
        "valid": valid,
        "warmup": warmup,
        "cutoff_to_available_seconds": distribution(latencies),
    }


def scenario(args: argparse.Namespace) -> int:
    if os.environ.get("R204_OFFLINE_GUARD") != "1":
        raise RuntimeError("inherited offline guard is mandatory")
    args.output.mkdir(parents=True, exist_ok=False)
    clock = ScenarioClock(args.mode == "wall")
    transport = ArtificialTransport(clock, args.seconds, args.rate)
    writers: list[ObservedWriter] = []

    def factory(root: Path, **kwargs: Any) -> ObservedWriter:
        writer = ObservedWriter(root, **kwargs)
        writers.append(writer)
        return writer

    factory.TEST_ONLY = True  # type: ignore[attr-defined]
    parser_calls = 0
    original_parse = bounded_live.payload_for_core

    def observed_parse(payload: bytes) -> Any:
        nonlocal parser_calls
        writer = writers[0]
        sequence = writer.raw_sequences[hashlib.sha256(payload).hexdigest()]
        assert writer.cursor is not None and writer.cursor >= sequence, "RAW_BEFORE_ACK"
        parser_calls += 1
        return original_parse(payload)

    bounded_live.payload_for_core = observed_parse
    state_peaks = {"reserved_bytes": 0, "pending_bytes": 0}
    original_check = bounded_live.StateReservations.check

    def observed_check(self: Any, **kwargs: Any) -> Any:
        value = original_check(self, **kwargs)
        state_peaks["reserved_bytes"] = max(state_peaks["reserved_bytes"], value[0])
        state_peaks["pending_bytes"] = max(state_peaks["pending_bytes"], kwargs["pending_bytes"])
        return value

    bounded_live.StateReservations.check = observed_check
    config = bounded_live.BoundedLiveConfig(
        run_id=f"TEST_ONLY-600s-{args.mode}-{args.rate}",
        output_root=args.output / "run",
        observation_seconds=args.seconds,
        unit_approval=make_test_only_approval(),
    )
    began = time.monotonic_ns()
    parent_cpu = time.process_time()
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    result = asyncio.run(
        bounded_live.run_test_only_bounded_live(
            config,
            transport=transport,
            clock=clock,
            test_only_writer="journal",
            test_only_writer_factory=factory,
        )
    )
    finished = time.monotonic_ns()
    child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    parent_cpu = time.process_time() - parent_cpu
    child_cpu = sum((child_after.ru_utime, child_after.ru_stime)) - sum(
        (child_before.ru_utime, child_before.ru_stime)
    )
    root = config.output_root
    before = tree_digest(root)
    recovered = recover_test_only_journal(root)
    first = replay_test_only_storage(root)
    second = replay_test_only_storage(root)
    after = tree_digest(root)
    rows = recovered["rows"]
    counted = dict(Counter(row["kind"] for row in rows))
    checked = oracle(rows, first["features"])
    fixed = None
    if args.mode == "simulated" and args.seconds > 30 and first["features"]:
        last = first["features"][-1]
        # Independently hand-calculated full 30s window: 30r alternating trades.
        fixed = (
            last["agg_message_count"] == 30 * args.rate
            and last["buy_volume_base"] == Decimal("0.015") * args.rate
            and last["sell_volume_base"] == Decimal("0.015") * args.rate
            and last["volume_base"] == Decimal("0.030") * args.rate
            and last["trade_delta_base"] == 0
            and last["trade_imbalance"] == 0
        )
    replays_ok = (
        first == second
        and first["status"] == "PASS"
        and first["read_only"]
        and first["input_unchanged"]
        and first["replay_matches_original"]
        and before == after
    )
    complete = (
        result["stop_reason"] == "OBSERVATION_COMPLETE"
        and result["messages"] == transport.sent == args.seconds * args.rate
        and counted.get("RAW") == transport.sent == parser_calls
        and len(first["normalized"]) == transport.sent
        and not first["quarantine"]
        and counted.get("FEATURE_CUTOFF") == counted.get("FEATURE_PUBLISHED") == args.seconds
        and len(first["features"]) == args.seconds
        and checked["pass"]
        and fixed is not False
        and replays_ok
        and not writers[0].process.is_alive()
    )
    if args.mode == "wall":
        complete = complete and result["observation_duration_ns"] >= args.seconds * NS
    files = [p for p in root.rglob("*") if p.is_file()]
    journal_counts = {
        name: len(list((root / "journal" / name).glob("*.jnl")))
        for name in ("raw", "control", "feature")
    }
    writer = writers[0]
    summary = {
        "schema_version": "r204-journal-600s-1",
        "acceptance": "PASS_LIMITED_OFFLINE" if complete else "PARTIAL",
        "REAL": "BLOCKED",
        "mode": args.mode,
        "target_seconds": args.seconds,
        "target_rate": args.rate,
        "generated": transport.sent,
        "run_result": result,
        "counts": counted,
        "parser_after_ack_checks": parser_calls,
        "normalized": len(first["normalized"]),
        "quarantine": len(first["quarantine"]),
        "features": len(first["features"]),
        "oracle": checked,
        "fixed_final_oracle": fixed,
        "final_feature": first["features"][-1] if first["features"] else None,
        "recovery_status": recovered["status"],
        "recovery_events": recovered["recovery_events"],
        "two_replays": replays_ok,
        "input_sha256_before": before,
        "input_sha256_after": after,
        "logical_sha256": first.get("logical_sha256"),
        "replay_statuses": [first["status"], second["status"]],
        "journal_files": journal_counts,
        "rolls": {k: max(0, v - 1) for k, v in journal_counts.items()},
        "default_roll_bytes": 4 * 1024 * 1024,
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "state_peaks": state_peaks,
        "queue_records_peak": writer.queue_records_peak,
        "queue_bytes_peak": writer.queue_bytes_peak,
        "ack_seconds": distribution(writer.command_samples),
        "delivery_lateness_seconds": distribution(transport.delivery_lateness),
        "fsync_distribution_status": "COUNT_P95_MAX_FROM_WORKER; P50_P99_NOT_INSTRUMENTED",
        "controller_wall_seconds": (finished - began) / NS,
        "parent_cpu_seconds": parent_cpu,
        "child_cpu_seconds": child_cpu,
        "controller_start_monotonic_ns": began,
        "controller_finish_monotonic_ns": finished,
        "phase_seconds": {
            "prepare": None
            if result["observation_start_monotonic_ns"] is None or not clock.real
            else (result["observation_start_monotonic_ns"] - began) / NS,
            "observation": result["observation_duration_ns"] / NS
            if result["observation_duration_ns"] is not None
            else None,
            "drain_and_derived": None
            if writer.close_began is None or not clock.real or transport.closed_at is None
            else (writer.close_began - transport.closed_at) / NS,
            "writer_close": None
            if writer.close_done is None or writer.close_began is None
            else (writer.close_done - writer.close_began) / NS,
        },
        "package_origin": importlib.util.find_spec("btc_microstructure").origin,
        "python": sys.version,
        "network": "INHERITED_AUDIT_GUARD_INET_DNS_DENIED",
    }
    (args.output / "summary.json").write_text(json.dumps(summary, default=str, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "acceptance",
                    "mode",
                    "generated",
                    "features",
                    "oracle",
                    "two_replays",
                    "controller_wall_seconds",
                )
            },
            default=str,
        ),
        flush=True,
    )
    return 0 if complete else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["simulated", "wall"], required=True)
    parser.add_argument("--seconds", type=int, choices=[30, 60, 120, 600], required=True)
    parser.add_argument("--rate", type=int, choices=[1, 5, 10], required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(scenario(parser.parse_args()))
