from __future__ import annotations

import asyncio
import base64
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from btc_microstructure.bounded_live import BoundedLiveConfig
from btc_microstructure.bounded_live import StateCharge
from btc_microstructure.bounded_live import run_test_only_bounded_live
from btc_microstructure.bounded_replay import ReadOnlyStore
from btc_microstructure.bounded_replay import replay_bounded
from btc_microstructure.bounded_transport import MetadataReply, TransportScopeError, WsEvent
from btc_microstructure.offline_core import BOUND_ADAPTER_VERSION, NS
from btc_microstructure.offline_core import Core as RealCore
from btc_microstructure.unit_binding import TEST_ONLY_APPROVAL_KIND, STREAM, UNIT_EVIDENCE_ID
from btc_microstructure.unit_binding import load_unit_evidence

from bounded_fakes import FakeClock, FakeTransport, agg_trade, metadata_body


EVIDENCE_PATH = Path("reports/round2/units_and_collection_plan/quantity_unit_evidence.json")


def make_test_only_approval() -> dict[str, object]:
    _, evidence_sha = load_unit_evidence(EVIDENCE_PATH.read_bytes())
    return {
        "kind": TEST_ONLY_APPROVAL_KIND,
        "approval_id": "TEST_ONLY:r2-04:unit-binding",
        "unit_evidence_id": UNIT_EVIDENCE_ID,
        "evidence_version": "1",
        "evidence_sha256": evidence_sha,
        "instrument_id": "binance:um_perpetual:BTCUSDT",
        "source_stream": "um_agg_trade",
        "stream_name": STREAM,
        "st_if_present": 1,
        "filters": {"tickSize": "0.10", "stepSize": "0.001", "minQty": "0.001"},
    }


def bounded_config(tmp_path: Path, **updates: Any) -> BoundedLiveConfig:
    value: dict[str, Any] = {
        "run_id": "r2-04-test",
        "output_root": tmp_path / "run",
        "grid_seconds": 1,
        "window_seconds": [30],
        "observation_seconds": 2,
        "min_free_bytes": 0,
        "unit_approval": make_test_only_approval(),
    }
    value.update(updates)
    return BoundedLiveConfig(**value)


def _run(
    config: Any, transport: Any, clock: FakeClock, writer: Any | None = None
) -> dict[str, Any]:
    return asyncio.run(
        run_test_only_bounded_live(config, transport=transport, clock=clock, writer=writer)
    )


def _rows(root: Path) -> list[dict[str, Any]]:
    return list(ReadOnlyStore(root).read_rows())


