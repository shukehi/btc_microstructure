from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from pydantic import ValidationError

from btc_microstructure.cli import main
from btc_microstructure.probe import ProbeLimits, Reply, REST_URL, WS_URL, run_probe, save_probe
from btc_microstructure.probe_transport import AioTransport


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "synthetic" / "probe"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def combined_trade_frame() -> bytes:
    return (FIXTURES / "aggtrade.combined.json").read_bytes()


@dataclass
class ScriptedTransport:
    metadata_delay: float = 0.0
    receive_delay: float = 0.0
    metadata_reply: Reply = field(
        default_factory=lambda: Reply(
            200, {"X-MBX-USED-WEIGHT-1M": "1"}, fixture_bytes("metadata.valid.json")
        )
    )
    connect_reply: Reply = field(default_factory=lambda: Reply(101, {}))
    frames: list[bytes] = field(default_factory=lambda: [combined_trade_frame()])
    metadata_calls: int = 0
    connect_calls: int = 0
    receive_calls: int = 0
    closed: bool = False

    async def metadata(self, limits: ProbeLimits) -> Reply:
        self.metadata_calls += 1
        await asyncio.sleep(self.metadata_delay)
        return self.metadata_reply

    async def connect(self, limits: ProbeLimits) -> Reply:
        self.connect_calls += 1
        return self.connect_reply

    async def receive(self) -> bytes:
        self.receive_calls += 1
        await asyncio.sleep(self.receive_delay)
        return self.frames.pop(0)

    async def close(self) -> None:
        self.closed = True


class ChunkContent:
    def __init__(self, chunks: list[bytes], delay: float) -> None:
        self.chunks = list(chunks)
        self.delay = delay
        self.read_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if not self.chunks:
            return b""
        await asyncio.sleep(self.delay)
        chunk = self.chunks.pop(0)
        return chunk[:size]


class ChunkResponse:
    status = 200
    headers = {"X-MBX-USED-WEIGHT-1M": "2"}

    def __init__(self, content: ChunkContent) -> None:
        self.content = content
        self.exited = False

    async def __aenter__(self) -> "ChunkResponse":
        return self

    async def __aexit__(self, *args: object) -> bool:
        self.exited = True
        return False


class TextSocket:
    def __init__(self, frames: list[bytes] | None = None) -> None:
        self.frames = list(frames or [combined_trade_frame()])

    async def receive(self) -> object:
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=self.frames.pop(0).decode())

    async def close(self) -> None:
        pass


class CaptureSession:
    def __init__(self, **options: object) -> None:
        self.options = options
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.response = ChunkResponse(ChunkContent([fixture_bytes("metadata.valid.json")], 0.0))
        self.socket = TextSocket()
        self.closed = False

    def get(self, url: str, **kwargs: object) -> ChunkResponse:
        self.calls.append(("GET", url, kwargs))
        return self.response

    async def ws_connect(self, url: str, **kwargs: object) -> object:
        self.calls.append(("WS", url, kwargs))
        return self.socket

    async def close(self) -> None:
        self.closed = True


def test_probe_limits_keep_old_default_and_reject_invalid_rest_total_values() -> None:
    legacy = ProbeLimits(connect_seconds=4.0)

    assert legacy.rest_total_seconds is None

    for value in (0.0, -1.0, 20.1, float("nan"), float("inf"), True):
        with pytest.raises(ValidationError):
            ProbeLimits(rest_total_seconds=value)


def test_cli_rest_total_seconds_reaches_limits_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: list[ProbeLimits] = []

    class Denied:
        async def metadata(self, limits: ProbeLimits) -> Reply:
            captured.append(limits)
            return Reply(451, {"Retry-After": "5"})

        async def connect(self, limits: ProbeLimits) -> Reply:
            raise AssertionError("WS must not run after HTTP rejection")

        async def close(self) -> None:
            pass

    monkeypatch.setattr("btc_microstructure.probe_transport.AioTransport", Denied)

    result = main(
        [
            "probe",
            str(ROOT / "config/research.example.yaml"),
            "--output",
            str(tmp_path / "probe"),
            "--allow-network",
            "--rest-total-seconds",
            "15",
        ]
    )

    assert result == 1
    assert captured[0].rest_total_seconds == 15.0
    assert '"status": "BLOCKED"' in capsys.readouterr().out


