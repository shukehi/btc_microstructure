"""Socket-denied one-scenario benchmark for legacy versus journal prototype."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import resource
import socket
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))


def _deny_network(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("journal benchmark forbids network access")


socket.socket.connect = _deny_network
socket.socket.connect_ex = _deny_network
socket.create_connection = _deny_network
socket.getaddrinfo = _deny_network

from bounded_fakes import agg_trade, metadata_body  # noqa: E402
from test_bounded_live import make_test_only_approval  # noqa: E402

from btc_microstructure import bounded_live  # noqa: E402
from btc_microstructure.bounded_transport import MetadataReply, WsEvent  # noqa: E402
from btc_microstructure.bounded_writer import ProcessWriter  # noqa: E402
from btc_microstructure.journal_prototype import (  # noqa: E402
    TestOnlyJournalProcessWriter,
    replay_test_only_storage,
)
from btc_microstructure.offline_core import NS  # noqa: E402


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


class SimulatedClock:
    def __init__(self) -> None:
        self.value = 1_800_000_000_000_000_000

    def wall_ns(self) -> int:
        return self.value

    def monotonic_ns(self) -> int:
        return self.value

    def advance(self, amount: int) -> None:
        self.value += amount


class UniformTransport:
    TEST_ONLY = True

    def __init__(self, clock: SimulatedClock, *, seconds: int, rate: int = 10) -> None:
        self.clock = clock
        self.count = seconds * rate
        self.interval_ns = NS // rate
        self.sent = 0
        self.next_message_ns: int | None = None

    async def metadata(self) -> MetadataReply:
        return MetadataReply(200, {}, metadata_body())

    async def connect(self) -> None:
        self.next_message_ns = self.clock.monotonic_ns() + self.interval_ns

    async def poll(self, timeout_ns: int) -> WsEvent:
        assert self.next_message_ns is not None
        deadline = self.clock.monotonic_ns() + timeout_ns
        if self.sent >= self.count or self.next_message_ns > deadline:
            self.clock.advance(timeout_ns)
            return WsEvent("tick")
        self.clock.advance(self.next_message_ns - self.clock.monotonic_ns())
        self.sent += 1
        self.next_message_ns += self.interval_ns
        milliseconds = self.clock.wall_ns() // 1_000_000
        return WsEvent(
            "message",
            agg_trade(
                agg_id=self.sent,
                quantity="0.001",
                buyer_is_maker=bool(self.sent % 2),
                event_time=milliseconds,
                trade_time=milliseconds,
            ),
        )

    async def send_control(self, kind: str, payload: bytes = b"") -> None:
        return None

    async def close(self) -> None:
        return None


class ObservedLegacyWriter(ProcessWriter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._benchmark_ack: dict[str, list[float]] = defaultdict(list)

    def _command(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = str(request.get("operation", "unknown"))
        began = time.perf_counter()
        try:
            return super()._command(request)
        finally:
            self._benchmark_ack[operation].append(time.perf_counter() - began)

    @property
    def metrics(self) -> dict[str, Any]:
        value = super().metrics
        all_values = [item for values in self._benchmark_ack.values() for item in values]
        value.update(
            {
                "ack_count": len(all_values),
                "ack_p95_seconds": _percentile(all_values, 0.95),
                "ack_max_seconds": max(all_values, default=0.0),
                "ack_by_operation": {
                    name: {
                        "count": len(values),
                        "p95_seconds": _percentile(values, 0.95),
                        "total_seconds": sum(values),
                    }
                    for name, values in sorted(self._benchmark_ack.items())
                },
                "partition_bytes": dict(self.budget_used),
                "fsync_p95_seconds": None,
                "fsync_metric_status": "NOT_INSTRUMENTED_INCLUDED_IN_ACK",
            }
        )
        return value


def _tree_summary(root: Path) -> dict[str, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return {"files": len(files), "bytes": sum(path.stat().st_size for path in files)}


def _replay(root: Path) -> dict[str, Any]:
    began = time.perf_counter()
    cpu_began = time.process_time()
    value = replay_test_only_storage(root)
    return {
        "status": value["status"],
        "read_only": value["read_only"],
        "input_unchanged": value["input_unchanged"],
        "replay_matches_original": value["replay_matches_original"],
        "normalized": len(value["normalized"]),
        "quarantine": len(value["quarantine"]),
        "features": len(value["features"]),
        "logical_sha256": value.get("logical_sha256"),
        "elapsed_seconds": time.perf_counter() - began,
        "cpu_seconds": time.process_time() - cpu_began,
        "final_feature": value["features"][-1] if value["features"] else None,
    }


def _oracle(feature: dict[str, Any] | None, simulated_seconds: int) -> dict[str, Any]:
    if feature is None:
        return {"pass": False, "reason": "missing feature"}
    full_window = simulated_seconds > 30
    expected = {
        "agg_message_count": 300 if full_window else 299,
        "buy_volume_base": "0.150" if full_window else "0.149",
        "sell_volume_base": "0.150",
        "volume_base": "0.300" if full_window else "0.299",
        "trade_delta_base": "0.000" if full_window else "-0.001",
        "trade_imbalance": 0.0 if full_window else -1 / 299,
        "quality_flags": [],
    }
    actual = {key: str(feature[key]) if key.endswith("_base") else feature[key] for key in expected}
    return {"pass": actual == expected, "expected": expected, "actual": actual}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--writer", choices=["legacy", "journal"], required=True)
    parser.add_argument("--seconds", type=int, choices=[30, 60, 120], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output must not exist")
    args.output.mkdir(parents=True)
    run_root = args.output / "run"
    writer_class = ObservedLegacyWriter if args.writer == "legacy" else TestOnlyJournalProcessWriter
    bounded_live.ProcessWriter = writer_class
    clock = SimulatedClock()
    transport = UniformTransport(clock, seconds=args.seconds)
    config = bounded_live.BoundedLiveConfig(
        run_id=f"TEST_ONLY-journal-benchmark-{args.seconds}",
        output_root=run_root,
        observation_seconds=args.seconds,
        window_seconds=[30],
        unit_approval=make_test_only_approval(),
    )
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    began = time.perf_counter()
    cpu_began = time.process_time()
    result = asyncio.run(
        bounded_live.run_test_only_bounded_live(config, transport=transport, clock=clock)
    )
    elapsed = time.perf_counter() - began
    parent_cpu = time.process_time() - cpu_began
    child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    child_cpu = (
        child_after.ru_utime + child_after.ru_stime - child_before.ru_utime - child_before.ru_stime
    )
    first = _replay(run_root)
    second = _replay(run_root)
    source_paths = [
        ROOT / "src" / "btc_microstructure" / "bounded_live.py",
        ROOT / "src" / "btc_microstructure" / "bounded_writer.py",
        ROOT / "src" / "btc_microstructure" / "journal_prototype.py",
        Path(__file__),
    ]
    output = {
        "schema_version": "journal-benchmark-1",
        "writer": args.writer,
        "simulated_seconds": args.seconds,
        "rate_messages_per_second": 10,
        "network_access": "DISABLED_SOCKET_GUARD",
        "python": sys.version,
        "run_result": result,
        "generated_messages": transport.sent,
        "elapsed_seconds": elapsed,
        "parent_cpu_seconds": parent_cpu,
        "child_cpu_seconds": child_cpu,
        "parent_ru_maxrss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "tree": _tree_summary(run_root),
        "replays": [first, second],
        "replays_equal": all(
            first[key] == second[key]
            for key in (
                "status",
                "input_unchanged",
                "replay_matches_original",
                "normalized",
                "quarantine",
                "features",
                "logical_sha256",
                "final_feature",
            )
        ),
        "oracle": _oracle(first["final_feature"], args.seconds),
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_paths
        },
    }
    (args.output / "result.json").write_text(json.dumps(output, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {
                "writer": args.writer,
                "seconds": args.seconds,
                "elapsed_seconds": elapsed,
                "messages": result["messages"],
                "features": result["feature_rows"],
                "files": output["tree"]["files"],
                "replay": first["status"],
                "oracle": output["oracle"]["pass"],
                "output": str(args.output / "result.json"),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