def _context(root: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(
        (root / "snapshots" / "run-context.json").read_text(encoding="utf-8")
    )
    return value


class RecordingWriter:
    def __init__(self) -> None:
        self.cursor: int | None = None
        self.rows: list[dict[str, Any]] = []
        self.closed = False

    def append(self, row: dict[str, Any]) -> None:
        self.rows.append(deepcopy(row))

    def flush(self) -> dict[str, Any] | None:
        if self.rows:
            self.cursor = self.rows[-1]["ingest_sequence"]
        return None

    def write_snapshot(self, relative_path: str, payload: bytes) -> None:
        return None

    def append_durable(self, row: dict[str, Any]) -> None:
        self.append(row)
        self.flush()

    def write_table(self, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"manifest": {"path": name, "sha256": "TEST_ONLY"}}

    def close(self) -> None:
        self.flush()
        self.closed = True


def test_rest_http_rejection_is_durable_before_ws_connect(tmp_path: Path) -> None:
    clock = FakeClock()
    body = b'{"code":451,"msg":"blocked"}'
    transport = FakeTransport(clock, [], metadata=MetadataReply(status=451, headers={}, body=body))

    result = _run(bounded_config(tmp_path), transport, clock)

    assert result["status"] == "PARTIAL"
    assert result["stop_reason"] == "REST_HTTP_451"
    assert transport.connect_calls == 0
    assert result["trusted_cursor"] is not None
    context = _context(tmp_path / "run")
    assert context["metadata_response"]["status"] == 451
    assert base64.b64decode(context["metadata_response"]["base64"]) == body
    rest_receipt = next(row for row in _rows(tmp_path / "run") if row["kind"] == "REST_RESPONSE")
    assert rest_receipt["http_status"] == 451
    assert rest_receipt["body_complete"] is True
    assert rest_receipt["adapter_version"] == BOUND_ADAPTER_VERSION


def test_partial_rest_body_is_saved_and_blocks_ws(tmp_path: Path) -> None:
    clock = FakeClock()
    body = metadata_body()[:37]
    transport = FakeTransport(
        clock, [], metadata=MetadataReply(status=200, headers={}, body=body, body_complete=False)
    )

    result = _run(bounded_config(tmp_path), transport, clock)

    assert result["status"] == "PARTIAL"
    assert result["stop_reason"] == "REST_NOT_COMPLETE"
    assert transport.connect_calls == 0
    context = _context(tmp_path / "run")
    assert context["metadata_response"]["body_complete"] is False
    assert base64.b64decode(context["metadata_response"]["base64"]) == body
    assert any(row["kind"] == "REST_RESPONSE" for row in _rows(tmp_path / "run"))


def test_unit_rejection_keeps_rest_receipt_and_does_not_connect(tmp_path: Path) -> None:
    clock = FakeClock()
    bad_approval = deepcopy(make_test_only_approval())
    bad_approval["filters"]["tickSize"] = "0.01"  # type: ignore[index]
    transport = FakeTransport(clock, [])

    result = _run(bounded_config(tmp_path, unit_approval=bad_approval), transport, clock)

    assert result["status"] == "BLOCKED"
    assert "UnitBindingError" in result["stop_reason"]
    assert transport.connect_calls == 0
    rows = _rows(tmp_path / "run")
    assert any(row["kind"] == "REST_RESPONSE" for row in rows)
    assert all(row["kind"] != "SESSION_OPEN" for row in rows)


def test_test_only_raw_and_control_use_bounded_adapter_version(tmp_path: Path) -> None:
    clock = FakeClock()
    start = clock.wall_ns()
    transport = FakeTransport(
        clock,
        [
            (
                start + 100_000_000,
                WsEvent("message", agg_trade(agg_id=1, quantity="0.1", buyer_is_maker=False)),
            ),
            (start + NS, WsEvent("disconnect")),
        ],
    )

    _run(bounded_config(tmp_path, window_seconds=[1]), transport, clock)

    rows = _rows(tmp_path / "run")
    checked_kinds = {"REST_RESPONSE", "SESSION_OPEN", "SUBSCRIBED", "RAW", "FEATURE_CUTOFF"}
    assert {row["adapter_version"] for row in rows if row["kind"] in checked_kinds} == {
        BOUND_ADAPTER_VERSION
    }


def test_rest_timeout_observation_is_durable_without_ws_connect(tmp_path: Path) -> None:
    clock = FakeClock()
    observed = MetadataReply(status=200, headers={}, body=metadata_body()[:29], body_complete=False)

    class TimeoutTransport(FakeTransport):
        @property
        def metadata_observation(self) -> MetadataReply:
            return observed

        async def metadata(self) -> MetadataReply:
            self.metadata_calls += 1
            raise TimeoutError("injected rest timeout")

    transport = TimeoutTransport(clock, [])

    result = _run(bounded_config(tmp_path), transport, clock)

    assert result["status"] == "PARTIAL"
    assert result["stop_reason"] == "REST_TIME_LIMIT"
    assert transport.connect_calls == 0
    context = _context(tmp_path / "run")
    assert base64.b64decode(context["metadata_response"]["base64"]) == observed.body
    rows = _rows(tmp_path / "run")
    assert any(row["kind"] == "REST_RESPONSE" for row in rows)
    failure = next(row for row in rows if row["kind"] == "STARTUP_FAILURE")
    assert failure["failure_reason"] == "REST_TIME_LIMIT"


def test_rest_failure_before_response_keeps_startup_failure_receipt(tmp_path: Path) -> None:
    clock = FakeClock()

    class NoResponseTransport(FakeTransport):
        async def metadata(self) -> MetadataReply:
            self.metadata_calls += 1
            raise TransportScopeError("REST_NETWORK_ERROR")

    transport = NoResponseTransport(clock, [])
    writer = RecordingWriter()

    result = _run(bounded_config(tmp_path), transport, clock, writer)

    assert result["status"] == "BLOCKED"
    assert result["stop_reason"] == "TransportScopeError:REST_NETWORK_ERROR"
    assert transport.connect_calls == 0
    assert writer.closed is True
    assert writer.cursor is not None
    assert [row["kind"] for row in writer.rows] == ["STARTUP_FAILURE"]
    assert writer.rows[0]["failure_reason"] == "TransportScopeError:REST_NETWORK_ERROR"


def test_state_cap_rejects_before_raw_core_process_but_keeps_raw_prefix(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from btc_microstructure import bounded_live

    processed: list[str] = []

    class SpyCore(RealCore):
        def process(self, row: dict[str, Any]) -> None:
            processed.append(row["kind"])
            super().process(row)

    monkeypatch.setattr(bounded_live, "Core", SpyCore)
    monkeypatch.setattr(
        bounded_live.StateReservations,
        "reserve_raw_payload",
        lambda self, payload_size: 10_000_000,
    )
    clock = FakeClock()
    start = clock.wall_ns()
    payload = b"\xff" * 40_000
    transport = FakeTransport(
        clock,
        [
            (start + 100_000_000, WsEvent("message", payload)),
            (start + NS, WsEvent("disconnect")),
        ],
    )

    result = _run(bounded_config(tmp_path, max_state_bytes=1_000_000), transport, clock)

    assert result["stop_reason"] == "ENCODED_STATE_LIMIT"
    assert "RAW" not in processed
    rows = _rows(tmp_path / "run")
    raw = next(row for row in rows if row["kind"] == "RAW")
    assert base64.b64decode(raw["payload_base64"]) == payload
    assert raw["derivation_status"] == "DERIVATION_NOT_RUN_STATE_LIMIT"
    assert not (tmp_path / "run" / "tables" / "features.parquet").exists()
    assert replay_bounded(tmp_path / "run")["status"] == "PARTIAL"
    assert (tmp_path / "run" / "writer-closed").exists()


def test_control_state_cap_rejects_before_writer_append(tmp_path: Path, monkeypatch: Any) -> None:
    from btc_microstructure import bounded_live

    original = bounded_live.StateReservations.reserve_core_row

    def oversized_control(self: Any, row: dict[str, Any], core: Any) -> StateCharge:
        if row["kind"] == "SESSION_OPEN":
            return StateCharge(persistent_bytes=10_000_000)
        return original(self, row, core)

    monkeypatch.setattr(bounded_live.StateReservations, "reserve_core_row", oversized_control)
    clock = FakeClock()
    writer = RecordingWriter()

    result = _run(
        bounded_config(tmp_path, max_state_bytes=1_000_000),
        FakeTransport(clock, []),
        clock,
        writer=writer,
    )

    assert result["stop_reason"] == "ENCODED_STATE_LIMIT"
    assert writer.closed
    assert [row["kind"] for row in writer.rows].count("REST_RESPONSE") == 1
    assert all(row["kind"] != "SESSION_OPEN" for row in writer.rows)


def test_stop_reason_is_durable_in_terminal_control_receipt(tmp_path: Path) -> None:
    clock = FakeClock()
    config = bounded_config(tmp_path)
    result = _run(config, FakeTransport(clock, [(clock.wall_ns(), WsEvent("disconnect"))]), clock)
    terminal = next(row for row in _rows(config.output_root) if row["kind"] == "SESSION_CLOSE")
    assert result["stop_reason"] == "DISCONNECT"
    assert terminal["stop_reason"] == "DISCONNECT"
    assert terminal["stop_status"] == "PARTIAL"
    assert result["trusted_cursor"] == terminal["ingest_sequence"]


def test_external_rest_cancellation_preserves_received_prefix_and_propagates(
    tmp_path: Path,
) -> None:
    import pytest

    clock = FakeClock()
    prefix = b'{"symbols":['

    class CancelledMetadata(FakeTransport):
        metadata_observation = MetadataReply(200, {}, prefix, body_complete=False)

        async def metadata(self) -> MetadataReply:
            self.metadata_calls += 1
            raise asyncio.CancelledError

    config = bounded_config(tmp_path)
    transport = CancelledMetadata(clock, [])
    with pytest.raises(asyncio.CancelledError):
        _run(config, transport, clock)
    response = _context(config.output_root)["metadata_response"]
    assert base64.b64decode(response["base64"]) == prefix
    assert response["body_complete"] is False
    assert transport.metadata_calls == 1
    assert transport.connect_calls == 0
    assert transport.closed
    assert (config.output_root / "writer-closed").read_bytes() == b"closed\n"
    failure = next(row for row in _rows(config.output_root) if row["kind"] == "STARTUP_FAILURE")
    assert failure["failure_reason"] == "CancelledError:"
