"""Explicit development re-analysis of the already selected asset.

Real saved media + installed models + optional approved Jev; simulated clock,
NOT browser playback. Reuses only the media identified in the before capture.
"""
import argparse
import hashlib
import http.cookiejar
import json
from pathlib import Path
import time
import urllib.request
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/live-status-m3a'
BASE='http://127.0.0.1:8876'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--approved-live-v02',action='store_true');parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    original=json.loads((OUT/'before-current-snapshot.json').read_text(encoding='utf-8'))
    media=original['media'];aid=media['id']
    if not aid.startswith('asset-') or len(aid)!=38 or not all(c in '0123456789abcdef' for c in aid[6:]):raise ValueError('asset_id')
    source=ROOT/'artifacts/media'/f'{aid}{Path(media["name"]).suffix.lower()}'
    if source.resolve().parent!=(ROOT/'artifacts/media').resolve():raise ValueError('asset_boundary')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=media['sha256']:raise ValueError('selected_bytes_changed')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    token=None
    def call(path,body=None):
        req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json',**({'X-Session-Token':token} if token else {})})
        with opener.open(req,timeout=15) as response:return json.load(response)
    token=call('/api/bootstrap')['token']
    models=call('/api/models')
    if models['status']!='ready' and not args.prepare_only:raise ValueError('models_not_ready')
    with source.open('rb') as stream:
        req=urllib.request.Request(BASE+'/api/assets',data=stream,headers={'Content-Type':'application/octet-stream',
             'X-Session-Token':token,'Content-Length':str(source.stat().st_size),'X-File-Name':quote(media['name'])})
        with opener.open(req,timeout=30) as response:asset=json.load(response)
    state=call('/api/session',{'mode':'LOCAL','asset_id':asset['id'],'start_s':0})
    if args.prepare_only:
        (OUT/'demo-ready.json').write_text(json.dumps({'asset':asset,'session':state['snapshot']['session'],'mode':state['evaluation_label'],
            'state':'paused; no released samples; no new API sends','budget':state['external']},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'session':state['snapshot']['session'],'state':'prepared, paused, no send','asset_id':asset['id']}))
        return
    if args.approved_live_v02:state=call('/api/live/start',{'asset_id':asset['id']})
    run=state['snapshot']['session'];start_counts=state['external'];epoch=state['clock']['epoch']
    first={};peak={};latencies=[];frames=[];seq=0;began=time.monotonic();state_before_stop=None
    report={'run':run,'material':'same user-selected SHA256; development re-analysis',
        'clock':'simulated player notifications, NOT browser playback','source_sha256':media['sha256'],
        'original_asset_id':aid,'asset':asset,'start_counts':start_counts,'models':models,'mode':state['evaluation_label']}
    try:
        for i in range(301):
            target=i*.2;delay=began+target-time.monotonic()
            if delay>0:time.sleep(delay)
            t=time.monotonic()
            state=call('/api/clock',{'epoch':epoch,'seq':seq,'media_s':target,'video_presented_s':target,
                       'audio_presented_s':target,'state':'playing'});seq+=1
            latencies.append(time.monotonic()-t)
            for name in ('SpeechASR','FrameInterpreter','TextContext'):
                if name in state['local_observations']:first.setdefault(name,{'wall_s':time.monotonic()-began,'media_s':target})
            for p in state.get('profiles',[]):
                if p['runtime_verified']:peak[p['id']]=p
            if i%5==0:frames.append({'media_s':target,'readouts':state['readouts'],'profiles':state['profiles'],'queues':state['queues']})
            if args.approved_live_v02 and state['evaluation_status']['stopped']:report['exit_reason']='dispatcher_stopped';break
            if state['external']['attempts']-start_counts['attempts']>=20:report['exit_reason']='development_send_target_reached';break
            if peak and target>=38:report['exit_reason']='topic_runtime_response_verified';break
        if not state['evaluation_status']['stopped'] and state['external']['attempts']-start_counts['attempts']<20:
            target=state['clock']['media_s']
            for _ in range(30):
                state=call('/api/clock',{'epoch':epoch,'seq':seq,'media_s':target,'video_presented_s':target,
                           'audio_presented_s':target,'state':'paused'});seq+=1;time.sleep(.2)
                if state['evaluation_status']['stopped']:break
        state_before_stop=state
    finally:
        stopped=call('/api/control',{'epoch':epoch,'action':'stop'})
        time.sleep(1)
        final=call('/api/snapshot')
        replay=call('/api/replay',{'capture':True})
        if replay['count']:last_replay=call('/api/replay',{'index':replay['count']-1})['frame']
        else:last_replay=None
        report.update(first_outputs=first,http_max_s=max(latencies,default=0),elapsed_s=time.monotonic()-began,
            verified_profiles=peak,frames=frames,before_stop=state_before_stop,final=final,replay_count=replay['count'],
            replay_preserves_transcripts=bool(last_replay and last_replay['transcripts']==stopped['transcripts']),
            end_counts=final['external'])
        (OUT/f'{run}-development-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'run':run,'exit_reason':report.get('exit_reason'),'start':start_counts,'end':final['external'],
            'profiles':final['profiles'],'feedback':final['feedback'],'replay_frames':replay['count']},ensure_ascii=True),flush=True)

if __name__=='__main__':main()
