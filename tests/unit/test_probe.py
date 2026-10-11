from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from btc_microstructure.probe import (
    ProbeError,
    ProbeLimits,
    Reply,
    budget_headers,
    inspect_metadata,
    inspect_trade,
    run_probe,
    save_probe,
)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "probe"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_object(name: str) -> dict[str, Any]:
    return json.loads(fixture_bytes(name))


def body(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


@dataclass
class FakeTransport:
    metadata_reply: Reply | BaseException
    connect_reply: Reply | BaseException = field(default_factory=lambda: Reply(101, {}))
    frames: list[bytes] = field(default_factory=list)
    close_error: BaseException | None = None
    metadata_calls: int = 0
    connect_calls: int = 0
    receive_calls: int = 0
    closed: bool = False

    async def metadata(self, limits: ProbeLimits) -> Reply:
        self.metadata_calls += 1
        if isinstance(self.metadata_reply, BaseException):
            raise self.metadata_reply
        return self.metadata_reply

    async def connect(self, limits: ProbeLimits) -> Reply:
        self.connect_calls += 1
        if isinstance(self.connect_reply, BaseException):
            raise self.connect_reply
        return self.connect_reply

    async def receive(self) -> bytes:
        self.receive_calls += 1
        if not self.frames:
            raise OSError("synthetic receive exhaustion")
        return self.frames.pop(0)

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def limits(**overrides: Any) -> ProbeLimits:
    defaults: dict[str, Any] = {
        "total_seconds": 2.0,
        "connect_seconds": 0.05,
        "idle_seconds": 0.05,
        "max_messages": 1,
        "max_bytes": 4096,
        "max_frame_bytes": 512,
    }
    defaults.update(overrides)
    return ProbeLimits(**defaults)


def valid_metadata_reply(**headers: str) -> Reply:
    merged_headers = {"X-MBX-USED-WEIGHT-1M": "1", **headers}
    return Reply(200, merged_headers, fixture_bytes("metadata.valid.json"))


def combined_trade_frame(**updates: Any) -> bytes:
    value = fixture_object("aggtrade.combined.json")
    value["data"].update(updates)
    return body(value)


def raw_trade_frame(**updates: Any) -> bytes:
    value = fixture_object("aggtrade.raw-extra.json")
    value.update(updates)
    return body(value)


def test_inspect_metadata_accepts_minimal_synthetic_exchange_info() -> None:
    inspected = inspect_metadata(fixture_bytes("metadata.valid.json"))

    assert inspected["symbol"] == "BTCUSDT"
    assert inspected["contractType"] == "PERPETUAL"
    assert inspected["status"] == "TRADING"
    assert inspected["tickSize"] == "0.10"
    assert inspected["stepSize"] == "0.001"
    assert inspected["minQty"] == "0.001"
    assert inspected["quantity_unit"] == "UNKNOWN"
    assert any(entry["rateLimitType"] == "REQUEST_WEIGHT" for entry in inspected["rateLimits"])


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"[]", "expected object"),
        (b'{"symbols": [], "x": NaN}', "nonfinite"),
        (b'{"symbols": [], "symbols": []}', "duplicate"),
        (b'{"symbols": "BTCUSDT", "rateLimits": []}', "symbols"),
    ],
)
def test_object_metadata_rejects_malformed_nonfinite_duplicate_and_wrong_types(
    payload: bytes, message: str
) -> None:
    with pytest.raises(ProbeError, match=message):
        inspect_metadata(payload)


def test_inspect_metadata_rejects_duplicate_btcusdt_symbols() -> None:
    with pytest.raises(ProbeError, match="expected exactly one instrument"):
        inspect_metadata(fixture_bytes("metadata.duplicate-symbol.json"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"status": "SETTLING"}, "status"),
        ({"contractType": "CURRENT_QUARTER"}, "contractType"),
        ({"baseAsset": "WBTC"}, "baseAsset"),
        ({"marginAsset": "BUSD"}, "marginAsset"),
    ],
)
def test_inspect_metadata_rejects_unknown_or_wrong_instrument_state(
    change: dict[str, Any], message: str
) -> None:
    value = fixture_object("metadata.valid.json")
    value["symbols"][0].update(change)

    with pytest.raises(ProbeError, match=message):
        inspect_metadata(body(value))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"symbols": []}, "expected exactly one instrument"),
        ({"rateLimits": []}, "REQUEST_WEIGHT"),
    ],
)
def test_inspect_metadata_rejects_missing_required_protocol_sections(
    change: dict[str, Any], message: str
) -> None:
    value = fixture_object("metadata.valid.json")
    value.update(change)

    with pytest.raises(ProbeError, match=message):
        inspect_metadata(body(value))