@pytest.mark.parametrize("bad_value", ["0", "21", "nan", "inf"])
def test_cli_rejects_invalid_rest_total_seconds_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    bad_value: str,
) -> None:
    def denied() -> object:
        raise AssertionError("transport must not be constructed for invalid limits")

    monkeypatch.setattr("btc_microstructure.probe_transport.AioTransport", denied)

    result = main(
        [
            "probe",
            str(ROOT / "config/research.example.yaml"),
            "--output",
            str(tmp_path / f"probe-{bad_value}"),
            "--allow-network",
            "--rest-total-seconds",
            bad_value,
        ]
    )

    assert result == 1
    assert "rest_total_seconds" in capsys.readouterr().err


def test_run_probe_uses_rest_total_for_metadata_without_changing_legacy_default() -> None:
    slow_metadata = 0.07
    old_transport = ScriptedTransport(metadata_delay=slow_metadata)
    old_report = asyncio.run(
        run_probe(old_transport, ProbeLimits(total_seconds=2.0, connect_seconds=0.03))
    )
    new_transport = ScriptedTransport(metadata_delay=slow_metadata)
    new_report = asyncio.run(
        run_probe(
            new_transport,
            ProbeLimits(
                total_seconds=2.0,
                connect_seconds=0.03,
                rest_total_seconds=0.2,
                max_messages=1,
            ),
        )
    )

    assert old_report["status"] == "BLOCKED"
    assert old_report["rest"]["status"] == "BLOCKED"
    assert old_report["ws"]["status"] == "NOT_RUN"
    assert old_transport.connect_calls == 0
    assert new_report["status"] == "PASS"
    assert new_report["rest"]["status"] == "PASS"
    assert new_report["ws"]["status"] == "PASS"
    assert new_transport.connect_calls == 1
    assert new_transport.receive_calls == 1


def test_report_records_candidate_timeout_mapping() -> None:
    transport = ScriptedTransport()

    report = asyncio.run(
        run_probe(
            transport,
            ProbeLimits(
                total_seconds=30.0,
                connect_seconds=5.0,
                rest_total_seconds=15.0,
                idle_seconds=5.0,
                max_messages=1,
            ),
        )
    )

    assert report["status"] == "PASS"
    assert report["configured_timeouts"] == {
        "total_seconds": 30.0,
        "connect_seconds": 5.0,
        "rest_total_seconds": 15.0,
        "rest_total_mode": "EXPLICIT",
        "idle_seconds": 5.0,
        "cleanup_seconds": 1.0,
    }
    assert report["effective_timeouts"]["work_seconds"] == 29.0
    assert report["effective_timeouts"]["rest_total_mode"] == "EXPLICIT"
    assert report["effective_timeouts"]["rest_total_cap_source"] == "REST_TOTAL"


def test_aio_transport_slow_complete_rest_body_can_pass_with_explicit_rest_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = fixture_bytes("metadata.valid.json")
    response = ChunkResponse(ChunkContent([metadata[:80], metadata[80:]], delay=0.025))
    session = CaptureSession()
    session.response = response
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: session)

    report = asyncio.run(
        run_probe(
            AioTransport(),
            ProbeLimits(
                total_seconds=2.0,
                connect_seconds=0.03,
                rest_total_seconds=0.2,
                idle_seconds=0.05,
                max_messages=1,
            ),
        )
    )

    assert report["status"] == "PASS"
    assert report["rest"]["status"] == "PASS"
    assert report["ws"]["status"] == "PASS"
    assert report["metadata"]["quantity_unit"] == "UNKNOWN"
    assert session.calls[0][0] == "GET"
    assert session.calls[1][0] == "WS"
    assert response.content.read_sizes


