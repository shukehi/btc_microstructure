from pathlib import Path
import socket

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def raw_config():
    return yaml.safe_load((ROOT / "config/research.example.yaml").read_text())


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network access forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