@pytest.mark.parametrize(
    ("filter_type", "field_name", "field_value", "message"),
    [
        ("PRICE_FILTER", "tickSize", 0.1, "exact decimal string"),
        ("LOT_SIZE", "stepSize", "NaN", "finite positive"),
        ("LOT_SIZE", "minQty", "0", "finite positive"),
    ],
)
def test_inspect_metadata_rejects_imprecise_or_nonpositive_filter_values(
    filter_type: str, field_name: str, field_value: Any, message: str
) -> None:
    value = fixture_object("metadata.valid.json")
    for item in value["symbols"][0]["filters"]:
        if item["filterType"] == filter_type:
            item[field_name] = field_value

    with pytest.raises(ProbeError, match=message):
        inspect_metadata(body(value))


def test_inspect_trade_accepts_raw_payload_and_preserves_decimal_strings_and_unknowns() -> None:
    inspected = inspect_trade(fixture_bytes("aggtrade.raw-extra.json"))

    assert inspected["agg_trade_id"] == 900000002
    assert inspected["source_time_unit"] == "ms"
    assert inspected["price_native"] == "12345.67000000"
    assert inspected["quantity_native"] == "0.12345678"
    assert inspected["quantity_unit"] == "UNKNOWN"
    assert inspected["buyer_is_maker"] is False
    assert inspected["unknown_fields"] == ["futureField"]
    assert inspected["unknown_wrapper_fields"] == []


def test_inspect_trade_accepts_combined_wrapper_optional_nq_st_and_wrapper_extra() -> None:
    inspected = inspect_trade(fixture_bytes("aggtrade.combined.json"), combined=True)

    assert inspected["normal_quantity_native"] == "0.02345678"
    assert inspected["symbol_type_raw"] == 1
    assert inspected["unknown_fields"] == []
    assert inspected["unknown_wrapper_fields"] == ["synthetic_note"]


def test_inspect_trade_requires_combined_envelope_when_requested() -> None:
    with pytest.raises(ProbeError, match="combined route requires envelope"):
        inspect_trade(fixture_bytes("aggtrade.raw-extra.json"), combined=True)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"s": "ETHUSDT"}, "unexpected event or symbol"),
        ({"e": "trade"}, "unexpected event or symbol"),
        ({"a": "900000001"}, "nonnegative integer"),
        ({"p": 12345.67}, "exact decimal string"),
        ({"q": "Infinity"}, "finite positive"),
        ({"m": 1}, "strict boolean"),
        ({"f": 20, "l": 19}, "reversed trade ID range"),
        ({"st": 2}, "unknown or non-UM"),
        ({"nq": "0.20000000"}, "exceeds total quantity"),
    ],
)
def test_inspect_trade_rejects_wrong_symbol_types_nonfinite_and_unknown_state(
    updates: dict[str, Any], message: str
) -> None:
    with pytest.raises(ProbeError, match=message):
        inspect_trade(combined_trade_frame(**updates), combined=True)


def test_budget_headers_keep_only_rate_budget_metadata_and_truncate_values() -> None:
    headers = {
        "X-MBX-USED-WEIGHT-1M": "123",
        "Retry-After": "9",
        "Set-Cookie": "secret=value",
        "Authorization": "Bearer secret",
        "Server": "synthetic",
        "X-MBX-USED-WEIGHT-1H": "x" * 200,
    }

    cleaned = budget_headers(headers)

    assert cleaned == {
        "x-mbx-used-weight-1m": "123",
        "retry-after": "9",
        "x-mbx-used-weight-1h": "x" * 128,
    }


def test_run_probe_success_is_bounded_synthetic_and_closes_transport() -> None:
    transport = FakeTransport(
        metadata_reply=valid_metadata_reply(Server="synthetic"),
        frames=[
            combined_trade_frame(),
            combined_trade_frame(a=900000003, f=700000005, l=700000006),
        ],
    )

    report = asyncio.run(run_probe(transport, limits(max_messages=2)))

    assert report["status"] == "PASS"
    assert report["evidence_kind"] == "SYNTHETIC"
    assert report["rest"]["status"] == "PASS"
    assert report["ws"]["status"] == "PASS"
    assert report["ws"]["frames_seen"] == 2
    assert len(report["ws"]["messages"]) == 2
    assert report["retry_count"] == 0
    assert report["backoff_cap_seconds"] == 0
    assert report["continuous_collection_enabled"] is False
    assert report["rest"]["rate_headers"] == {"x-mbx-used-weight-1m": "1"}
    assert "body_hex" in report["rest"]
    assert all("body_hex" in item for item in report["ws"]["messages"])
    assert transport.closed is True
    assert transport.metadata_calls == 1
    assert transport.connect_calls == 1
    assert transport.receive_calls == 2


