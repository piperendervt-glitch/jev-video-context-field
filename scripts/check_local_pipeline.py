"""Real elapsed-time inference with synthetic AV + simulated player notifications.

Never reports user-media/browser acceptance. Uses the running localhost app.
"""
import http.cookiejar
import json
from pathlib import Path
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8876'
def main():
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    token=None
    def call(path,body=None):
        headers={'Content-Type':'application/json'}
        if token:headers['X-Session-Token']=token
        req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with opener.open(req,timeout=15) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode()) from error
    token=call('/api/bootstrap')['token']
    deadline=time.monotonic()+120
    while call('/api/models')['status']!='ready':
        if time.monotonic()>deadline:raise TimeoutError('model_initialization')
        time.sleep(.5)
    media=ROOT/'fixtures/local-diagnostic.mp4'
    with media.open('rb') as f:
        req=urllib.request.Request(BASE+'/api/assets',data=f,headers={'Content-Type':'application/octet-stream','X-Session-Token':token,
              'Content-Length':str(media.stat().st_size),'X-File-Name':'local-diagnostic.mp4'})
        with opener.open(req,timeout=30) as response:asset=json.load(response)
    state=call('/api/session',{'mode':'LOCAL','asset_id':asset['id'],'start_s':0});epoch=state['clock']['epoch']
    start=time.monotonic();frames=[];first_output={};latencies=[]
    for i in range(151):
        target=i*.2
        delay=start+target-time.monotonic()
        if delay>0:time.sleep(delay)
        called=time.monotonic()
        state=call('/api/clock',{'epoch':epoch,'seq':i,'media_s':target,'video_presented_s':target,'audio_presented_s':target,'state':'playing'})
        latencies.append(time.monotonic()-called)
        for name in ('SpeechASR','FrameInterpreter','TextContext'):
            if name in state['local_observations']:first_output.setdefault(name,time.monotonic()-start)
        frames.append(state)
    for i in range(151,227):
        state=call('/api/clock',{'epoch':epoch,'seq':i,'media_s':30,'video_presented_s':30,'audio_presented_s':30,'state':'paused'})
        frames.append(state);time.sleep(.2)
    report={'material':'synthetic AV / real installed inference / simulated player, not browser or user footage',
            'elapsed_s':time.monotonic()-start,'asset':asset,'last_state':state,
            'max_hypotheses':{space:max(len(f['snapshot']['spaces'][space]['hypotheses']) for f in frames) for space in ('person','place','conversation')},
            'external':state['external'],'first_output_wall_s':first_output,'http_latency_max_s':max(latencies),
            'feedback':state['feedback']}
    (ROOT/'artifacts/local-pipeline-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('elapsed_s','max_hypotheses','external')},ensure_ascii=False))
    print(json.dumps(state['local_pipeline'],ensure_ascii=False,indent=2))

if __name__=='__main__':main()
