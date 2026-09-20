from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.offline_guard import install
install()
import json,math
from collections import Counter
OUT=ROOT/'artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1'
es=[json.loads(x) for x in (ROOT/'artifacts/sessions/run-1789867504398998900.jsonl').read_text(encoding='utf8').splitlines()]
def quant(v):
    v=sorted(v)
    return {'n':len(v),'p50':v[math.ceil(len(v)*.5)-1] if v else None,'p95':v[math.ceil(len(v)*.95)-1] if v else None}
logs=[e['payload'] for e in es if e['kind']=='dispatcher_result' and e['payload'].get('reservation')]
raws=[json.loads(p.read_text(encoding='utf8')) for p in (ROOT/'artifacts/local-private/cd-display-acceptance-20260920-v1/raw').glob('*.json')]
intervals=[]
for r in raws:
    if r['request_context']['attempt'] not in {x['reservation']['attempt'] for x in logs}:continue
    intervals += [(r['request_context']['sent_wall_s'],1),(r['timings']['received_wall_s'],-1)]
active=peak=0
for _,change in sorted(intervals):active+=change;peak=max(peak,active)
report={'source_run':'run-1789867504398998900','requests':len(logs),'raw_samples':len(intervals)//2,'raw_transport_peak':peak,
    'queue_wait_s':quant([x['queue_wait_s'] for x in logs]),'queue_by_role':{role:quant([x['queue_wait_s'] for x in logs if x['reservation']['role']==role]) for role in ('evidence','dominance')},
    'model_admissions':[{'event_seq':e['event_seq'],**e['payload']} for e in es if e['kind']=='local_model_admitted'],
    'request_roles':dict(Counter(e['payload']['role'] for e in es if e['kind']=='evaluation_requested')),
    'accepted_roles':dict(Counter(x['reservation']['role'] for x in logs if x['status']=='accepted')),
    'record_kinds':dict(Counter(e['payload']['kind'] for e in es if e['kind']=='record')),
    'asr_source_report':'saved-media-http.json contains direct PCM source ranges; see CD report for unadmitted intervals',
    'interpretation':'old observation supply and Jev waiting separated; probability_sum stop is not proof of concurrency bottleneck'}
(OUT/'legacy-analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:report[k] for k in ('requests','raw_samples','raw_transport_peak','queue_wait_s','queue_by_role','request_roles','accepted_roles')}))