def test_aio_transport_uses_rest_request_timeout_without_leaking_it_to_ws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[CaptureSession] = []

    def factory(**options: object) -> CaptureSession:
        session = CaptureSession(**options)
        sessions.append(session)
        return session

    monkeypatch.setattr(aiohttp, "ClientSession", factory)

    async def check() -> None:
        transport = AioTransport()
        limits = ProbeLimits(connect_seconds=0.04, idle_seconds=0.05, rest_total_seconds=0.2)
        await transport.metadata(limits)
        await transport.connect(limits)
        await transport.close()

    asyncio.run(check())

    session = sessions[0]
    session_timeout = session.options["timeout"]
    assert isinstance(session_timeout, aiohttp.ClientTimeout)
    assert session_timeout.connect == 0.04
    assert session_timeout.sock_connect == 0.04
    assert session_timeout.sock_read == 0.05
    get_timeout = session.calls[0][2]["timeout"]
    assert isinstance(get_timeout, aiohttp.ClientTimeout)
    assert get_timeout.total == 0.2
    ws_timeout = session.calls[1][2]["timeout"]
    assert isinstance(ws_timeout, aiohttp.ClientWSTimeout)
    assert ws_timeout.ws_receive == 0.05
    assert not isinstance(ws_timeout, aiohttp.ClientTimeout)
    assert session.calls[0][1] == REST_URL
    assert session.calls[1][1] == WS_URL


def test_aio_transport_rest_total_timeout_retains_prefix_and_blocks_ws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = b'{"symbols":['
    response = ChunkResponse(ChunkContent([prefix, b'"slow"'], delay=0.035))
    session = CaptureSession()
    session.response = response
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: session)

    report = asyncio.run(
        run_probe(
            AioTransport(),
            ProbeLimits(
                total_seconds=2.0,
                connect_seconds=0.02,
                rest_total_seconds=0.055,
                idle_seconds=0.2,
                max_bytes=2048,
            ),
        )
    )

    assert report["status"] == "BLOCKED"
    assert report["rest"]["status"] == "BLOCKED"
    assert report["ws"]["status"] == "NOT_RUN"
    assert report["rest"]["http_status"] == 200
    assert report["rest"]["body_hex"] == prefix.hex()
    assert report["rest"]["body_complete"] is False
    diagnostic = report["rest"]["timeout_diagnostics"]
    assert diagnostic["source"] == "REST_TOTAL"
    assert diagnostic["phase"] == "REST_HEADERS_OR_BODY"
    assert diagnostic["transport_phase"] in {"BODY", "REST_READ_BODY"}
    assert report["logical_calls"] == {"rest": 1, "ws": 0}
    assert session.calls == [
        ("GET", REST_URL, {"allow_redirects": False, "timeout": session.calls[0][2]["timeout"]})
    ]
    assert response.exited and session.closed


def test_aio_transport_records_socket_read_timeout_as_body_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutContent:
        async def read(self, size: int) -> bytes:
            raise aiohttp.SocketTimeoutError("SYNTHETIC_SOCKET_TIMEOUT")

    response = ChunkResponse(TimeoutContent())  # type: ignore[arg-type]
    session = CaptureSession()
    session.response = response
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: session)

    async def check() -> AioTransport:
        transport = AioTransport()
        with pytest.raises(TimeoutError):
            await transport.metadata(ProbeLimits(rest_total_seconds=0.2))
        await transport.close()
        return transport

    transport = asyncio.run(check())

    assert transport.timeout_diagnostic is not None
    assert transport.timeout_diagnostic["phase"] in {"BODY", "REST_READ_BODY"}
    assert transport.timeout_diagnostic["source"] == "AIOHTTP_SOCKET_READ_TIMEOUT"
    assert transport.metadata_observation is not None
    assert transport.metadata_observation.body_read_state == "INTERRUPTED"


