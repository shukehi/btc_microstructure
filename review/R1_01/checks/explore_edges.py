from pathlib import Path
from copy import deepcopy
from decimal import Decimal
import json, subprocess, os, tempfile
import yaml
from btc_microstructure.config_models import ResearchConfig
from btc_microstructure.config import config_digest
from pydantic import ValidationError
root=Path('/mnt/data/foundation_audit/project')
raw=yaml.safe_load((root/'config/research.example.yaml').read_text())
def changed(path,value):
 data=deepcopy(raw); bits=path.split('.'); n=data
 for key in bits[:-1]:n=n[key]
 n[bits[-1]]=value
 return data
cases=[
 ('storage.source_payload_preserved',False),
 ('quality.unknown_as_null',False),
 ('quality.known_gap_invalidates_flow_window',False),
 ('quality.require_complete_book_price_band',False),
 ('quality.clock_uncertain_disables_lead_lag',False),
 ('states.freeze_thresholds_in_validation_and_test',False),
 ('states.allow_unknown',False),
 ('research.report_all_horizons',False),
 ('research.report_common_event_sample',False),
 ('research.split.purge_by_actual_label_intervals',False),
 ('research.horizon_seconds',[86400]),
 ('research.split.minimum_boundary_buffer_seconds',1),
 ('research.inference.block_bootstrap_min_block_seconds',1),
 ('features.settled_funding_z_same_interval_only',False),
 ('features.rolling_std_ddof',30),
 ('collector.protocol_probe_required',False),
 ('streams.um_market',['not-a-stream']),
 ('endpoints.um_rest','https://127.0.0.1'),
]
results=[]
for p,v in cases:
 try:
  c=ResearchConfig.model_validate(changed(p,v)); outcome='ACCEPTED'
 except ValidationError as e:outcome='REJECTED'
 results.append({'field':p,'value':v,'outcome':outcome})
print(json.dumps(results,indent=2,ensure_ascii=False))
(root.parent/'evidence/edge-observations.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
# Deliberately short-lived isolated Git repository, not the uploaded project.
with tempfile.TemporaryDirectory(prefix='ignore-check-',dir=root.parent) as temp:
 p=Path(temp); (p/'.gitignore').write_bytes((root/'.gitignore').read_bytes())
 subprocess.run(['git','init','-q',temp],check=True,capture_output=True)
 paths=['.env','.env.production','private.key','certificate.pem','.venv/file','data/file','codex-local-logs/session.log']
 proc=subprocess.run(['git','-C',temp,'check-ignore','--stdin'],input='\n'.join(paths)+'\n',text=True,capture_output=True)
 result={'paths':paths,'ignored':proc.stdout.splitlines(),'not_ignored':[x for x in paths if x not in proc.stdout.splitlines()],'exit_code':proc.returncode}
 print('GIT_IGNORE',json.dumps(result)); (root.parent/'evidence/ignore-observations.json').write_text(json.dumps(result,indent=2))
# In-memory mutability only; never written to the uploaded config.
c=ResearchConfig.model_validate(raw)
try:c.collector.reconnect_initial_seconds=1000
except ValidationError: print('ASSIGNMENT_RAISED=True')
print('STATE_AFTER_ASSIGNMENT_ERROR',c.collector.reconnect_initial_seconds,c.collector.reconnect_max_seconds)
c=ResearchConfig.model_validate(raw); c.research.horizon_seconds.append(-5)
print('LIST_MUTATION_ACCEPTED',c.research.horizon_seconds)
