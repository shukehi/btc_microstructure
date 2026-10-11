"""Offline real aiohttp request-loop injection; socket access is blocked by conftest."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from btc_microstructure.probe import AccessError, ProbeLimits, Reply, REST_URL, WS_URL, run_probe
from btc_microstructure.probe_transport import AioTransport

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/synthetic/probe"


@pytest.mark.parametrize("stage", ["rest", "ws"])
@pytest.mark.parametrize("error_type", [aiohttp.ServerDisconnectedError, aiohttp.ClientOSError])
def test_client_retry_loop_never_reenters_sending_handler(monkeypatch, stage, error_type):
    original_session = aiohttp.ClientSession
    entered = []

    async def disconnect(request, handler):
        entered.append((request.method, str(request.url)))
        raise error_type("SYNTHETIC_EXCEPTION_MUST_NOT_LEAK")

    def factory(**options):
        return original_session(**{**options, "middlewares": (*options["middlewares"], disconnect)})

    class WSOnly(AioTransport):
        async def metadata(self, limits):
            return Reply(200, {}, (FIXTURES / "metadata.valid.json").read_bytes())

    monkeypatch.setattr(aiohttp, "ClientSession", factory)
    transport = AioTransport() if stage == "rest" else WSOnly()
    report = asyncio.run(run_probe(transport, ProbeLimits(total_seconds=2.0)))
    assert report["status"] == "BLOCKED"
    assert entered == [("GET", REST_URL if stage == "rest" else WS_URL)]
    assert report["transport_attempts"][stage] == 1
    assert report["logical_calls"][stage] == 1
    assert report["retry_count"] == 0
    assert report["retry_count_scope"] == "APPLICATION_AND_REQUEST_HANDLER"
    assert report["blocked_retry_count"] == 0  # exception translation already stops aiohttp's retry
    assert "SYNTHETIC_EXCEPTION_MUST_NOT_LEAK" not in str(report)
    assert transport.session.closed


@pytest.mark.parametrize("url,stage", [(REST_URL, "rest"), (WS_URL, "ws")])
def test_second_middleware_entry_is_rejected_before_downstream_handler(url, stage):
    transport = AioTransport()
    calls = []

    async def handler(request):
        calls.append(request)
        return SimpleNamespace(status=200)

    request = SimpleNamespace(method="GET", url=url)

    async def check():
        await transport._single_attempt(request, handler)
        with pytest.raises(AccessError, match="IMPLICIT_RETRY_BLOCKED"):
            await transport._single_attempt(request, handler)

    asyncio.run(check())
    assert len(calls) == 1
    assert transport.request_attempts[stage] == 1
    assert transport.blocked_reentries == 1
    counts = transport.request_attempts
    counts[stage] = 99
    assert transport.request_attempts[stage] == 1


def test_attempt_guard_rejects_out_of_scope_before_handler():
    transport = AioTransport()

    async def handler(request):
        raise AssertionError("must not enter unexpected request handler")

    request = SimpleNamespace(method="POST", url=REST_URL)
    with pytest.raises(AccessError, match="UNEXPECTED_REQUEST_SCOPE"):
        asyncio.run(transport._single_attempt(request, handler))
    assert transport.request_attempts == {"rest": 0, "ws": 0}


def test_legacy_transport_attempt_visibility_is_not_claimed():
    class Legacy:
        async def metadata(self, limits):
            return Reply(429, {})

        async def close(self):
            pass

    report = asyncio.run(run_probe(Legacy(), ProbeLimits()))
    assert report["status"] == "BLOCKED"
    assert report["logical_calls"] == {"rest": 1, "ws": 0}
    assert report["transport_attempts"] is None
    assert report["retry_count_scope"] == "APPLICATION_ONLY_TRANSPORT_UNOBSERVED"