def test_aio_transport_records_connection_timeout_before_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HeaderTimeoutResponse(ChunkResponse):
        async def __aenter__(self) -> "HeaderTimeoutResponse":
            raise aiohttp.ConnectionTimeoutError("SYNTHETIC_HEADER_TIMEOUT")

    session = CaptureSession()
    session.response = HeaderTimeoutResponse(ChunkContent([], 0.0))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: session)

    async def check() -> AioTransport:
        transport = AioTransport()
        with pytest.raises(TimeoutError):
            await transport.metadata(ProbeLimits(rest_total_seconds=0.2))
        await transport.close()
        return transport

    transport = asyncio.run(check())

    assert transport.timeout_diagnostic is not None
    assert transport.timeout_diagnostic["phase"] in {"REQUEST", "REST_WAIT_HEADERS_OR_CONNECT"}
    assert transport.timeout_diagnostic["source"] == "AIOHTTP_CONNECT_TIMEOUT"
    assert transport.metadata_observation is None


def test_aio_transport_records_header_wait_socket_timeout_before_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HeaderTimeoutResponse(ChunkResponse):
        async def __aenter__(self) -> "HeaderTimeoutResponse":
            raise aiohttp.SocketTimeoutError("SYNTHETIC_HEADER_IDLE_TIMEOUT")

    session = CaptureSession()
    session.response = HeaderTimeoutResponse(ChunkContent([], 0.0))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: session)

    async def check() -> AioTransport:
        transport = AioTransport()
        with pytest.raises(TimeoutError):
            await transport.metadata(ProbeLimits(rest_total_seconds=0.2))
        await transport.close()
        return transport

    transport = asyncio.run(check())

    assert transport.timeout_diagnostic is not None
    assert transport.timeout_diagnostic["phase"] in {"REQUEST", "REST_WAIT_HEADERS_OR_CONNECT"}
    assert transport.timeout_diagnostic["source"] == "AIOHTTP_SOCKET_READ_TIMEOUT"
    assert transport.metadata_observation is None


def test_unattributed_transport_timeout_keeps_unknown_source() -> None:
    class GenericTimeout:
        async def metadata(self, limits: ProbeLimits) -> Reply:
            raise TimeoutError

        async def connect(self, limits: ProbeLimits) -> Reply:
            raise AssertionError("WS must not run after REST timeout")

        async def receive(self) -> bytes:
            raise AssertionError("WS must not receive after REST timeout")

        async def close(self) -> None:
            pass

    report = asyncio.run(
        run_probe(
            GenericTimeout(),
            ProbeLimits(total_seconds=2.0, connect_seconds=0.05, rest_total_seconds=0.2),
        )
    )

    assert report["status"] == "BLOCKED"
    assert report["reason"] == "TIMEOUT; no retry"
    assert report["rest"]["timeout_diagnostics"]["source"] == "UNKNOWN"
    assert report["rest"]["timeout_diagnostics"]["transport_source"] == "UNKNOWN"
    assert report["rest"]["timeout_diagnostics"]["transport_phase"] == "UNKNOWN"


def test_ws_handshake_timeout_reports_connect_phase() -> None:
    class SlowHandshake(ScriptedTransport):
        async def connect(self, limits: ProbeLimits) -> Reply:
            self.connect_calls += 1
            await asyncio.sleep(10)
            return Reply(101, {})

    transport = SlowHandshake(metadata_reply=Reply(200, {}, fixture_bytes("metadata.valid.json")))

    report = asyncio.run(
        run_probe(
            transport,
            ProbeLimits(
                total_seconds=2.0,
                connect_seconds=0.03,
                rest_total_seconds=0.2,
                max_messages=1,
            ),
        )
    )

    assert report["status"] == "BLOCKED"
    assert report["rest"]["status"] == "PASS"
    assert report["ws"]["status"] == "BLOCKED"
    assert report["ws"]["timeout_diagnostics"]["phase"] == "WS_CONNECT"
    assert report["ws"]["timeout_diagnostics"]["source"] == "CONNECT"
    assert transport.receive_calls == 0