@pytest.mark.parametrize("status", [401, 403, 418, 429, 451])
def test_run_probe_denied_rest_status_is_blocked_without_ws_retry(status: int) -> None:
    transport = FakeTransport(
        metadata_reply=Reply(status, {"Retry-After": "3", "Set-Cookie": "secret"}, b"denied")
    )

    report = asyncio.run(run_probe(transport, limits()))

    assert report["status"] == "BLOCKED"
    assert report["rest"]["status"] == "BLOCKED"
    assert report["ws"]["status"] == "NOT_RUN"
    assert report["reason"] == f"HTTP_{status}; no retry or WS fallback"
    assert report["rest"]["rate_headers"] == {"retry-after": "3"}
    assert report["retry_count"] == 0
    assert transport.connect_calls == 0
    assert transport.receive_calls == 0
    assert transport.closed is True


def test_run_probe_server_error_is_fail_without_ws_fallback() -> None:
    transport = FakeTransport(metadata_reply=Reply(500, {}, b"server-error"))

    report = asyncio.run(run_probe(transport, limits()))

    assert report["status"] == "FAIL"
    assert report["reason"] == "HTTP_500; no retry or WS fallback"
    assert transport.connect_calls == 0
    assert transport.closed is True


def test_run_probe_network_error_is_blocked_and_redacts_exception_details() -> None:
    transport = FakeTransport(metadata_reply=OSError("token=secret"))

    report = asyncio.run(run_probe(transport, limits()))

    assert report["status"] == "BLOCKED"
    assert report["rest"]["status"] == "BLOCKED"
    assert report["reason"] == "NETWORK_ERROR; exception details omitted to exclude credentials"
    assert "secret" not in json.dumps(report)
    assert transport.closed is True


def test_run_probe_ws_handshake_rejection_is_blocked_with_rate_headers() -> None:
    transport = FakeTransport(
        metadata_reply=valid_metadata_reply(),
        connect_reply=Reply(403, {"Retry-After": "7", "Set-Cookie": "secret"}, b""),
    )

    report = asyncio.run(run_probe(transport, limits()))

    assert report["status"] == "BLOCKED"
    assert report["ws"]["status"] == "BLOCKED"
    assert report["reason"] == "WS_HANDSHAKE_REJECTED"
    assert report["ws"]["http_status"] == 403
    assert report["ws"]["rate_headers"] == {"retry-after": "7"}
    assert transport.receive_calls == 0
    assert transport.closed is True


def test_run_probe_rejects_raw_trade_frame_on_combined_stream_route() -> None:
    transport = FakeTransport(metadata_reply=valid_metadata_reply(), frames=[raw_trade_frame()])

    report = asyncio.run(run_probe(transport, limits()))

    assert report["status"] == "FAIL"
    assert report["ws"]["status"] == "FAIL"
    assert report["reason"] == "stream/data: combined route requires envelope"
    assert report["ws"]["messages"][0]["truncated"] is False
    assert transport.closed is True


def test_run_probe_enforces_frame_cap_after_storing_bounded_sample() -> None:
    oversized_frame = b'{"stream":"btcusdt@aggTrade","data":' + (b'"x"' * 512) + b"}"
    transport = FakeTransport(metadata_reply=valid_metadata_reply(), frames=[oversized_frame])

    report = asyncio.run(run_probe(transport, limits(max_frame_bytes=512)))

    assert report["status"] == "FAIL"
    assert report["ws"]["status"] == "FAIL"
    assert report["reason"] == "ws: response byte/frame limit exceeded"
    assert report["ws"]["messages"][0]["truncated"] is True
    assert len(bytes.fromhex(report["ws"]["messages"][0]["body_hex"])) == 512
    assert transport.closed is True


def test_run_probe_stops_before_receive_when_remaining_byte_budget_is_too_small() -> None:
    transport = FakeTransport(
        metadata_reply=valid_metadata_reply(), frames=[combined_trade_frame()]
    )

    report = asyncio.run(run_probe(transport, limits(max_bytes=2048, max_frame_bytes=2048)))

    assert report["status"] == "BLOCKED"
    assert report["ws"]["status"] == "BLOCKED"
    assert report["reason"] == "BYTE_BUDGET_REACHED; no additional receive"
    assert report["ws"]["messages"] == []
    assert transport.receive_calls == 0
    assert transport.closed is True


def test_run_probe_marks_cleanup_failure_as_blocked_after_otherwise_successful_probe() -> None:
    transport = FakeTransport(
        metadata_reply=valid_metadata_reply(),
        frames=[combined_trade_frame()],
        close_error=OSError("synthetic close failure"),
    )

    report = asyncio.run(run_probe(transport, limits()))

    assert report["status"] == "BLOCKED"
    assert report["cleanup"] == "BLOCKED"
    assert report["reason"] == "CLEANUP_INCOMPLETE"
    assert transport.closed is True


