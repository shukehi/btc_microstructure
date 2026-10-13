"""R2-04 single-source bounded aggTrade controller for offline validation only."""

from __future__ import annotations

import base64
import asyncio
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import resource
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .bounded_transport import (
    BoundedTransport,
    MetadataReply,
    TransportScopeError,
    payload_for_core,
)
from .bounded_writer import ProcessWriter, WriterFailure
from .offline import INSTRUMENT_ID, MARKET_TYPE, SOURCE_STREAM, VENUE
from .offline_core import ADAPTER_VERSION, NS, SCHEMA_VERSION, Core
from .offline_storage import _json_line
from .probe import ProbeError, inspect_metadata, object_json
from .unit_binding import STREAM as BINDING_STREAM
from .unit_binding import UnitBinding, load_unit_evidence, resolve_test_only_unit_binding

JsonRow = dict[str, Any]


class Clock(Protocol):
    def wall_ns(self) -> int: ...
    def monotonic_ns(self) -> int: ...


class DurableWriter(Protocol):
    cursor: int | None

    def append(self, row: JsonRow) -> None: ...
    def flush(self) -> JsonRow | None: ...
    def write_snapshot(self, relative_path: str, payload: bytes) -> None: ...
    def append_durable(self, row: JsonRow) -> None: ...
    def write_table(self, name: str, rows: list[JsonRow]) -> JsonRow: ...
    def close(self) -> None: ...


class BoundedLiveConfig(BaseModel):
    """Strict TEST_ONLY config for fake bounded aggTrade sessions."""

    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    run_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    output_root: Path
    grid_seconds: int = Field(default=1, ge=1, le=60)
    window_seconds: list[int] = Field(default_factory=lambda: [30])
    startup_seconds: int = Field(default=30, ge=1, le=30)
    observation_seconds: int = Field(default=600, ge=1, le=600)
    shutdown_seconds: int = Field(default=30, ge=1, le=30)
    market_silence_seconds: int = Field(default=60, ge=1, le=60)
    max_messages: int = Field(default=100_000, ge=1, le=100_000)
    max_ws_payload_bytes: int = Field(default=33_554_432, ge=1, le=33_554_432)
    max_message_bytes: int = Field(default=65_536, ge=1, le=65_536)
    max_queue_records: int = Field(default=1000, ge=1, le=1000)
    max_queue_bytes: int = Field(default=1_048_576, ge=1, le=1_048_576)
    max_queue_age_ns: int = Field(default=NS, ge=0, le=NS)
    max_outbound_control_total: int = Field(default=64, ge=1, le=64)
    max_outbound_control_per_second: int = Field(default=2, ge=1, le=2)
    max_outbound_control_payload_bytes: int = Field(default=125, ge=0, le=125)
    max_inbound_control_total: int = Field(default=64, ge=0, le=64)
    max_inbound_control_payload_bytes: int = Field(default=8192, ge=0, le=8192)
    max_run_bytes: int = Field(default=536_870_912, ge=1, le=536_870_912)
    max_total_inputs: int = Field(default=104_096, ge=1, le=104_096)
    max_cutoffs: int = Field(default=600, ge=1, le=600)
    max_feature_rows: int = Field(default=3600, ge=1, le=3600)
    rss_stop_bytes: int = Field(default=536_870_912, ge=1, le=536_870_912)
    max_state_bytes: int = Field(default=134_217_728, ge=1, le=134_217_728)
    min_free_bytes: int = Field(default=2_684_354_560, ge=0)
    approval_mode: Literal["TEST_ONLY"] = "TEST_ONLY"
    network_authorized: bool = False
    shared_budget_trusted: bool = False
    unit_evidence_path: Path = Path(
        "reports/round2/units_and_collection_plan/quantity_unit_evidence.json"
    )
    unit_approval: dict[str, Any]

    @field_validator("window_seconds")
    @classmethod
    def _validate_windows(cls, value: list[int]) -> list[int]:
        if not value or any(item <= 0 for item in value):
            raise ValueError("window_seconds: positive integers required")
        if len(value) > 6:
            raise ValueError("window_seconds: at most 6 windows are allowed")
        if len(value) != len(set(value)):
            raise ValueError("window_seconds: duplicate values are not allowed")
        return value

    def core_config(self) -> JsonRow:
        return {
            "time_view": "as_received",
            "grid_seconds": self.grid_seconds,
            "window_seconds": list(self.window_seconds),
            "horizon_seconds": [],
            "run_id": self.run_id,
            "limits": {
                "max_events": self.max_messages,
                "max_output_grid_rows": self.observation_seconds * len(self.window_seconds),
                "min_free_bytes": self.min_free_bytes,
            },
        }


