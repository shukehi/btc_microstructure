"""Save one bounded, explicitly SYNTHETIC inspection; never instantiate live transport."""
import asyncio
from pathlib import Path
import sys

from btc_microstructure.probe import ProbeLimits, Reply, run_probe, save_probe

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'fixtures/synthetic/probe'


class SyntheticTransport:
    async def metadata(self, limits: ProbeLimits) -> Reply:
        return Reply(200, {'X-MBX-USED-WEIGHT-1M': '1'}, (FIXTURES/'metadata.valid.json').read_bytes())

    async def connect(self, limits: ProbeLimits) -> Reply:
        return Reply(101, {})

    async def receive(self) -> bytes:
        return (FIXTURES/'aggtrade.combined.json').read_bytes()

    async def close(self) -> None:
        pass


limits = ProbeLimits(total_seconds=2.0, max_messages=2, max_bytes=4096, max_frame_bytes=512)
report = asyncio.run(run_probe(SyntheticTransport(), limits, evidence_kind='SYNTHETIC'))
report['source'] = 'Hand-authored fixtures/synthetic/probe; not an exchange response'
output = ROOT / (sys.argv[1] if len(sys.argv)>1 else 'reports/round1/probe/synthetic-run')
save_probe(report, output)
print(report['status'], report['evidence_kind'], 'raw samples and hashes saved')
raise SystemExit(0 if report['status']=='PASS' else 1)
