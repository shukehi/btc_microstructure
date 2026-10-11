"""Persist interrupted-body evidence from an explicitly synthetic, socket-free session."""
import asyncio
import json
from pathlib import Path
import sys

import aiohttp
import pytest

from btc_microstructure.probe import ProbeLimits, run_probe, save_probe
from btc_microstructure.probe_transport import AioTransport

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tests/unit'))
from test_probe_interruptions import ScriptedContent, ScriptedResponse, ScriptedSession  # noqa: E402

PREFIX = b'{"synthetic":"partial'
response=ScriptedResponse(content=ScriptedContent([PREFIX], error=aiohttp.ClientPayloadError('SYNTHETIC_BODY_ERROR')))
session=ScriptedSession(response)
with pytest.MonkeyPatch.context() as patch:
    patch.setattr(aiohttp,'ClientSession',lambda **options:session)
    report=asyncio.run(run_probe(AioTransport(),ProbeLimits(total_seconds=2.0)))
report['source']='Handcrafted fake session; no socket access, not a real HTTP response'
assert report['status']=='BLOCKED' and report['bytes_received']==21
assert report['rest']['body_hex']==PREFIX.hex() and report['rest']['body_complete'] is False
output=ROOT/(sys.argv[1] if len(sys.argv)>1 else 'reports/round1/probe_fix/synthetic-interrupted')
save_probe(report,output)
print(json.dumps(dict(evidence_kind='SYNTHETIC',status=report['status'],bytes_received=report['bytes_received'],output=str(output))))