@dataclass
class ResourceLedger:
    config: BoundedLiveConfig
    messages: int = 0
    ws_payload_bytes: int = 0
    inbound_control: int = 0
    inbound_control_bytes: int = 0
    outbound_normal_times: list[int] = field(default_factory=list)
    close_reserved: bool = False
    outbound_control_bytes: int = 0

    def observe_message(self, size: int) -> None:
        if size > self.config.max_message_bytes:
            raise StopRun("MESSAGE_BYTE_LIMIT")
        if self.messages + 1 > self.config.max_messages:
            raise StopRun("MESSAGE_COUNT_LIMIT")
        if self.ws_payload_bytes + size > self.config.max_ws_payload_bytes:
            raise StopRun("WS_PAYLOAD_BYTE_LIMIT")
        self.messages += 1
        self.ws_payload_bytes += size

    def observe_inbound_control(self, size: int) -> None:
        if self.inbound_control + 1 > self.config.max_inbound_control_total:
            raise StopRun("INBOUND_CONTROL_LIMIT")
        if self.inbound_control_bytes + size > self.config.max_inbound_control_payload_bytes:
            raise StopRun("INBOUND_CONTROL_BYTE_LIMIT")
        self.inbound_control += 1
        self.inbound_control_bytes += size

    def reserve_outbound_control(
        self, now_ns: int, size: int, *, kind: Literal["normal", "close"] = "normal"
    ) -> None:
        if size > self.config.max_outbound_control_payload_bytes:
            raise StopRun("OUTBOUND_CONTROL_PAYLOAD_LIMIT")
        if kind == "close":
            if self.close_reserved:
                raise StopRun("OUTBOUND_CLOSE_ALREADY_USED")
            if len(self.outbound_normal_times) + 1 > self.config.max_outbound_control_total:
                raise StopRun("OUTBOUND_CONTROL_TOTAL_LIMIT")
            self.close_reserved = True
            self.outbound_control_bytes += size
            return
        window_left = now_ns - NS
        recent = [stamp for stamp in self.outbound_normal_times if stamp > window_left]
        if len(self.outbound_normal_times) + 1 > self.config.max_outbound_control_total - 1:
            raise StopRun("OUTBOUND_CONTROL_TOTAL_LIMIT")
        if len(recent) + 1 > min(1, self.config.max_outbound_control_per_second):
            raise StopRun("OUTBOUND_CONTROL_RATE_LIMIT")
        self.outbound_normal_times.append(now_ns)
        self.outbound_control_bytes += size


