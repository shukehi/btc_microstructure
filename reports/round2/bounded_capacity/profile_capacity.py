"""Bounded, socket-denied L04 state/RSS/CPU/writer profiler.

This script observes the existing controller through subclasses and produces a
small JSON result.  It does not change limits, create a network transport, or
retain generated raw data outside the explicitly supplied output directory.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from collections.abc import Iterable
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import socket
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))


def _deny_network(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("L04 capacity profiler forbids network access")


socket.socket.connect = _deny_network
socket.socket.connect_ex = _deny_network
socket.create_connection = _deny_network
socket.getaddrinfo = _deny_network

from bounded_fakes import agg_trade, metadata_body  # noqa: E402
from test_bounded_live import make_test_only_approval  # noqa: E402

from btc_microstructure import bounded_live  # noqa: E402
from btc_microstructure.bounded_replay import replay_bounded  # noqa: E402
from btc_microstructure.bounded_transport import MetadataReply, WsEvent  # noqa: E402
from btc_microstructure.bounded_writer import ProcessWriter  # noqa: E402
from btc_microstructure.offline_core import Core, NS  # noqa: E402


Json = dict[str, Any]


def _deep_size(value: Any, seen: set[int] | None = None) -> int:
    """Reachable Python allocation size with shared identities counted once."""

    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    total = sys.getsizeof(value)
    if isinstance(value, dict):
        total += sum(_deep_size(key, seen) + _deep_size(item, seen) for key, item in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        total += sum(_deep_size(item, seen) for item in value)
    return total


def _core_memory(core: Core) -> Json:
    groups: list[tuple[str, Any]] = [
        ("history", core.history),
        ("normalized", core.normalized),
        ("seen_index", core.seen),
        (
            "window_and_features",
            {
                "windows": core.windows,
                "features": core.features,
                "pending": core.pending,
            },
        ),
        (
            "quality",
            {
                "quarantine": core.quarantine,
                "observations": core.quality_observations,
                "gaps": core.gaps,
                "errors": core.errors,
                "conflicts": core.conflicts,
            },
        ),
        (
            "base_and_scalars",
            {
                "config": core.config,
                "metadata": core.metadata,
                "unit_policy": core.unit_policy,
                "start": core.start,
                "last_time": core.last_time,
                "cursor": core.cursor,
                "session": core.session,
                "last_cutoff": core.last_cutoff,
            },
        ),
    ]
    shared_seen: set[int] = set()
    exclusive = {name: _deep_size(value, shared_seen) for name, value in groups}
    standalone = {name: _deep_size(value) for name, value in groups}
    normalized_ids = {id(item) for item in core.normalized}
    seen_value_ids = {id(item) for item in core.seen.values()}
    return {
        "exclusive_bytes": exclusive,
        "exclusive_total_bytes": sum(exclusive.values()),
        "standalone_bytes": standalone,
        "normalized_seen_shared_values": len(normalized_ids & seen_value_ids),
        "counts": {
            "history": len(core.history),
            "normalized": len(core.normalized),
            "seen": len(core.seen),
            "quarantine": len(core.quarantine),
            "features": len(core.features),
            "pending_cutoffs": len(core.pending),
            "pending_features": sum(len(rows) for rows in core.pending.values()),
            "quality_observations": len(core.quality_observations),
            "gaps": len(core.gaps),
            "errors": len(core.errors),
            "conflicts": len(core.conflicts),
        },
    }


def _tree_bytes(root: Path) -> int:
    try:
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    except FileNotFoundError:
        return 0


def _rss_bytes(pid: int | None) -> int | None:
    if not pid:
        return None
    completed = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    value = completed.stdout.strip()
    return int(value) * 1024 if value else None


def _percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


class Tracker:
    def __init__(self, checkpoints: set[int], output_root: Path) -> None:
        self.checkpoints = checkpoints
        self.output_root = output_root
        self.core: Core | None = None
        self.writer: ObservedWriter | None = None
        self.transport: BaseTransport | None = None
        self.reservations: ObservedReservations | None = None
        self.processed_raw = 0
        self.last_pending_bytes = 0
        self.snapshots: list[Json] = []
        self.first_failure: Json | None = None
        self.charge_totals: dict[str, Json] = defaultdict(
            lambda: {
                "count": 0,
                "persistent_bytes": 0,
                "scratch_bytes": 0,
                "persistent_max_bytes": 0,
                "scratch_max_bytes": 0,
            }
        )
        self.core_process_seconds: dict[str, list[float]] = defaultdict(list)
        self.window_seconds: list[float] = []

    def snapshot(self, label: str) -> Json:
        assert self.core is not None
        writer = self.writer
        transport = self.transport
        reservations = self.reservations
        result = {
            "label": label,
            "generated_messages": None if transport is None else transport.sent,
            "processed_raw": self.processed_raw,
            "pending_raw_records": None
            if transport is None
            else max(0, transport.sent - self.processed_raw),
            "pending_raw_reserved_bytes": self.last_pending_bytes,
            "state_committed_bytes": None if reservations is None else reservations.committed_bytes,
            "core_memory": _core_memory(self.core),
            "parent_current_rss_bytes": _rss_bytes(os.getpid()),
            "child_current_rss_bytes": None if writer is None else _rss_bytes(writer.process.pid),
            "parent_cpu_seconds": time.process_time(),
            "files_bytes": _tree_bytes(self.output_root),
        }
        self.snapshots.append(result)
        return result


TRACKER: Tracker


class ObservedCore(Core):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        TRACKER.core = self

    def process(self, row: Json) -> None:
        began = time.perf_counter()
        super().process(row)
        TRACKER.core_process_seconds[row["kind"]].append(time.perf_counter() - began)

    def _window(self, cutoff: int, high: int, window: int) -> Json:
        began = time.perf_counter()
        result = super()._window(cutoff, high, window)
        TRACKER.window_seconds.append(time.perf_counter() - began)
        return result


class ObservedReservations(bounded_live.StateReservations):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._charge_kinds: dict[int, str] = {}
        TRACKER.reservations = self

    def reserve_core_row(self, row: Json, core: Core) -> bounded_live.StateCharge:
        charge = super().reserve_core_row(row, core)
        self._charge_kinds[id(charge)] = str(row["kind"])
        return charge

    def check(self, **kwargs: Any) -> tuple[int, int]:
        reserve = kwargs.get("reserve_bytes", 0)
        charge = (
            reserve
            if isinstance(reserve, bounded_live.StateCharge)
            else bounded_live.StateCharge(int(reserve))
        )
        operation = self._charge_kinds.get(id(reserve), "CHECK_OR_RAW_ENVELOPE")
        pending = int(kwargs["pending_bytes"])
        TRACKER.last_pending_bytes = pending
        predicted = self.committed_bytes + 2 * pending + charge.total
        try:
            return super().check(**kwargs)
        except bounded_live.StopRun as exc:
            if TRACKER.first_failure is None:
                core = kwargs["core"]
                TRACKER.first_failure = {
                    "reason": exc.reason,
                    "operation": operation,
                    "sequence": kwargs["sequence"],
                    "committed_bytes_before": self.committed_bytes,
                    "pending_bytes": pending,
                    "pending_multiplier_bytes": 2 * pending,
                    "charge": {
                        "persistent_bytes": charge.persistent_bytes,
                        "scratch_bytes": charge.scratch_bytes,
                        "total_bytes": charge.total,
                    },
                    "predicted_bytes": predicted,
                    "limit_bytes": self.config.max_state_bytes,
                    "over_by_bytes": predicted - self.config.max_state_bytes,
                    "core_memory": _core_memory(core),
                    "parent_current_rss_bytes": _rss_bytes(os.getpid()),
                    "child_current_rss_bytes": None
                    if TRACKER.writer is None
                    else _rss_bytes(TRACKER.writer.process.pid),
                    "files_bytes": _tree_bytes(TRACKER.output_root),
                }
            raise

    def commit(self, reserve_bytes: int | bounded_live.StateCharge) -> None:
        kind = self._charge_kinds.pop(id(reserve_bytes), "UNKNOWN")
        charge = (
            reserve_bytes
            if isinstance(reserve_bytes, bounded_live.StateCharge)
            else bounded_live.StateCharge(int(reserve_bytes))
        )
        stats = TRACKER.charge_totals[kind]
        stats["count"] += 1
        stats["persistent_bytes"] += charge.persistent_bytes
        stats["scratch_bytes"] += charge.scratch_bytes
        stats["persistent_max_bytes"] = max(stats["persistent_max_bytes"], charge.persistent_bytes)
        stats["scratch_max_bytes"] = max(stats["scratch_max_bytes"], charge.scratch_bytes)
        super().commit(reserve_bytes)
        if kind == "RAW":
            TRACKER.processed_raw += 1
            if TRACKER.processed_raw in TRACKER.checkpoints:
                TRACKER.snapshot(f"processed_raw_{TRACKER.processed_raw}")


class ObservedWriter(ProcessWriter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.command_seconds: dict[str, list[float]] = defaultdict(list)
        TRACKER.writer = self

    def _command(self, request: Json) -> Json:
        operation = str(request.get("operation", "unknown"))
        began = time.perf_counter()
        try:
            return super()._command(request)
        finally:
            self.command_seconds[operation].append(time.perf_counter() - began)

    def timing_summary(self) -> Json:
        return {
            operation: {
                "count": len(values),
                "total_seconds": sum(values),
                "p95_seconds": _percentile(values, 0.95),
                "max_seconds": max(values),
            }
            for operation, values in sorted(self.command_seconds.items())
            if values
        }


class SimulatedClock:
    def __init__(self) -> None:
        self.value = 1_800_000_000_000_000_000

    def wall_ns(self) -> int:
        return self.value

    def monotonic_ns(self) -> int:
        return self.value

    def advance(self, amount: int) -> None:
        self.value += amount


class PhysicalClock:
    def wall_ns(self) -> int:
        return time.time_ns()

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class BaseTransport:
    TEST_ONLY = True

    def __init__(self, count: int, rate: float) -> None:
        self.count = count
        self.rate = rate
        self.sent = 0
        self.observation_start_ns: int | None = None

    async def metadata(self) -> MetadataReply:
        return MetadataReply(200, {}, metadata_body())

    async def send_control(self, kind: str, payload: bytes = b"") -> None:
        return None

    async def close(self) -> None:
        return None


class SimulatedTransport(BaseTransport):
    def __init__(self, clock: SimulatedClock, count: int, rate: float) -> None:
        super().__init__(count, rate)
        self.clock = clock
        self.interval_ns = max(1, round(NS / rate))
        self.next_message_ns: int | None = None

    async def connect(self) -> None:
        self.observation_start_ns = self.clock.monotonic_ns()
        self.next_message_ns = self.observation_start_ns + self.interval_ns

    async def poll(self, timeout_ns: int) -> WsEvent:
        assert self.next_message_ns is not None
        now = self.clock.monotonic_ns()
        if self.sent < self.count and self.next_message_ns <= now + timeout_ns:
            self.clock.advance(max(0, self.next_message_ns - now))
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
        self.clock.advance(timeout_ns)
        return WsEvent("tick")


class PhysicalTransport(BaseTransport):
    def __init__(self, count: int, rate: float) -> None:
        super().__init__(count, rate)
        self.interval = 1.0 / rate
        self.next_message: float | None = None

    async def connect(self) -> None:
        self.observation_start_ns = time.monotonic_ns()
        self.next_message = time.monotonic()

    async def poll(self, timeout_ns: int) -> WsEvent:
        assert self.next_message is not None
        now = time.monotonic()
        if self.sent < self.count and self.next_message <= now + timeout_ns / NS:
            await asyncio.sleep(max(0.0, self.next_message - now))
            self.sent += 1
            self.next_message += self.interval
            milliseconds = time.time_ns() // 1_000_000
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
        await asyncio.sleep(timeout_ns / NS)
        return WsEvent("tick")


def _timing(values: list[float]) -> Json:
    return {
        "count": len(values),
        "total_seconds": sum(values),
        "p95_seconds": _percentile(values, 0.95),
        "max_seconds": max(values) if values else None,
    }


def _replay_summary(root: Path) -> Json:
    began = time.perf_counter()
    cpu_began = time.process_time()
    replay = replay_bounded(root)
    return {
        "status": replay["status"],
        "read_only": replay["read_only"],
        "input_unchanged": replay["input_unchanged"],
        "replay_matches_original": replay["replay_matches_original"],
        "normalized": len(replay["normalized"]),
        "quarantine": len(replay["quarantine"]),
        "features": len(replay["features"]),
        "elapsed_seconds": time.perf_counter() - began,
        "cpu_seconds": time.process_time() - cpu_began,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["simulated", "wall"], default="simulated")
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--observation-seconds", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replays", type=int, default=2, choices=[0, 1, 2])
    parser.add_argument("--checkpoints", default="100,500,1000,2000")
    args = parser.parse_args()
    if not 1 <= args.count <= 100_000:
        parser.error("--count must be in [1, 100000]")
    if not 0 < args.rate <= 1000:
        parser.error("--rate must be in (0, 1000]")
    if args.output.exists():
        parser.error("--output must not exist")
    args.output.mkdir(parents=True)

    global TRACKER
    checkpoints = {int(value) for value in args.checkpoints.split(",") if value}
    run_root = args.output / "run"
    TRACKER = Tracker(checkpoints, run_root)
    bounded_live.Core = ObservedCore
    bounded_live.StateReservations = ObservedReservations
    bounded_live.ProcessWriter = ObservedWriter
    if args.mode == "simulated":
        clock: SimulatedClock | PhysicalClock = SimulatedClock()
        transport: BaseTransport = SimulatedTransport(clock, args.count, args.rate)
    else:
        clock = PhysicalClock()
        transport = PhysicalTransport(args.count, args.rate)
    TRACKER.transport = transport

    source_paths = [
        ROOT / "src" / "btc_microstructure" / "bounded_live.py",
        ROOT / "src" / "btc_microstructure" / "bounded_writer.py",
        ROOT / "src" / "btc_microstructure" / "offline_core.py",
        Path(__file__),
    ]
    source_hashes = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_paths
    }
    config = bounded_live.BoundedLiveConfig(
        run_id=f"TEST_ONLY-capacity-{args.mode}-{args.count}-{args.rate:g}",
        output_root=run_root,
        window_seconds=[30],
        observation_seconds=args.observation_seconds,
        unit_approval=make_test_only_approval(),
    )
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    began = time.perf_counter()
    cpu_began = time.process_time()
    result = asyncio.run(
        bounded_live.run_test_only_bounded_live(
            config,
            transport=transport,
            clock=clock,
        )
    )
    elapsed = time.perf_counter() - began
    cpu = time.process_time() - cpu_began
    child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    child_cpu = (
        child_after.ru_utime + child_after.ru_stime - child_before.ru_utime - child_before.ru_stime
    )
    writer = TRACKER.writer
    if TRACKER.core is not None:
        TRACKER.snapshot("final")
    replay_results = []
    for _ in range(args.replays):
        replay_results.append(_replay_summary(run_root))
        gc.collect()
    output = {
        "schema_version": "l04-capacity-profile-1",
        "mode": args.mode,
        "fixture": "uniform artificial unique BTCUSDT aggTrade",
        "network_access": "DISABLED_SOCKET_GUARD",
        "python": sys.version,
        "requested_messages": args.count,
        "rate_messages_per_event_second": args.rate,
        "observation_seconds": args.observation_seconds,
        "run_result": result,
        "generated_messages": transport.sent,
        "processed_raw": TRACKER.processed_raw,
        "elapsed_seconds": elapsed,
        "parent_cpu_seconds": cpu,
        "child_cpu_seconds": child_cpu,
        "files_final_bytes": _tree_bytes(run_root),
        "state_snapshots": TRACKER.snapshots,
        "first_failure": TRACKER.first_failure,
        "reservation_totals_by_operation": dict(TRACKER.charge_totals),
        "core_process_timing": {
            kind: _timing(values) for kind, values in sorted(TRACKER.core_process_seconds.items())
        },
        "window_scan_timing": _timing(TRACKER.window_seconds),
        "writer_command_timing": {} if writer is None else writer.timing_summary(),
        "replays": replay_results,
        "source_sha256": source_hashes,
        "source_unchanged": source_hashes
        == {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_paths
        },
    }
    (args.output / "profile.json").write_text(json.dumps(output, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "stop_reason": result["stop_reason"],
                "generated_messages": transport.sent,
                "processed_raw": TRACKER.processed_raw,
                "elapsed_seconds": elapsed,
                "output": str(args.output / "profile.json"),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
