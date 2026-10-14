"""Socket-denied, streaming TEST_ONLY pressure using physical clocks and writer."""

from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
from pathlib import Path
import resource
import socket
import sys
import threading
import time
from typing import Any

sys.path.insert(0, str(Path("tests/unit").resolve()))


def denied(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("offline physical pressure: network forbidden")


socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
socket.getaddrinfo = denied

from bounded_fakes import agg_trade, metadata_body  # noqa: E402
from test_bounded_live import make_test_only_approval  # noqa: E402
from btc_microstructure import bounded_live  # noqa: E402
from btc_microstructure.bounded_transport import MetadataReply, WsEvent  # noqa: E402
from btc_microstructure.bounded_writer import ProcessWriter  # noqa: E402


class PhysicalClock:
    def wall_ns(self) -> int:
        return time.time_ns()

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class StreamingTransport:
    TEST_ONLY = True

    def __init__(self, count: int) -> None:
        self.count = count
        self.sent = 0
        self.observation_start: float | None = None
        self.last_message: float | None = None
        self.control_count = 0

    async def metadata(self) -> MetadataReply:
        return MetadataReply(200, {}, metadata_body())

    async def connect(self) -> None:
        self.observation_start = time.perf_counter()

    async def poll(self, timeout_ns: int) -> WsEvent:
        if self.sent >= self.count:
            # Ensure the small sample also crosses one physical feature cutoff.
            assert self.observation_start is not None
            if time.perf_counter() - self.observation_start < 1.1:
                await asyncio.sleep(min(timeout_ns / 1e9, 0.05))
                return WsEvent("tick")
            return WsEvent("disconnect")
        self.sent += 1
        self.last_message = time.perf_counter()
        return WsEvent(
            "message",
            agg_trade(
                agg_id=self.sent,
                quantity="0.001",
                buyer_is_maker=bool(self.sent % 2),
                event_time=time.time_ns() // 1_000_000,
                trade_time=time.time_ns() // 1_000_000,
            ),
        )

    async def send_control(self, kind: str, payload: bytes = b"") -> None:
        self.control_count += 1

    async def close(self) -> None:
        pass


class ObservedWriter(ProcessWriter):
    instance: ObservedWriter | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        ObservedWriter.instance = self
        self.queue_peak_records = 0
        self.queue_peak_bytes = 0
        self.queue_peak_age_ns = 0
        self.pressure_file_peak = 0
        self.pressure_samples = 0
        self.pressure_stop = threading.Event()
        self.pressure_thread = threading.Thread(target=self.sample, daemon=True)
        self.pressure_thread.start()

    def append(self, row: dict[str, Any]) -> None:
        super().append(row)
        self.queue_peak_records = max(self.queue_peak_records, len(self.rows))
        self.queue_peak_bytes = max(self.queue_peak_bytes, self.encoded_bytes)
        if self.rows:
            age = max(0, time.monotonic_ns() - self.rows[0]["receive_monotonic_ns"])
            self.queue_peak_age_ns = max(self.queue_peak_age_ns, age)

    def sample(self) -> None:
        while not self.pressure_stop.wait(0.1):
            self.pressure_samples += 1
            try:
                size = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
                self.pressure_file_peak = max(self.pressure_file_peak, size)
            except FileNotFoundError:
                pass  # atomic renames; final audited size also recorded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, choices=[1000, 100000], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bounded_live.ProcessWriter = ObservedWriter
    config = bounded_live.BoundedLiveConfig(
        run_id=f"TEST_ONLY-physical-{args.count}",
        output_root=args.output / "run",
        window_seconds=[1],
        observation_seconds=600,
        unit_approval=make_test_only_approval(),
    )
    transport = StreamingTransport(args.count)
    source_paths = [
        Path("src/btc_microstructure/bounded_live.py"),
        Path("src/btc_microstructure/bounded_transport.py"),
        Path("src/btc_microstructure/bounded_writer.py"),
        Path(__file__),
    ]
    source_before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    began, cpu = time.perf_counter(), time.process_time()
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    result = asyncio.run(
        bounded_live.run_test_only_bounded_live(
            config,
            transport=transport,
            clock=PhysicalClock(),
        )
    )
    ended = time.perf_counter()
    writer = ObservedWriter.instance
    if writer is not None:
        writer.pressure_stop.set()
        writer.pressure_thread.join(timeout=0.5)
    child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    result.update(
        {
            "fixture": "TEST_ONLY streaming generated aggTrade, no preallocated input list",
            "tested_source_sha256": source_before,
            "source_unchanged_during_run": source_before
            == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
            "requested_source_messages": args.count,
            "generated_source_messages": transport.sent,
            "physical_elapsed_seconds_including_startup_cleanup": ended - began,
            "physical_observation_seconds_including_cleanup": None
            if transport.observation_start is None
            else ended - transport.observation_start,
            "physical_input_delivery_seconds": None
            if transport.last_message is None or transport.observation_start is None
            else transport.last_message - transport.observation_start,
            "parent_cpu_process_seconds": time.process_time() - cpu,
            "child_cpu_seconds": child_after.ru_utime
            + child_after.ru_stime
            - child_before.ru_utime
            - child_before.ru_stime,
            "one_second_1000_delivery": args.count == 1000
            and result["messages"] == 1000
            and transport.last_message is not None
            and transport.observation_start is not None
            and transport.last_message - transport.observation_start <= 1,
            "physical_clock": "time.time_ns/time.monotonic_ns/perf_counter/process_time",
            "network_guard": "connect/connect_ex/create_connection/getaddrinfo denied in parent and spawned writer",
            "files_final_bytes": sum(
                p.stat().st_size for p in config.output_root.rglob("*") if p.is_file()
            ),
            "files_final_count": sum(1 for p in config.output_root.rglob("*") if p.is_file()),
            "queue_peak_records": None if writer is None else writer.queue_peak_records,
            "queue_peak_bytes": None if writer is None else writer.queue_peak_bytes,
            "queue_peak_age_ns": None if writer is None else writer.queue_peak_age_ns,
            "file_growth_peak_bytes": None if writer is None else writer.pressure_file_peak,
            "file_growth_samples_100ms": None if writer is None else writer.pressure_samples,
            "writer_metrics": None if writer is None else getattr(writer, "metrics", {}),
        }
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
