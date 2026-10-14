import json
from pathlib import Path
import socket
import sys
import time
sys.path.insert(0, str(Path('tests/unit').resolve()))
def forbidden(*args, **kwargs):
    raise AssertionError('network forbidden in offline pressure sample')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
from bounded_fakes import FakeClock, FakeTransport, agg_trade
from test_bounded_live import bounded_config, run_controller
from btc_microstructure.bounded_transport import WsEvent
from btc_microstructure.offline_core import NS

if __name__ == '__main__':
    clock=FakeClock()
    started=clock.wall_ns()
    events=[(started+i*1_000_000,WsEvent('message',agg_trade(agg_id=i+1,quantity='0.001',buyer_is_maker=bool(i%2)))) for i in range(1000)]
    events.append((started+NS,WsEvent('disconnect')))
    class TimedTransport(FakeTransport):
        async def connect(self):
            self.real_observation_start = time.perf_counter()
            await super().connect()
    transport=TimedTransport(clock, events)
    config=bounded_config(Path('data/r204-pressure-reproduction'),observation_seconds=1,max_messages=1000)
    result=run_controller(config,transport,clock)
    elapsed=time.perf_counter()-transport.real_observation_start
    result['fixture_provenance']='TEST_ONLY manually generated 1000 aggTrade messages'
    result['virtual_input_span_ns']=999_000_000
    result['real_elapsed_seconds_including_cleanup']=elapsed
    result['physical_market_transport']='NOT_IMPLEMENTED'
    result['network_access']='BLOCKED_BY_SOCKET_GUARD'
    Path('data/r204-pressure-reproduction-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True))