def test_aio_transport_ws_connect_timeout_reports_typed_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConnectTimeoutSession(CaptureSession):
        async def ws_connect(self, url: str, **kwargs: object) -> object:
            self.calls.append(("WS", url, kwargs))
            raise aiohttp.ConnectionTimeoutError("SYNTHETIC_WS_CONNECT_TIMEOUT")

    session = ConnectTimeoutSession()
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: session)

    report = asyncio.run(
        run_probe(
            AioTransport(),
            ProbeLimits(
                total_seconds=2.0,
                connect_seconds=0.2,
                rest_total_seconds=0.2,
                max_messages=1,
            ),
        )
    )

    assert report["status"] == "BLOCKED"
    assert report["rest"]["status"] == "PASS"
    assert report["ws"]["status"] == "BLOCKED"
    assert report["reason"] == "TIMEOUT; no retry"
    assert report["ws"]["timeout_diagnostics"]["phase"] == "WS_CONNECT"
    assert report["ws"]["timeout_diagnostics"]["source"] == "UNKNOWN"
    assert report["ws"]["timeout_diagnostics"]["transport_source"] == "AIOHTTP_CONNECT_TIMEOUT"
    assert report["ws"]["timeout_diagnostics"]["transport_phase"] == "WS_CONNECT"
    assert session.closed


def test_aio_transport_ws_receive_timeout_reports_typed_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutSocket(TextSocket):
        async def receive(self) -> object:
            raise aiohttp.SocketTimeoutError("SYNTHETIC_WS_RECEIVE_TIMEOUT")

    session = CaptureSession()
    session.socket = TimeoutSocket()
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: session)

    report = asyncio.run(
        run_probe(
            AioTransport(),
            ProbeLimits(
                total_seconds=2.0,
                connect_seconds=0.2,
                rest_total_seconds=0.2,
                idle_seconds=0.2,
                max_messages=1,
            ),
        )
    )

    assert report["status"] == "BLOCKED"
    assert report["rest"]["status"] == "PASS"
    assert report["ws"]["status"] == "BLOCKED"
    assert report["reason"] == "TIMEOUT; no retry"
    assert report["ws"]["frames_seen"] == 0
    assert report["ws"]["timeout_diagnostics"]["phase"] == "WS_RECEIVE"
    assert report["ws"]["timeout_diagnostics"]["source"] == "UNKNOWN"
    assert report["ws"]["timeout_diagnostics"]["transport_source"] == "AIOHTTP_SOCKET_READ_TIMEOUT"
    assert report["ws"]["timeout_diagnostics"]["transport_phase"] == "WS_RECEIVE"
    assert session.closed


def test_global_deadline_shorter_than_rest_total_stops_before_ws() -> None:
    transport = ScriptedTransport(metadata_delay=1.2)

    report = asyncio.run(
        run_probe(
            transport,
            ProbeLimits(
                total_seconds=2.0,
                connect_seconds=0.05,
                rest_total_seconds=5.0,
                max_messages=1,
            ),
        )
    )

    assert report["status"] == "BLOCKED"
    assert report["rest"]["status"] == "BLOCKED"
    assert report["ws"]["status"] == "NOT_RUN"
    diagnostic = report["rest"]["timeout_diagnostics"]
    assert diagnostic["source"] == "GLOBAL_WORK"
    assert diagnostic["phase"] in {"REST_HEADERS_OR_BODY", "REST_WAIT_HEADERS_OR_BODY"}
    assert transport.connect_calls == 0
    assert transport.closed


def test_ws_receive_keeps_global_elapsed_budget_after_rest_success() -> None:
    transport = ScriptedTransport(metadata_delay=0.04, receive_delay=10.0)

    report = asyncio.run(
        run_probe(
            transport,
            ProbeLimits(
                total_seconds=2.0,
                connect_seconds=0.03,
                rest_total_seconds=0.2,
                idle_seconds=10.0,
                max_messages=1,
            ),
        )
    )

    assert report["status"] == "BLOCKED"
    assert report["rest"]["status"] == "PASS"
    assert report["ws"]["status"] == "BLOCKED"
    assert report["ws"]["timeout_diagnostics"]["phase"] == "WS_RECEIVE"
    assert report["ws"]["timeout_diagnostics"]["source"] == "GLOBAL_WORK"
    assert transport.receive_calls == 1
    assert transport.closed


