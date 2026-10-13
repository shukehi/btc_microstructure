"""Deterministic synthetic clock/transport regressions for R203-CI-01."""

import asyncio
from typing import Any

import aiohttp
import pytest

from btc_microstructure.probe import ProbeLimits, REST_URL, run_probe
from btc_microstructure.probe_transport import AioTransport


class RejectedResponse:
    status = 451
    headers: dict[str, str] = {}

    async def __aenter__(self) -> "RejectedResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class CaptureSession:
    def __init__(self, **options: Any) -> None:
        self.options = options
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, url: str, **options: Any) -> RejectedResponse:
        self.calls.append((url, options))
        return RejectedResponse()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "start,rest_start,total,configured,expected,cap_source",
    [
        (49.9, 49.9, 60.0, 15.0, 15.0, "REST_TOTAL"),
        (49.9, 49.9, 60.0, 20.0, 20.0, "REST_TOTAL"),
        (0.1, 0.1, 60.0, 15.0, 15.0, "REST_TOTAL"),
        (50.0, 50.0, 60.0, 15.0, 15.0, "REST_TOTAL"),
        (49.9, 49.9, 2.0, 15.0, 1.0, "GLOBAL_WORK"),
        (49.9, 51.9, 2.0, 20.0, 0.0, "GLOBAL_WORK"),
        (50.0, 50.0, 16.0, 15.0, 15.0, "SHARED_DEADLINE"),
    ],
)
def test_rest_duration_and_actual_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
    start: float,
    rest_start: float,
    total: float,
    configured: float,
    expected: float,
    cap_source: str,
) -> None:
    session = CaptureSession()
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: session)
    deadlines: list[float | None] = []
    original_timeout_at = asyncio.timeout_at

    def capture_deadline(when: float | None) -> asyncio.Timeout:
        deadlines.append(when)
        return original_timeout_at(when)

    monkeypatch.setattr(asyncio, "timeout_at", capture_deadline)
    limits = ProbeLimits(total_seconds=total, rest_total_seconds=configured)

    async def check() -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        clock = iter([start, rest_start])
        # Immediate synthetic rejection avoids wall-clock waits/network I/O.
        # Restore the real loop clock before asyncio.run shuts down the loop.
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(loop, "time", lambda: next(clock, rest_start))
            return await run_probe(AioTransport(), limits)

    report = asyncio.run(check())

    assert report["effective_timeouts"]["rest_total_seconds"] == expected
    assert report["effective_timeouts"]["rest_total_cap_source"] == cap_source
    assert report["configured_timeouts"]["rest_total_seconds"] == configured
    assert limits.rest_total_seconds == configured
    assert len(session.calls) == 1
    url, options = session.calls[0]
    assert url == REST_URL
    assert options["allow_redirects"] is False
    timeout = options["timeout"]
    assert isinstance(timeout, aiohttp.ClientTimeout)
    assert timeout.total == expected
    assert 0.0 <= timeout.total <= configured
    assert timeout.connect == limits.connect_seconds
    assert timeout.sock_connect == limits.connect_seconds
    assert timeout.sock_read == limits.idle_seconds
    assert deadlines == [start + total - 1, min(start + total - 1, rest_start + configured)]
    assert report["status"] == "BLOCKED"
    assert report["ws"]["status"] == "NOT_RUN"
    assert session.closed


@pytest.mark.parametrize("total,expected", [(60.0, 5.0), (2.0, 1.0)])
def test_omitted_rest_timeout_keeps_legacy_request_shape(
    monkeypatch: pytest.MonkeyPatch, total: float, expected: float
) -> None:
    sessions: list[CaptureSession] = []

    def make_session(**options: Any) -> CaptureSession:
        session = CaptureSession(**options)
        sessions.append(session)
        return session

    monkeypatch.setattr(aiohttp, "ClientSession", make_session)

    async def check() -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(loop, "time", lambda: 50.0)
            return await run_probe(AioTransport(), ProbeLimits(total_seconds=total))

    report = asyncio.run(check())

    assert report["effective_timeouts"]["rest_total_seconds"] == expected
    assert report["effective_timeouts"]["rest_total_mode"] == "FALLBACK_CONNECT"
    assert report["configured_timeouts"]["rest_total_seconds"] is None
    assert sessions[0].calls == [(REST_URL, {"allow_redirects": False})]
    assert sessions[0].options["timeout"].total == 5.0
    assert sessions[0].closed