class StopRun(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def fail_closed_real_entry(config: BoundedLiveConfig) -> None:
    if config.network_authorized or config.shared_budget_trusted:
        raise TransportScopeError("R2-04 real startup is blocked pending later approval")
    raise TransportScopeError("R2-04 real startup is blocked; offline TEST_ONLY path only")


async def run_test_only_bounded_live(
    config: BoundedLiveConfig,
    *,
    transport: BoundedTransport,
    clock: Clock,
    writer: DurableWriter | None = None,
) -> JsonRow:
    if config.network_authorized or config.shared_budget_trusted:
        fail_closed_real_entry(config)
    if getattr(transport, "TEST_ONLY", False) is not True:
        raise TransportScopeError("transport: explicit TEST_ONLY marker required")
    if not callable(getattr(transport, "poll", None)):
        raise TransportScopeError("transport: bounded poll protocol required")
    if config.output_root.exists():
        raise FileExistsError("output_root: refusing to overwrite previous run")
    config.output_root.mkdir(parents=True)
    durable_writer = writer
    core: Core | None = None
    pending: list[JsonRow] = []
    pending_encoded_bytes = 0
    state_cache: dict[str, int] = {}
    ledger = ResourceLedger(config)
    seq = _Sequencer()
    status, reason = "PARTIAL", "OBSERVATION_COMPLETE"
    connected = session_opened = cancelled = False
    observation_start: int | None = None
    observation_mono: int | None = None
    observation_end: int | None = None
    first_receive: int | None = None
    last_receive: int | None = None
    metadata_hash: str | None = None
    cutoffs = state_bytes = 0
    peak_rss = 0
    start = clock.monotonic_ns()
    startup_end = start + config.startup_seconds * NS
    global_end = (
        start + (config.startup_seconds + config.observation_seconds + config.shutdown_seconds) * NS
    )
    real_start = time.monotonic()
    real_global_end = real_start + (global_end - start) / NS
    real_startup_end = real_start + config.startup_seconds
    writer_real_end = real_startup_end

    async def call(
        operation: Callable[[], Awaitable[Any]],
        cap: float,
        name: str,
        phase_end: int | None = None,
        *,
        cleanup: bool = False,
    ) -> Any:
        now = clock.monotonic_ns()
        if not cleanup:
            _check_global_deadline(clock, global_end)
            if phase_end is not None and now >= phase_end:
                raise StopRun("STARTUP_TIME_LIMIT" if phase_end == startup_end else name)
        remaining = min(cap, real_global_end - time.monotonic())
        if phase_end == startup_end:
            remaining = min(remaining, real_startup_end - time.monotonic())
        if phase_end is not None:
            remaining = min(remaining, (phase_end - now) / NS)
        if not cleanup:
            remaining = min(remaining, (global_end - now) / NS)
            if phase_end is not None:
                remaining = min(remaining, (phase_end - now) / NS)
        if remaining <= 0:
            raise StopRun(name)
        began = now
        try:
            result = await asyncio.wait_for(operation(), remaining)
        except TimeoutError as exc:
            raise StopRun(name) from exc
        if not cleanup:
            _check_global_deadline(clock, global_end)
            if phase_end == startup_end:
                _check_startup_deadline(clock, startup_end)
        if name != "RECEIVE_TIME_LIMIT" and clock.monotonic_ns() - began >= int(cap * NS):
            raise StopRun(name)
        if cleanup and phase_end is not None and clock.monotonic_ns() > phase_end:
            raise StopRun(name)
        return result

    def writer_budget(phase_end: int | None = None) -> None:
        if isinstance(durable_writer, ProcessWriter):
            remaining = (global_end - clock.monotonic_ns()) / NS
            if phase_end is not None:
                remaining = min(remaining, (phase_end - clock.monotonic_ns()) / NS)
            durable_writer.deadline = min(
                real_global_end, writer_real_end, time.monotonic() + max(0.0, remaining)
            )

    def flush_raw() -> None:
        nonlocal pending_encoded_bytes
        if pending and durable_writer is not None and core is not None:
            writer_budget()
            _flush_pending_raw(durable_writer, core, pending)
            pending_encoded_bytes = 0

    def audit_control(kind: str, **fields: Any) -> None:
        if durable_writer is None or core is None:
            return
        flush_raw()
        writer_budget()
        row = _control_row(seq, clock, kind, session_id=config.run_id)
        row.update(fields)
        _append(durable_writer, core, row)

    try:
        if (
            config.min_free_bytes
            and shutil.disk_usage(config.output_root).free < config.min_free_bytes
        ):
            raise StopRun("DISK_START_LIMIT")
        evidence_bytes = config.unit_evidence_path.read_bytes()
        metadata = await call(transport.metadata, 15, "REST_TIME_LIMIT", startup_end)
        if len(metadata.body) > 2_097_152:
            raise StopRun("REST_BYTE_LIMIT")
        metadata_object = _require_metadata(metadata)
        metadata_hash = hashlib.sha256(metadata.body).hexdigest()
        binding = _resolve_binding(config, evidence_bytes, metadata_object)
        prepare_end = min(startup_end, clock.monotonic_ns() + 5 * NS)
        writer_real_end = min(real_startup_end, time.monotonic() + 5)
        if durable_writer is None:
            if (
                config.min_free_bytes
                and shutil.disk_usage(config.output_root).free < config.min_free_bytes
            ):
                raise StopRun("DISK_START_LIMIT")
            durable_writer = ProcessWriter(
                config.output_root,
                run_metadata={
                    "run_id": config.run_id,
                    "mode": "TEST_ONLY_BOUNDED_LIVE",
                    "instrument_id": INSTRUMENT_ID,
                },
                max_run_bytes=config.max_run_bytes,
                min_free_bytes=2_147_483_648 if config.min_free_bytes else 0,
                max_records=config.max_queue_records,
                max_bytes=config.max_queue_bytes,
                rss_stop_bytes=config.rss_stop_bytes,
            )
        writer_budget(prepare_end)
        _persist_snapshot(
            durable_writer,
            config=config,
            evidence_bytes=evidence_bytes,
            approval=config.unit_approval,
            metadata_reply=metadata,
        )
        if clock.monotonic_ns() > prepare_end:
            raise StopRun("PREPARE_TIME_LIMIT")
        core = Core(config.core_config(), dict(binding.core_metadata()), binding=binding)
        audit_control("REST_RESPONSE", http_status=metadata.status, body_sha256=metadata_hash)
        if clock.monotonic_ns() > prepare_end:
            raise StopRun("PREPARE_TIME_LIMIT")
        writer_real_end = real_startup_end
        await call(transport.connect, 10, "CONNECT_TIME_LIMIT", startup_end)
        connected = True
        audit_control("SESSION_OPEN")
        session_opened = True
        audit_control("SUBSCRIBED")
        _check_startup_deadline(clock, startup_end)
        observation_start = clock.wall_ns()
        observation_mono = clock.monotonic_ns()
        # Configured TEST_ONLY observation lengths advance on the injected clock.
        # A separate absolute real guard uses the 600s ceiling and shared global
        # deadline; virtual 1s burst fixtures are not wall-clock throughput claims.
        writer_real_end = min(time.monotonic() + 600, real_global_end - config.shutdown_seconds)
        observation_end = min(
            observation_mono + config.observation_seconds * NS,
            global_end - config.shutdown_seconds * NS,
        )
        silence_end = observation_mono + config.market_silence_seconds * NS
        grid = config.grid_seconds * NS
        next_cutoff = (observation_start // grid + 1) * grid
        while clock.monotonic_ns() < observation_end:
            if time.monotonic() >= writer_real_end:
                raise StopRun("OBSERVATION_TIME_LIMIT")
            _check_global_deadline(clock, global_end)
            state_bytes, rss = _check_state_budget(
                config, seq.value, core, durable_writer, state_cache, pending_encoded_bytes
            )
            peak_rss = max(peak_rss, rss)
            if clock.monotonic_ns() >= silence_end:
                raise StopRun("MARKET_SILENCE")
            receive_end = min(
                global_end, observation_end, silence_end, clock.monotonic_ns() + 100_000_000
            )
            event = await call(
                lambda: transport.poll(receive_end - clock.monotonic_ns()),
                (receive_end - clock.monotonic_ns()) / NS,
                "RECEIVE_TIME_LIMIT",
            )
            if clock.monotonic_ns() > receive_end:
                raise StopRun("RECEIVE_TIME_LIMIT")
            if clock.monotonic_ns() >= silence_end:
                raise StopRun("MARKET_SILENCE")
            now = clock.wall_ns()
            if event is not None and event.kind == "message":
                ledger.observe_message(len(event.payload))
                first_receive = now if first_receive is None else first_receive
                last_receive = now
                silence_end = clock.monotonic_ns() + config.market_silence_seconds * NS
                row = _raw_row(seq, clock, config.run_id, event.payload, binding)
                durable_writer.append(_stored_row(row))
                pending.append(row)
                pending_encoded_bytes += len(_json_line(row))
            elif event is not None and event.kind in {"ping", "pong", "close"}:
                ledger.observe_inbound_control(len(event.payload))
                audit_control(
                    "CONTROL_RECEIVED",
                    frame_kind=event.kind,
                    payload_base64=base64.b64encode(event.payload).decode(),
                    direction="inbound",
                )
                if event.kind == "close":
                    raise StopRun("REMOTE_CLOSE")
                if event.kind == "ping":
                    ledger.reserve_outbound_control(clock.monotonic_ns(), len(event.payload))
                    await call(
                        lambda: transport.send_control("pong", event.payload), 1, "SEND_TIME_LIMIT"
                    )
                    audit_control(
                        "CONTROL_SENT",
                        frame_kind="pong",
                        direction="outbound",
                        payload_base64=base64.b64encode(event.payload).decode(),
                    )
            elif event is not None and event.kind == "disconnect":
                raise StopRun("DISCONNECT")
            elif event is not None and event.kind != "tick":
                raise StopRun("UNKNOWN_CONTROL")
            # Shared time advancement: market messages and empty polls obey the same rules.
            if pending:
                oldest_age = clock.monotonic_ns() - pending[0]["receive_monotonic_ns"]
                if oldest_age >= config.max_queue_age_ns:
                    raise StopRun("QUEUE_AGE_LIMIT")
                if (
                    len(pending) >= 500
                    or sum(len(_json_line(_stored_row(r))) for r in pending) >= 524_288
                    or oldest_age >= 500_000_000
                ):
                    flush_raw()
            while clock.wall_ns() >= next_cutoff:
                if cutoffs >= config.max_cutoffs:
                    raise StopRun("CUTOFF_LIMIT")
                if (cutoffs + 1) * len(config.window_seconds) > config.max_feature_rows:
                    raise StopRun("FEATURE_ROW_LIMIT")
                flush_raw()
                assert durable_writer.cursor is not None
                cutoff = _cutoff_row(
                    seq,
                    clock,
                    config.run_id,
                    cutoff_ns=next_cutoff,
                    input_cursor=durable_writer.cursor,
                )
                writer_budget()
                _append(durable_writer, core, cutoff)
                state_bytes, rss = _check_state_budget(
                    config, seq.value, core, durable_writer, state_cache, pending_encoded_bytes
                )
                peak_rss = max(peak_rss, rss)
                receipt = durable_writer.write_table("features_pending", core.pending_features)
                done_ns = clock.wall_ns()
                published = _published_row(seq, clock, config.run_id, cutoff_ns=next_cutoff)
                published["compute_done_observed_ns"] = done_ns
                published["feature_manifest"] = receipt.get("manifest")
                _append(durable_writer, core, published)
                cutoffs += 1
                next_cutoff += grid
            _check_run_bytes(config)
    except asyncio.CancelledError:
        cancelled = True
        status, reason = "BLOCKED", "EXTERNAL_CANCELLED"
    except StopRun as exc:
        status, reason = "PARTIAL", exc.reason
    except (WriterFailure, ProbeError, ValueError, KeyError, OSError, TransportScopeError) as exc:
        status, reason = "BLOCKED", f"{type(exc).__name__}:{exc}"
    finally:
        cleanup_errors = []
        cleanup_start = clock.monotonic_ns()
        shutdown_end = min(global_end, cleanup_start + config.shutdown_seconds * NS)
        real_shutdown_end = min(real_global_end, time.monotonic() + config.shutdown_seconds)
        writer_real_end = real_shutdown_end
        close_end = cleanup_start + 5 * NS
        real_close_end = min(real_shutdown_end, time.monotonic() + 5)
        try:
            if connected:
                ledger.reserve_outbound_control(clock.monotonic_ns(), 0, kind="close")
                await call(
                    lambda: transport.send_control("close", b""),
                    5,
                    "CLOSE_SEND_TIME_LIMIT",
                    close_end,
                    cleanup=True,
                )
            await call(
                transport.close,
                max(0, real_close_end - time.monotonic()),
                "CLOSE_TIME_LIMIT",
                close_end,
                cleanup=True,
            )
        except (Exception, asyncio.CancelledError) as exc:
            cleanup_errors.append(str(exc))
        try:
            if durable_writer is not None and core is not None and session_opened:
                drain_end = min(shutdown_end, clock.monotonic_ns() + 10 * NS)
                writer_real_end = min(real_shutdown_end, time.monotonic() + 10)
                writer_budget(drain_end)
                _flush_pending_raw(durable_writer, core, pending)
                _append(
                    durable_writer,
                    core,
                    _control_row(seq, clock, "SESSION_CLOSE", session_id=config.run_id),
                )
                derived_end = min(shutdown_end, clock.monotonic_ns() + 14 * NS)
                writer_real_end = min(real_shutdown_end, time.monotonic() + 14)
                writer_budget(derived_end)
                for name, rows in (
                    ("normalized", core.normalized),
                    ("quarantine", core.quarantine),
                    ("features", core.features),
                ):
                    durable_writer.write_table(name, rows)
        except (Exception, asyncio.CancelledError) as exc:
            cleanup_errors.append(str(exc))
        finally:
            if durable_writer is not None:
                try:
                    if isinstance(durable_writer, ProcessWriter):
                        durable_writer.deadline = real_shutdown_end
                    durable_writer.close()
                except (Exception, asyncio.CancelledError) as exc:
                    cleanup_errors.append(str(exc))
        if cleanup_errors:
            status, reason = "BLOCKED", f"STOP_FAILED:{cleanup_errors}; original={reason}"
    if cancelled:
        raise asyncio.CancelledError
    return {
        "status": status,
        "stop_reason": reason,
        "run_id": config.run_id,
        "mode": "TEST_ONLY_BOUNDED_LIVE",
        "acceptance_status": "PARTIAL_L04_L05_BLOCKED",
        "network_access": "DISABLED",
        "metadata_sha256": metadata_hash,
        "observation_start_ns": observation_start,
        "observation_start_monotonic_ns": observation_mono,
        "observation_duration_ns": None
        if observation_mono is None
        else max(0, cleanup_start - observation_mono),
        "first_receive_ns": first_receive,
        "last_receive_ns": last_receive,
        "trusted_cursor": None if durable_writer is None else durable_writer.cursor,
        "messages": ledger.messages,
        "ws_payload_bytes": ledger.ws_payload_bytes,
        "outbound_control_messages": len(ledger.outbound_normal_times) + int(ledger.close_reserved),
        "inbound_control_messages": ledger.inbound_control,
        "normalized_rows": 0 if core is None else len(core.normalized),
        "quarantine_rows": 0 if core is None else len(core.quarantine),
        "feature_rows": 0 if core is None else len(core.features),
        "pending_feature_rows": 0 if core is None else len(core.pending_features),
        "encoded_state_bytes": state_bytes,
        "observed_combined_peak_rss_bytes": peak_rss,
        "limitations": {
            "physical_transport": "BLOCKED_NOT_IMPLEMENTED",
            "worker_rss_sampling": "COMMAND_BOUNDARY_ONLY_NOT_100MS_DURING_IO",
            "formal_unit_approval": "PENDING_INDEPENDENT_REVIEW",
        },
    }


def _append(writer: DurableWriter, core: Core, row: JsonRow) -> None:
    writer.append_durable(_stored_row(row))
    if row["kind"] in {
        "RAW",
        "FEATURE_CUTOFF",
        "FEATURE_PUBLISHED",
        "SESSION_OPEN",
        "SESSION_CLOSE",
        "SUBSCRIBED",
    }:
        core.process(row)


def _stored_row(row: JsonRow) -> JsonRow:
    stored = dict(row)
    if stored.get("kind") == "RAW":
        stored.pop("payload", None)
    return stored


def _flush_pending_raw(writer: DurableWriter, core: Core, rows: list[JsonRow]) -> None:
    writer.flush()
    for row in rows:
        core.process(row)
    rows.clear()


def _require_metadata(reply: MetadataReply) -> JsonRow:
    if reply.status in {418, 429, 451}:
        raise StopRun(f"REST_HTTP_{reply.status}")
    if reply.status != 200 or not reply.body_complete:
        raise StopRun("REST_NOT_COMPLETE")
    inspected = inspect_metadata(reply.body)
    if inspected.get("symbol") != "BTCUSDT":
        raise StopRun("REST_IDENTITY_MISMATCH")
    return object_json(reply.body)


def _resolve_binding(
    config: BoundedLiveConfig, evidence_bytes: bytes, metadata_object: JsonRow
) -> UnitBinding:
    evidence, evidence_sha = load_unit_evidence(evidence_bytes)
    return resolve_test_only_unit_binding(
        evidence=evidence,
        evidence_sha256=evidence_sha,
        current_metadata=metadata_object,
        approval=config.unit_approval,
        source_stream=BINDING_STREAM,
        symbol_type_raw=None,
    )


def _persist_snapshot(
    writer: DurableWriter,
    *,
    config: BoundedLiveConfig,
    evidence_bytes: bytes,
    approval: JsonRow | None,
    metadata_reply: MetadataReply,
) -> None:
    run_context = {
        "schema_version": "bounded-run-context-1",
        "config": config.model_dump(mode="json"),
        "unit_evidence": {
            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "base64": base64.b64encode(evidence_bytes).decode("ascii"),
        },
        "unit_approval": approval,
        "metadata_response": {
            "status": metadata_reply.status,
            "headers": metadata_reply.headers,
            "body_complete": metadata_reply.body_complete,
            "sha256": hashlib.sha256(metadata_reply.body).hexdigest(),
            "base64": base64.b64encode(metadata_reply.body).decode("ascii"),
        },
    }
    writer.write_snapshot(
        "snapshots/run-context.json",
        (json.dumps(run_context, sort_keys=True, indent=2) + "\n").encode(),
    )


def _control_row(seq: _Sequencer, clock: Clock, kind: str, *, session_id: str) -> JsonRow:
    return {
        "kind": kind,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "venue": VENUE,
        "market_type": MARKET_TYPE,
        "instrument_id": INSTRUMENT_ID,
        "source_stream": SOURCE_STREAM,
        "session_id": session_id,
        "ts_receive_ns": clock.wall_ns(),
        "receive_monotonic_ns": clock.monotonic_ns(),
        "ingest_sequence": seq.next(),
    }


def _raw_row(
    seq: _Sequencer,
    clock: Clock,
    session_id: str,
    payload: bytes,
    binding: UnitBinding,
) -> JsonRow:
    payload_text, flags = payload_for_core(payload)
    return {
        "kind": "RAW",
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "venue": VENUE,
        "market_type": MARKET_TYPE,
        "instrument_id": INSTRUMENT_ID,
        "source_stream": SOURCE_STREAM,
        "source_origin": "live_ws",
        "source_time_unit": "ms",
        "metadata_version": binding.current_metadata_sha256,
        "evidence_kind": binding.runtime_scope,
        "session_id": session_id,
        "ts_receive_ns": clock.wall_ns(),
        "receive_monotonic_ns": clock.monotonic_ns(),
        "ingest_sequence": seq.next(),
        "payload": payload_text,
        "payload_base64": base64.b64encode(payload).decode("ascii"),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_size": len(payload),
        "raw_ref": f"bounded:{session_id}:{seq.value}",
        "quality_flags": flags,
    }


def _cutoff_row(
    seq: _Sequencer,
    clock: Clock,
    session_id: str,
    *,
    cutoff_ns: int,
    input_cursor: int,
) -> JsonRow:
    row = _control_row(seq, clock, "FEATURE_CUTOFF", session_id=session_id)
    row.update({"cutoff_ns": cutoff_ns, "input_cursor": input_cursor})
    return row


def _published_row(seq: _Sequencer, clock: Clock, session_id: str, *, cutoff_ns: int) -> JsonRow:
    row = _control_row(seq, clock, "FEATURE_PUBLISHED", session_id=session_id)
    row.update({"cutoff_ns": cutoff_ns, "feature_available_ns": row["ts_receive_ns"]})
    return row


def _check_global_deadline(clock: Clock, deadline_ns: int) -> None:
    if clock.monotonic_ns() >= deadline_ns:
        raise StopRun("GLOBAL_TIME_LIMIT")


def _check_startup_deadline(clock: Clock, deadline_ns: int) -> None:
    if clock.monotonic_ns() >= deadline_ns:
        raise StopRun("STARTUP_TIME_LIMIT")


def _check_state_budget(
    config: BoundedLiveConfig,
    sequence: int,
    core: Core,
    writer: DurableWriter,
    cache: dict[str, int],
    pending_bytes: int,
) -> tuple[int, int]:
    if sequence > config.max_total_inputs:
        raise StopRun("STATE_INPUT_LIMIT")
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    rss_bytes = rss if sys.platform == "darwin" else rss * 1024
    rss_bytes += int(getattr(writer, "worker_rss_peak", 0))
    if rss_bytes > config.rss_stop_bytes:
        raise StopRun("RSS_LIMIT")
    # Core changes only at a durable process() boundary. Pending raw has two
    # parent copies (transient Core envelope and writer queue); charge the larger
    # encoding twice, so this accounting is a conservative bound, not a scan on
    # each market packet.
    if cache.get("cursor") != core.cursor:
        cache["bytes"] = (
            len(
                _json_line(
                    {
                        "config": core.config,
                        "metadata": core.metadata,
                        "unit_policy": core.unit_policy,
                        "history": core.history,
                        "normalized": core.normalized,
                        "quarantine": core.quarantine,
                        "features": core.features,
                        "pending_features": core.pending_features,
                        "seen": list(core.seen.values()),
                        "quality": core.quality_observations,
                        "gaps": core.gaps,
                        "errors": core.errors,
                        "conflicts": core.conflicts,
                    }
                )
            )
            + 4096
        )
        cache["cursor"] = core.cursor
    encoded = cache["bytes"] + 2 * pending_bytes
    if encoded > config.max_state_bytes:
        raise StopRun("ENCODED_STATE_LIMIT")
    return encoded, rss_bytes


def _check_run_bytes(config: BoundedLiveConfig) -> None:
    total = sum(path.stat().st_size for path in config.output_root.rglob("*") if path.is_file())
    if total > config.max_run_bytes:
        raise StopRun("RUN_BYTE_LIMIT")


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TransportScopeError("unit binding field must be a nonempty string")
    return value


class _Sequencer:
    def __init__(self) -> None:
        self.value = 0

    def next(self) -> int:
        self.value += 1
        return self.value
