"""M3a HTTP contract check, synthetic session only, zero external sends."""
import json
import os
import urllib.request
import urllib.error
from pathlib import Path

op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
base=os.environ.get('JEV_TEST_BASE_URL','http://127.0.0.1:8876');token=None;checks=0
def call(path,body=None):
    req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
         headers={'Content-Type':'application/json',**({'X-Session-Token':token} if token else {})})
    with op.open(req,timeout=10) as r:return json.load(r)
def check(x):
    global checks
    assert x;checks+=1
token=call('/api/bootstrap')['token']
before=call('/api/snapshot')['external']['attempts']
s=call('/api/session',{'mode':'MOCK'});epoch=s['clock']['epoch']
check(s['snapshot']['schema_version']=='3')
check(s['transcripts']['current']==[] and s['profiles']==[])
check(set(s['unknown_triage']['spaces'])=={'person','place','conversation'})
s=call('/api/clock',{'epoch':epoch,'seq':0,'media_s':0,'video_presented_s':0,'state':'playing'})
receipt={'display_event_cursor':s['display_event_cursor'],'roundtrip_ms':12,'render_ms':3}
s=call('/api/clock',{'epoch':epoch,'seq':1,'media_s':0,'video_presented_s':0,'state':'paused','viewer_receipt':receipt})
check(any(e['kind']=='viewer_receipt' for e in s['events']))
bad=dict(receipt,render_ms=-1)
try:call('/api/clock',{'viewer_receipt':bad});raise AssertionError('bad receipt accepted')
except urllib.error.HTTPError as error:check(error.code==400)
replay=call('/api/replay',{'capture':True})
check(replay['frame']['transcripts']['current']==[])
check(call('/api/snapshot')['external']['attempts']==before)
report={'checks':checks,'result':'passed','external_sends':0,'browser_operation':False}
(Path(os.environ.get('JEV_TEST_ARTIFACT_DIR','artifacts/live-status-m3a'))/'http-m3a.json').write_text(json.dumps(report),encoding='utf-8')
print(json.dumps(report))
