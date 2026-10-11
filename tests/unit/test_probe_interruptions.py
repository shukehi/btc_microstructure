"""Offline regressions for partial REST evidence captured through AioTransport."""

import asyncio
import hashlib
import json
from pathlib import Path

import aiohttp
import pytest

from btc_microstructure.probe import ProbeLimits, REST_URL, run_probe, save_probe
from btc_microstructure.probe_transport import AioTransport

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/synthetic/probe"


class ScriptedContent:
    def __init__(
        self,
        chunks: list[bytes],
        error: BaseException | None = None,
        pause_before_error: asyncio.Event | None = None,
    ) -> None:
        self.chunks = list(chunks)
        self.error = error
        self.pause_before_error = pause_before_error
        self.read_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if self.chunks:
            return self.chunks.pop(0)[:size]
        if self.pause_before_error is not None:
            self.pause_before_error.set()
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return b""


class ScriptedResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        content: ScriptedContent | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {
            "X-MBX-USED-WEIGHT-1M": "7",
            "Retry-After": "5",
            "Set-Cookie": "SECRET_COOKIE",
            "Server": "example",
        }
        self.content = content or ScriptedContent([])
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "ScriptedResponse":
        self.entered = True
        return self

    async def __aexit__(self, *args: object) -> bool:
        self.exited = True
        return False


class ScriptedSession:
    def __init__(
        self, response: ScriptedResponse | None = None, get_error: Exception | None = None
    ):
        self.response = response
        self.get_error = get_error
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: object) -> ScriptedResponse:
        self.calls.append(("GET", url, kwargs))
        if self.get_error is not None:
            raise self.get_error
        assert self.response is not None
        return self.response

    async def ws_connect(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("WS must not be reached after incomplete REST body")

    async def close(self) -> None:
        self.closed = True


def install_session(monkeypatch: pytest.MonkeyPatch, session: ScriptedSession) -> None:
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: session)


@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ClientPayloadError("SYNTHETIC_SECRET_MUST_NOT_LEAK"),
        TimeoutError("SYNTHETIC_SECRET_MUST_NOT_LEAK"),
    ],
)
def test_partial_rest_body_is_retained_after_read_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    prefix = b'{"symbols":['
    response = ScriptedResponse(content=ScriptedContent([prefix], error=error))
    session = ScriptedSession(response)
    install_session(monkeypatch, session)

    transport = AioTransport()
    report = asyncio.run(run_probe(transport, ProbeLimits(total_seconds=2.0, max_bytes=2048)))
    save_probe(report, tmp_path / "probe")
    saved_report = json.loads((tmp_path / "probe" / "protocol_probe_report.json").read_text())
    payload = (tmp_path / "probe" / "rest-response.bin").read_bytes()

    assert report["schema_version"] == "probe-0.2"
    assert report["status"] == "BLOCKED"
    assert report["rest"]["status"] == "BLOCKED"
    assert report["ws"]["status"] == "NOT_RUN"
    assert report["logical_calls"] == {"rest": 1, "ws": 0}
    assert report["rest"]["http_status"] == 200
    assert report["rest"]["rate_headers"] == {
        "x-mbx-used-weight-1m": "7",
        "retry-after": "5",
    }
    assert "headers_received_ns" in report["rest"]
    assert report["rest"]["body_hex"] == prefix.hex()
    assert report["rest"]["body_bytes_observed"] == len(prefix)
    assert report["rest"]["body_complete"] is False
    assert report["rest"]["body_read_state"] == "INTERRUPTED"
    assert report["rest"]["truncated"] is True
    assert "SYNTHETIC_SECRET_MUST_NOT_LEAK" not in str(report)
    assert "SECRET_COOKIE" not in str(report)
    assert transport.metadata_observation is not None
    assert transport.metadata_observation.body == prefix
    assert transport.metadata_observation.body_complete is False
    assert session.calls == [("GET", REST_URL, {"allow_redirects": False})]
    assert response.entered and response.exited and session.closed
    assert payload == prefix
    assert saved_report["rest"]["sample"] == "rest-response.bin"
    assert saved_report["rest"]["sha256"] == hashlib.sha256(prefix).hexdigest()


def test_parseable_metadata_prefix_still_blocks_ws_when_body_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete_metadata = (FIXTURES / "metadata.valid.json").read_bytes()
    response = ScriptedResponse(
        content=ScriptedContent(
            [complete_metadata],
            error=aiohttp.ClientPayloadError("SYNTHETIC_AFTER_VALID_JSON"),
        )
    )
    session = ScriptedSession(response)
    install_session(monkeypatch, session)

    report = asyncio.run(run_probe(AioTransport(), ProbeLimits(total_seconds=2.0)))

    assert report["status"] == "BLOCKED"
    assert report["reason"] == "REST_NETWORK_ERROR"
    assert report["rest"]["http_status"] == 200
    assert report["rest"]["body_hex"] == complete_metadata.hex()
    assert report["rest"]["body_complete"] is False
    assert report["rest"]["body_read_state"] == "INTERRUPTED"
    assert "metadata" not in report
    assert report["ws"]["status"] == "NOT_RUN"
    assert report["logical_calls"] == {"rest": 1, "ws": 0}
    assert session.closed


