"""Synthetic transport contract and explicit network gate; no sockets are used."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
import yaml

from btc_microstructure.cli import main
from btc_microstructure.probe import AccessError, ProbeLimits, Reply, REST_URL, WS_URL
from btc_microstructure.probe_transport import AioTransport

ROOT = Path(__file__).resolve().parents[2]


class FakeContent:
    def __init__(self, data):
        self.data = data
        self.read_sizes = []

    async def read(self, size):
        self.read_sizes.append(size)
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk


class FakeResponse:
    status = 200
    headers = {"X-MBX-USED-WEIGHT-1M": "1"}

    def __init__(self, data):
        self.content = FakeContent(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeWS:
    async def receive(self):
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data='{"synthetic":true}')

    async def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, **options):
        self.options = options
        self.response = FakeResponse(b"x" * 5000)
        self.calls = []
        self.socket = FakeWS()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    async def ws_connect(self, url, **kwargs):
        self.calls.append(("WS", url, kwargs))
        return self.socket

    async def close(self):
        self.closed = True


def test_live_adapter_limits_and_fixed_scope(monkeypatch):
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)

    async def check():
        transport = AioTransport()
        assert transport.session is None
        limits = ProbeLimits(max_bytes=2048)
        response = await transport.metadata(limits)
        session = transport.session
        assert response.status == 200
        assert len(response.body) == 2049  # one overflow-detection byte, never full body
        assert session.options["trust_env"] is False
        assert isinstance(session.options["cookie_jar"], aiohttp.DummyCookieJar)
        assert session.options["headers"] == {"Accept-Encoding": "identity"}
        assert session.options["timeout"].connect == 5
        assert session.calls[0] == ("GET", REST_URL, {"allow_redirects": False})
        handshake = await transport.connect(limits)
        assert handshake.status == 101
        kind, url, options = session.calls[1]
        assert kind == "WS" and url == WS_URL
        assert options["max_msg_size"] == 65536
        assert options["autoping"] is True
        assert options["compress"] == 0
        assert options["timeout"].ws_close == 0.5
        assert await transport.receive() == b'{"synthetic":true}'
        trace = session.options["trace_configs"][0]
        callback = trace.on_request_redirect[0]
        with pytest.raises(AccessError, match="REDIRECT_REJECTED") as caught:
            await callback(
                session, None, SimpleNamespace(response=SimpleNamespace(status=302, headers={}))
            )
        assert caught.value.reply.status == 302
        await transport.close()
        assert session.closed and session.socket.closed

    asyncio.run(check())


@pytest.mark.parametrize(
    "message_type", [aiohttp.WSMsgType.BINARY, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR]
)
def test_ws_nontext_terminates(message_type):
    class Socket:
        async def receive(self):
            return SimpleNamespace(type=message_type, data=b"not-accepted")

    transport = AioTransport()
    transport.ws = Socket()
    with pytest.raises(AccessError, match="WS_CLOSED_BINARY_OR_PROTOCOL_ERROR"):
        asyncio.run(transport.receive())


def test_handshake_denial_is_observed_not_retried(monkeypatch):
    class Denied(FakeSession):
        async def ws_connect(self, url, **kwargs):
            raise aiohttp.WSServerHandshakeError(
                request_info=SimpleNamespace(real_url=WS_URL),
                history=(),
                status=451,
                message="synthetic denial",
                headers={"Retry-After": "60"},
            )

    monkeypatch.setattr(aiohttp, "ClientSession", Denied)

    async def check():
        transport = AioTransport()
        with pytest.raises(AccessError) as caught:
            await transport.connect(ProbeLimits())
        assert caught.value.reason == "WS_HANDSHAKE_REJECTED"
        assert caught.value.reply.status == 451
        assert caught.value.reply.headers["Retry-After"] == "60"
        await transport.close()

    asyncio.run(check())


def test_network_error_details_are_not_exposed(monkeypatch):
    class Failed(FakeSession):
        def get(self, url, **kwargs):
            raise aiohttp.ClientConnectionError("SYNTHETIC_ENV_SENTINEL")

    monkeypatch.setattr(aiohttp, "ClientSession", Failed)

    async def check():
        transport = AioTransport()
        with pytest.raises(AccessError, match="REST_NETWORK_ERROR") as caught:
            await transport.metadata(ProbeLimits())
        assert "SYNTHETIC_ENV_SENTINEL" not in str(caught.value)
        await transport.close()

    asyncio.run(check())


def test_cli_network_requires_flag(tmp_path, monkeypatch, capsys):
    def denied():
        raise AssertionError("transport must not be constructed without explicit opt-in")

    monkeypatch.setattr("btc_microstructure.probe_transport.AioTransport", denied)
    output = tmp_path / "disabled"
    assert main(["probe", str(ROOT / "config/research.example.yaml"), "--output", str(output)]) == 2
    assert "NOT_RUN" in capsys.readouterr().out
    assert '"status": "NOT_RUN"' in (output / "protocol_probe_report.json").read_text()


def test_cli_flagged_denial_creates_live_blocked_evidence(tmp_path, monkeypatch, capsys):
    class Denied:
        async def metadata(self, limits):
            return Reply(451, {"Retry-After": "60"}, b'{"synthetic":"denial"}')

        async def connect(self, limits):
            raise AssertionError("no WS fallback after access restriction")

        async def close(self):
            pass

    monkeypatch.setattr("btc_microstructure.probe_transport.AioTransport", Denied)
    output = tmp_path / "blocked"
    assert (
        main(
            [
                "probe",
                str(ROOT / "config/research.example.yaml"),
                "--output",
                str(output),
                "--allow-network",
            ]
        )
        == 1
    )
    assert "BLOCKED" in capsys.readouterr().out
    assert (output / "rest-response.bin").read_bytes() == b'{"synthetic":"denial"}'
    assert (output / "endpoint_budget.json").exists()
    assert (
        main(
            [
                "probe",
                str(ROOT / "config/research.example.yaml"),
                "--output",
                str(output),
                "--allow-network",
            ]
        )
        == 1
    )
    assert "refusing to overwrite" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("option", "value", "field"),
    [
        ("--seconds", "61", "total_seconds"),
        ("--seconds", "nan", "total_seconds"),
        ("--messages", "21", "max_messages"),
        ("--max-bytes", "999999999", "max_bytes"),
    ],
)
def test_cli_hard_caps_before_transport(tmp_path, monkeypatch, capsys, option, value, field):
    def denied():
        raise AssertionError("must reject over-limit input before network")

    monkeypatch.setattr("btc_microstructure.probe_transport.AioTransport", denied)
    assert (
        main(
            [
                "probe",
                str(ROOT / "config/research.example.yaml"),
                "--output",
                str(tmp_path / "x"),
                "--allow-network",
                option,
                value,
            ]
        )
        == 1
    )
    assert field in capsys.readouterr().err


def test_probe_rejects_custom_routes_before_network(tmp_path, raw_config, monkeypatch, capsys):
    raw_config["endpoints"]["um_rest"] = "https://example.com"
    source = tmp_path / "config.yaml"
    source.write_text(yaml.safe_dump(raw_config))
    assert main(["probe", str(source), "--output", str(tmp_path / "out"), "--allow-network"]) == 1
    assert "endpoints.um_rest/um_market_ws" in capsys.readouterr().err