def test_save_probe_writes_samples_hashes_budget_and_does_not_mutate_input(tmp_path: Path) -> None:
    transport = FakeTransport(
        metadata_reply=valid_metadata_reply(), frames=[combined_trade_frame()]
    )
    report = asyncio.run(run_probe(transport, limits(), evidence_kind="LIVE"))
    original_rest_hex = report["rest"]["body_hex"]
    original_ws_hex = report["ws"]["messages"][0]["body_hex"]

    save_probe(report, tmp_path / "probe")

    assert report["rest"]["body_hex"] == original_rest_hex
    assert report["ws"]["messages"][0]["body_hex"] == original_ws_hex
    rest_payload = (tmp_path / "probe" / "rest-response.bin").read_bytes()
    ws_payload = (tmp_path / "probe" / "ws-000.bin").read_bytes()
    saved_report = json.loads((tmp_path / "probe" / "protocol_probe_report.json").read_text())
    saved_budget = json.loads((tmp_path / "probe" / "endpoint_budget.json").read_text())

    assert saved_report["evidence_kind"] == "LIVE"
    assert saved_report["rest"]["sample"] == "rest-response.bin"
    assert saved_report["rest"]["sha256"] == hashlib.sha256(rest_payload).hexdigest()
    assert saved_report["ws"]["messages"][0]["sample"] == "ws-000.bin"
    assert saved_report["ws"]["messages"][0]["sha256"] == hashlib.sha256(ws_payload).hexdigest()
    assert saved_budget["evidence_kind"] == "LIVE"
    assert saved_budget["status"] == "UNVERIFIED_FOR_CONTINUOUS_COLLECTION"
    assert saved_budget["continuous_collection_enabled"] is False

    with pytest.raises(FileExistsError):
        save_probe(report, tmp_path / "probe")


def test_connect_timeout_is_blocked_and_closed() -> None:
    class Slow(FakeTransport):
        async def metadata(self, limits: ProbeLimits) -> Reply:
            await asyncio.sleep(10)
            return valid_metadata_reply()

    transport = Slow(metadata_reply=valid_metadata_reply())
    report = asyncio.run(run_probe(transport, limits(connect_seconds=0.01)))
    assert report["status"] == "BLOCKED"
    assert report["reason"] == "TIMEOUT; no retry"
    assert report["ws"]["status"] == "NOT_RUN"
    assert transport.closed


def test_total_deadline_overrides_long_receive_timeout() -> None:
    import time

    class Slow(FakeTransport):
        async def receive(self) -> bytes:
            await asyncio.sleep(10)
            return combined_trade_frame()

    transport = Slow(metadata_reply=valid_metadata_reply())
    start = time.monotonic()
    report = asyncio.run(run_probe(transport, limits(idle_seconds=10.0)))
    assert time.monotonic() - start < 2.0
    assert report["status"] == "BLOCKED"
    assert report["reason"] == "TIMEOUT; no retry"
    assert report["rest"]["status"] == "PASS"
    assert transport.closed


def test_idle_timeout_does_not_wait_for_total_deadline() -> None:
    class Slow(FakeTransport):
        async def receive(self) -> bytes:
            await asyncio.sleep(10)
            return combined_trade_frame()

    transport = Slow(metadata_reply=valid_metadata_reply())
    report = asyncio.run(run_probe(transport, limits(idle_seconds=0.01)))
    assert report["status"] == "BLOCKED"
    assert report["elapsed_seconds"] < 1.0
    assert report["ws"]["frames_seen"] == 0
    assert transport.closed


def test_oversized_rest_payload_retains_prefix_and_blocks_ws() -> None:
    transport = FakeTransport(metadata_reply=Reply(200, {}, b"x" * 2049))
    report = asyncio.run(run_probe(transport, limits(max_bytes=2048)))
    assert report["status"] == "FAIL"
    assert report["reason"] == "rest: response byte limit exceeded"
    assert report["rest"]["truncated"] is True
    assert len(bytes.fromhex(report["rest"]["body_hex"])) == 2048
    assert report["bytes_received"] == 2049
    assert transport.connect_calls == 0
    assert transport.closed


def test_partial_sample_exhausting_byte_budget_cannot_pass() -> None:
    transport = FakeTransport(
        metadata_reply=valid_metadata_reply(), frames=[combined_trade_frame()] * 20
    )
    report = asyncio.run(
        run_probe(transport, limits(max_messages=20, max_bytes=2048, max_frame_bytes=512))
    )
    assert 0 < len(report["ws"]["messages"]) < 20
    assert report["reason"] == "BYTE_BUDGET_REACHED; no additional receive"
    assert report["status"] == "BLOCKED"
    assert report["ws"]["status"] == "BLOCKED"
    assert all("inspection" in sample for sample in report["ws"]["messages"])
    assert transport.closed
