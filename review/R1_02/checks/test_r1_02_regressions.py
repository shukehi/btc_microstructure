"""R1-02 independent regression expectations. All sources are synthetic; no sockets.

Run from the project root:
  PYTHONPATH=src python -m pytest -q <path-to-this-file>

The retry tests keep the real aiohttp request retry loop and inject an error
through its public middleware constructor option. They do NOT contact Binance.
"""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from btc_microstructure.probe import ProbeLimits, Reply, run_probe, save_probe
from btc_microstructure.probe_transport import AioTransport

ROOT = Path.cwd()
FIXTURES = ROOT / 'fixtures/synthetic/probe'
PREFIX = b'{"synthetic":"partial'

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('External network forbidden in independent regression tests')
    monkeypatch.setattr('socket.socket.connect', denied)
    monkeypatch.setattr('socket.socket.connect_ex', denied)
    monkeypatch.setattr('socket.getaddrinfo', denied)


def short_limits():
    return ProbeLimits(total_seconds=2.0, connect_seconds=0.03,
                       idle_seconds=0.03, max_messages=1)


@pytest.mark.parametrize('stage', ['rest', 'ws'])
def test_no_hidden_second_attempt_on_disconnect(monkeypatch, stage):
    """R102-A: no application retries must also mean no implicit client retry."""
    attempts = []
    original = aiohttp.ClientSession

    async def disconnect(request, handler):
        attempts.append((request.method, str(request.url)))
        raise aiohttp.ServerDisconnectedError('SYNTHETIC: interrupted before response')

    def factory(**options):
        middlewares = tuple(options.pop('middlewares', ()))
        return original(**options, middlewares=middlewares + (disconnect,))

    class WSOnly(AioTransport):
        async def metadata(self, limits):
            return Reply(200, {}, (FIXTURES / 'metadata.valid.json').read_bytes())

    monkeypatch.setattr(aiohttp, 'ClientSession', factory)
    transport = AioTransport() if stage == 'rest' else WSOnly()
    report = asyncio.run(run_probe(transport, short_limits()))
    assert report['status'] == 'BLOCKED'
    assert len(attempts) == 1, (
        f'{stage}: client entered request middleware {len(attempts)} times, '
        f"yet report says retry_count={report['retry_count']}; aiohttp={aiohttp.__version__}"
    )


def interrupted_transport(monkeypatch, status, failure):
    class Content:
        def __init__(self):
            self.read_count = 0
        async def read(self, size):
            self.read_count += 1
            if self.read_count == 1:
                return PREFIX
            if failure == 'timeout':
                await asyncio.sleep(2)
            raise aiohttp.ClientPayloadError('SYNTHETIC: body interrupted')

    response = SimpleNamespace(
        status=status, headers={'Retry-After': '60', 'X-MBX-USED-WEIGHT-1M': '7',
                                'Set-Cookie': 'SYNTHETIC_MUST_NOT_APPEAR'},
        content=Content())
    class ResponseContext:
        async def __aenter__(self):
            return response
        async def __aexit__(self, *args):
            return False
    class Session:
        def __init__(self, **options):
            self.closed = False
        def get(self, *args, **kwargs):
            return ResponseContext()
        async def ws_connect(self, *args, **kwargs):
            raise AssertionError('No WS fallback after REST failure')
        async def close(self):
            self.closed = True
    monkeypatch.setattr(aiohttp, 'ClientSession', Session)
    return AioTransport()


@pytest.mark.parametrize('status', [429, 451])
@pytest.mark.parametrize('failure', ['timeout', 'payload_error'])
def test_observed_status_and_budget_headers_survive_body_failure(monkeypatch, status, failure):
    """R102-B: already observed response headers cannot disappear on read failure."""
    transport = interrupted_transport(monkeypatch, status, failure)
    report = asyncio.run(run_probe(transport, short_limits()))
    assert report['status'] == 'BLOCKED'
    assert report['ws']['status'] == 'NOT_RUN'
    assert report['rest'].get('http_status') == status, (
        f'already delivered HTTP {status} became {report["rest"]!r}'
    )
    assert report['rest']['rate_headers']['retry-after'] == '60'
    assert report['rest']['rate_headers']['x-mbx-used-weight-1m'] == '7'
    assert 'SYNTHETIC_MUST_NOT_APPEAR' not in json.dumps(report)


def test_partial_200_body_is_retained_as_incomplete_evidence(monkeypatch, tmp_path):
    """R102-B: preserve delivered application bytes, not imaginary wire totals."""
    transport = interrupted_transport(monkeypatch, 200, 'timeout')
    report = asyncio.run(run_probe(transport, short_limits()))
    assert report['status'] != 'PASS'
    assert report['rest'].get('body_hex') == PREFIX.hex(), (
        '21 bytes were delivered to metadata() but the evidence lost the prefix'
    )
    assert report['rest']['truncated'] is True
    assert report['bytes_received'] >= len(PREFIX)
    output = tmp_path / 'partial'
    save_probe(report, output)
    saved = json.loads((output / 'protocol_probe_report.json').read_text())
    sample = output / saved['rest']['sample']
    assert sample.read_bytes() == PREFIX
    assert saved['rest']['sha256'] == hashlib.sha256(PREFIX).hexdigest()
    assert saved['ws']['status'] == 'NOT_RUN'


def test_control_complete_denial_is_recorded():
    """Passing control: old tests only return a fully constructed Reply."""
    class CompleteDenied:
        async def metadata(self, limits):
            return Reply(429, {'Retry-After': '60'}, b'{"synthetic":"denied"}')
        async def connect(self, limits):
            raise AssertionError('Must not connect')
        async def close(self):
            pass
    report = asyncio.run(run_probe(CompleteDenied(), short_limits()))
    assert report['rest']['http_status'] == 429
    assert report['rest']['rate_headers']['retry-after'] == '60'
    assert report['status'] == 'BLOCKED'


def test_control_complete_synthetic_success_preserves_hashes(tmp_path):
    class Complete:
        async def metadata(self, limits):
            return Reply(200, {}, (FIXTURES / 'metadata.valid.json').read_bytes())
        async def connect(self, limits):
            return Reply(101, {})
        async def receive(self):
            return (FIXTURES / 'aggtrade.combined.json').read_bytes()
        async def close(self):
            pass
    report = asyncio.run(run_probe(Complete(), short_limits()))
    assert report['status'] == 'PASS'
    assert report['evidence_kind'] == 'SYNTHETIC'
    assert report['continuous_collection_enabled'] is False
    save_probe(report, tmp_path / 'complete')
    saved = json.loads((tmp_path / 'complete/protocol_probe_report.json').read_text())
    sample = tmp_path / 'complete' / saved['ws']['messages'][0]['sample']
    assert saved['ws']['messages'][0]['sha256'] == hashlib.sha256(sample.read_bytes()).hexdigest()
