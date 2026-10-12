"""Synthetic old-default reference: actual AioTransport reader, no socket."""

import asyncio
import json
from pathlib import Path
import socket
from unittest.mock import patch

import aiohttp
from btc_microstructure.probe import ProbeLimits, run_probe
from btc_microstructure.probe_transport import AioTransport


def forbidden(*args, **kwargs):
    raise AssertionError("offline contrast forbids sockets")


async def check():
    payload = Path("fixtures/synthetic/probe/metadata.valid.json").read_bytes()
    chunks = [payload[i : i + 100] for i in range(0, len(payload), 100)]
    options = {}

    class Content:
        async def read(self, size):
            await asyncio.sleep(0.03)
            return chunks.pop(0) if chunks else b""

    class Response:
        status = 200
        headers = {"X-MBX-USED-WEIGHT-1M": "7"}
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Session:
        def __init__(self, **kwargs):
            options.update(kwargs)

        def get(self, *args, **kwargs):
            return Response()

        async def close(self):
            pass

        async def ws_connect(self, *args, **kwargs):
            raise AssertionError("incomplete REST must not start WS")

    with patch.object(aiohttp, "ClientSession", Session):
        result = await run_probe(
            AioTransport(), ProbeLimits(total_seconds=2.0, connect_seconds=0.06, idle_seconds=0.05)
        )
    observed = {
        "evidence_kind": "SYNTHETIC",
        "reference_code": "fd4fbd73fc6a7c0c2a4cff77ba97492dfdc56525",
        "scale": "legacy REST/connection0.06s; chunk interval0.03s<idle0.05s; complete body requires >0.06s",
        "http_session_total": options["timeout"].total,
        "status": result["status"],
        "reason": result["reason"],
        "rest": {k: v for k, v in result["rest"].items() if k != "body_hex"},
        "bytes_received": result["bytes_received"],
        "logical_calls": result["logical_calls"],
        "elapsed_seconds": result["elapsed_seconds"],
        "cleanup": result["cleanup"],
    }
    print(json.dumps(observed, indent=2))
    assert result["status"] == "BLOCKED" and result["ws"]["status"] == "NOT_RUN"
    assert result["rest"]["http_status"] == 200 and result["rest"]["body_complete"] is False
    assert options["timeout"].total == 0.06
    assert result["logical_calls"] == {"rest": 1, "ws": 0}


if __name__ == "__main__":
    for name in ["create_connection", "getaddrinfo"]:
        setattr(socket, name, forbidden)
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    asyncio.run(check())