def test_cleanup_timeout_blocks_otherwise_successful_probe() -> None:
    class SlowClose(ScriptedTransport):
        close_started: bool = False

        async def close(self) -> None:
            self.close_started = True
            await asyncio.sleep(10)

    transport = SlowClose()

    report = asyncio.run(
        run_probe(
            transport,
            ProbeLimits(
                total_seconds=3.0,
                connect_seconds=0.03,
                rest_total_seconds=0.2,
                max_messages=1,
            ),
        )
    )

    assert report["rest"]["status"] == "PASS"
    assert report["ws"]["status"] == "PASS"
    assert report["status"] == "BLOCKED"
    assert report["reason"] == "CLEANUP_INCOMPLETE"
    assert report["cleanup"] == "BLOCKED"
    assert transport.close_started is True


def test_save_probe_persists_timeout_limits_and_offline_diagnostics(tmp_path: Path) -> None:
    transport = ScriptedTransport()
    report = asyncio.run(
        run_probe(
            transport,
            ProbeLimits(
                total_seconds=2.0,
                connect_seconds=0.03,
                rest_total_seconds=0.2,
                max_messages=1,
            ),
            evidence_kind="SYNTHETIC",
        )
    )

    save_probe(report, tmp_path / "probe")

    saved_report = json.loads((tmp_path / "probe" / "protocol_probe_report.json").read_text())
    saved_budget = json.loads((tmp_path / "probe" / "endpoint_budget.json").read_text())
    rest_payload = (tmp_path / "probe" / "rest-response.bin").read_bytes()

    assert saved_report["evidence_kind"] == "SYNTHETIC"
    assert saved_report["limits"]["rest_total_seconds"] == 0.2
    assert saved_report["metadata"]["quantity_unit"] == "UNKNOWN"
    assert saved_report["reason"].endswith("continuous budget unverified")
    assert saved_report["rest"]["sha256"] == hashlib.sha256(rest_payload).hexdigest()
    assert saved_budget["evidence_kind"] == "SYNTHETIC"
    assert saved_budget["probe_limits"]["rest_total_seconds"] == 0.2
    assert saved_budget["status"] == "UNVERIFIED_FOR_CONTINUOUS_COLLECTION"
    assert saved_budget["continuous_collection_enabled"] is False


def test_save_probe_persists_blocked_timeout_diagnostics(tmp_path: Path) -> None:
    transport = ScriptedTransport(metadata_delay=1.2)
    report = asyncio.run(
        run_probe(
            transport,
            ProbeLimits(
                total_seconds=2.0,
                connect_seconds=0.05,
                rest_total_seconds=5.0,
                max_messages=1,
            ),
        )
    )

    save_probe(report, tmp_path / "blocked")

    saved_report = json.loads((tmp_path / "blocked" / "protocol_probe_report.json").read_text())
    saved_budget = json.loads((tmp_path / "blocked" / "endpoint_budget.json").read_text())

    assert saved_report["status"] == "BLOCKED"
    assert saved_report["rest"]["status"] == "BLOCKED"
    assert saved_report["ws"]["status"] == "NOT_RUN"
    assert saved_report["rest"]["timeout_diagnostics"] == report["rest"]["timeout_diagnostics"]
    assert saved_report["rest"]["timeout_diagnostics"]["source"] == "GLOBAL_WORK"
    assert saved_report["configured_timeouts"] == report["configured_timeouts"]
    assert saved_report["effective_timeouts"] == report["effective_timeouts"]
    assert saved_budget["probe_limits"]["rest_total_seconds"] == 5.0
    assert saved_budget["status"] == "UNVERIFIED_FOR_CONTINUOUS_COLLECTION"
