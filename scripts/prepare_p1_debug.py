"""Prepare existing saved runs on the already-started non-LIVE Viewer. Local only."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from context_fields.debug_server import install_readonly_guard
install_readonly_guard()
import json
import time
import urllib.request
from urllib.parse import urlencode

root=Path(__file__).resolve().parents[1]
base='http://127.0.0.1:8876'
with urllib.request.urlopen(base+'/api/bootstrap') as response:
    boot=json.load(response)
assert boot['debug'] and not boot['live_enabled']
def call(path, body=None):
    request=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),
        headers={'X-Session-Token':boot['token'],'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)
report={'debug':True,'live_enabled':False,'build':boot['build'],'browser_operation':False,'runs':[]}
out=root/'artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1'
new_runs=[json.loads((out/(n+'.json')).read_text(encoding='utf8'))['run_id'] for n in ('measurement-result','fixture')]
for run in new_runs+['run-1789867504398998900','run-1789828491134742200']:
    call('/api/debug/index',{'run':run})
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        status=call('/api/debug/status?run='+run)
        if status['status']!='indexing':break
        time.sleep(.2)
    assert status['status']=='ready',status
    asset=call('/api/debug/managed-asset',{'run':run})
    match=call('/api/debug/match?'+urlencode({'run':run,'asset_id':asset['id']}))
    assert match['media_match']=='hash_verified'
    data=call('/api/debug/evaluation-lists?'+urlencode({'run':run,'position':status['activity']['duration']}))
    assert 'logs' in data and 'evaluations' in data
    report['runs'].append({'run':run,'events':status['events'],'sha256':status['sha256'],'asset_id':asset['id'],
        'counts':{p:data[p]['total'] for p in ('evaluations','logs')},
        'evaluation_arrivals':[{'row_id':x['row_id'],'seconds':x['available_at'],'state':x['evaluation_state']} for x in data['evaluations']['items']]})
(out/'prepared.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'prepared':[x['run'] for x in report['runs']],'debug':True,'live_enabled':False}))
