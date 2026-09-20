"""Saved real-material logs over localhost, no inference or external requests."""
import hashlib,json,urllib.request,urllib.error,os
from pathlib import Path
op=urllib.request.build_opener(urllib.request.ProxyHandler({}));base=os.environ.get('JEV_TEST_BASE_URL','http://127.0.0.1:8876');token=None
def call(path,body=None):
    req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json',**({'X-Session-Token':token} if token else {})})
    with op.open(req,timeout=30) as r:return json.load(r)
token=call('/api/bootstrap')['token'];before=call('/api/snapshot')['external'];result=[]
for run in ('run-1789827258430987000','run-1789824695716131300'):
    path=Path(os.environ.get('JEV_TEST_LOG_DIR','artifacts/sessions'))/(run+'.jsonl');digest=hashlib.sha256(path.read_bytes()).hexdigest()
    first=call('/api/replay',{'run_id':run});last=call('/api/replay',{'index':first['count']-1})
    old=run=='run-1789824695716131300'
    assert ('transcripts' not in last['frame'])==old
    if not old:
        assert len(last['frame']['profiles'])==2
        assert all(not p['runtime_verified'] for p in last['frame']['profiles'])
    available=[s for data in last['frame']['snapshot']['spaces'].values() for row in data['hypotheses'] for signs in row['sources'].values() for s in signs]
    if available:
        from urllib.parse import quote
        oid=available[0]['observation_id'];q='/api/evidence/'+quote(oid,safe='')+'?replay_cursor='
        assert call(q+str(last['frame']['replay_event_cursor']))['id']==oid
        try:call(q+str(first['frame']['replay_event_cursor']));raise AssertionError('future replay record leaked')
        except urllib.error.HTTPError as e:assert e.code==404
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    result.append({'run':run,'frames':last['count'],'legacy_missing':old,'file_unchanged':True,'as_of_evidence_checked':bool(available)})
try:call('/api/replay',{'run_id':'../.env'});raise AssertionError('path accepted')
except urllib.error.HTTPError as e:assert e.code==400
after=call('/api/snapshot')['external'];assert after['attempts']==before['attempts'] and after['units']==before['units']
report={'result':'passed','runs':result,'external_sends':0,'budget':after,'browser_operation':False}
(Path(os.environ.get('JEV_TEST_ARTIFACT_DIR','artifacts/live-status-m3a'))/'saved-replay-http.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