@pytest.mark.parametrize("status", [429, 451])
def test_immediate_http_rejection_keeps_headers_without_reading_body(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    body = ScriptedContent([b"do not read"])
    response = ScriptedResponse(status=status, content=body)
    session = ScriptedSession(response)
    install_session(monkeypatch, session)

    report = asyncio.run(run_probe(AioTransport(), ProbeLimits(total_seconds=2.0)))

    assert report["status"] == "BLOCKED"
    assert report["reason"] == f"HTTP_{status}; no retry or WS fallback"
    assert report["rest"]["http_status"] == status
    assert report["rest"]["rate_headers"] == {
        "x-mbx-used-weight-1m": "7",
        "retry-after": "5",
    }
    assert report["rest"]["body_hex"] == ""
    assert report["rest"]["body_bytes_observed"] == 0
    assert report["rest"]["body_complete"] is False
    assert report["rest"]["body_read_state"] == "NOT_READ_HTTP_REJECTION"
    assert report["rest"]["truncated"] is True
    assert body.read_sizes == []
    assert report["ws"]["status"] == "NOT_RUN"
    assert session.closed


def test_pre_response_error_does_not_invent_headers_or_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = ScriptedSession(
        get_error=aiohttp.ClientConnectionError("SYNTHETIC_SECRET_MUST_NOT_LEAK")
    )
    install_session(monkeypatch, session)

    report = asyncio.run(run_probe(AioTransport(), ProbeLimits(total_seconds=2.0)))

    assert report["status"] == "BLOCKED"
    assert report["reason"] == "REST_NETWORK_ERROR"
    assert report["rest"]["status"] == "BLOCKED"
    assert "http_status" not in report["rest"]
    assert "headers_received_ns" not in report["rest"]
    assert "body_hex" not in report["rest"]
    assert "SYNTHETIC_SECRET_MUST_NOT_LEAK" not in str(report)
    assert session.calls == [("GET", REST_URL, {"allow_redirects": False})]
    assert session.closed


def test_body_limit_retains_cap_plus_one_and_marks_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"x" * 2049
    response = ScriptedResponse(content=ScriptedContent([payload]))
    session = ScriptedSession(response)
    install_session(monkeypatch, session)

    report = asyncio.run(run_probe(AioTransport(), ProbeLimits(total_seconds=2.0, max_bytes=2048)))

    assert report["status"] == "FAIL"
    assert report["reason"] == "rest: response byte limit exceeded"
    assert report["rest"]["body_hex"] == payload[:2048].hex()
    assert report["rest"]["body_bytes_observed"] == 2049
    assert report["rest"]["body_complete"] is False
    assert report["rest"]["body_read_state"] == "BYTE_LIMIT"
    assert report["rest"]["truncated"] is True
    assert response.content.read_sizes == [2049]
    assert session.closed


def test_external_cancellation_propagates_after_snapshot_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        prefix = b'{"partial":'
        stalled = asyncio.Event()
        response = ScriptedResponse(
            content=ScriptedContent(
                [prefix],
                error=asyncio.CancelledError(),
                pause_before_error=stalled,
            )
        )
        session = ScriptedSession(response)
        install_session(monkeypatch, session)
        transport = AioTransport()

        task = asyncio.create_task(run_probe(transport, ProbeLimits(total_seconds=2.0)))
        await asyncio.wait_for(stalled.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        observation = transport.metadata_observation
        assert observation is not None
        assert observation.status == 200
        assert observation.body == prefix
        assert observation.body_complete is False
        assert observation.body_read_state == "INTERRUPTED"
        assert observation.headers_received_ns is not None
        assert session.closed
        assert response.exited

    asyncio.run(check())


def test_total_deadline_keeps_partial_body_and_observed_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        prefix = b'{"partial":"total-deadline'
        response = ScriptedResponse(
            content=ScriptedContent([prefix], pause_before_error=asyncio.Event())
        )
        session = ScriptedSession(response)
        install_session(monkeypatch, session)
        report = await run_probe(
            AioTransport(), ProbeLimits(total_seconds=2.0, connect_seconds=10.0, idle_seconds=10.0)
        )
        assert report["status"] == "BLOCKED"
        assert report["reason"] == "TIMEOUT; no retry"
        assert report["rest"]["http_status"] == 200
        assert report["rest"]["body_hex"] == prefix.hex()
        assert report["rest"]["body_complete"] is False
        assert report["rest"]["body_read_state"] == "INTERRUPTED"
        assert report["bytes_received"] == len(prefix)
        assert report["rest"]["headers_received_ns"] <= report["ended_ns"]
        assert report["ws"]["status"] == "NOT_RUN"
        assert report["elapsed_seconds"] < 2.0
        assert session.closed and response.exited

    asyncio.run(check())
