"""Explicitly authorized one-run CD acceptance. Never retries or resumes this work."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import hashlib,json,os,time,importlib.metadata
from context_fields.live import LiveCapability,CAMPAIGN_FILE
from context_fields.work_budget import WorkBudget,LIMITS
from context_fields.score_policy import CD,policy_record
from context_fields.session import Session
from context_fields.media import Asset,probe,SUPPORTED
from context_fields.local_pipeline import LocalPipeline
from context_fields.local_models import LocalModels

WORK='cd-display-acceptance-20260920-v1'
OUT=ROOT/'artifacts/cd-display-acceptance'/WORK
PRIVATE=ROOT/'artifacts/local-private'/WORK
SOURCE='run-1789828491134742200'

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')

def managed_asset():
    expected=None
    with (ROOT/'artifacts/sessions'/f'{SOURCE}.jsonl').open(encoding='utf8') as f:
        for line in f:
            e=json.loads(line)
            if e['kind']=='local_asset':expected=e['payload'];break
    if not expected:raise ValueError('saved_media_identity_missing')
    paths=[ROOT/'artifacts/media'/(expected['id']+ext) for ext in SUPPORTED if (ROOT/'artifacts/media'/(expected['id']+ext)).is_file()]
    if len(paths)!=1:raise ValueError('managed_media_ambiguous')
    p=paths[0]
    if digest(p)!=expected['sha256'] or p.stat().st_size!=expected['bytes']:raise ValueError('managed_media_mismatch')
    return Asset(expected['id'],p,p.name,p.stat().st_size,expected['sha256'],probe(p))

def run():
    if sys.argv[1:]!=['--execute-approved-cd']:raise ValueError('explicit_execution_required')
    if (PRIVATE/'work-state.json').exists() or (PRIVATE/'work-state.tmp').exists():raise ValueError('work_exists_no_second_run')
    for k,v in {'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','HF_HOME':str(ROOT/'.cache/huggingface'),'TORCH_HOME':str(ROOT/'.cache/torch')}.items():os.environ[k]=v
    asset=managed_asset();end=min(60,asset.metadata['duration_s'])
    print('media_hash_verified; loading existing local models',flush=True)
    models=LocalModels();cap=s=work=None;reason='preflight_error';started=False
    try:
        models.load_future.result(timeout=240)
        manifest={'work_id':WORK,'source_run':SOURCE,'media':asset.public(),'interval':[0,end],
            'clock_source':'simulated_player_realtime','browser_operation':False,'policy':policy_record(CD),
            'models':models.public(),'limits':LIMITS,'retry':0,'redirect':0,
            'packages':{k:importlib.metadata.version(k) for k in ('torch','transformers','faster-whisper','av','numpy')},
            'sources':{str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'context_fields').glob('*.py'))}}
        cap=LiveCapability(approved=True,phase='demo')
        manifest['budget_start']=cap.budget.summary()
        if cap.budget.attempts>=240 or cap.budget.units>=720:raise ValueError('campaign_exhausted')
        work=WorkBudget(cap.budget,PRIVATE,WORK,manifest)
        sid='run-'+str(time.time_ns());work.start(sid)
        s=Session(sid,'LOCAL',ROOT/'artifacts/sessions'/f'{sid}.jsonl',adoption_policy_id=CD)
        s.local_pipeline=LocalPipeline(s,asset,models,start=0,end_s=end)
        manifest['profiles']=s.dominance.registry.public();manifest['run_id']=sid
        work.state['manifest']=manifest;work.save();save('run-manifest.json',manifest)
        s.ledger.event('run_manifest',manifest)
        s.enable_live(cap,capture_directory=PRIVATE/'raw',work_id=WORK,budget=work)
        s.campaign_budget=cap.budget
        started=True;reason='range_complete';base=time.monotonic();last_print=-10
        print('run_started '+sid,flush=True)
        # One continuously increasing real-time release; no whole-video semantic prepass.
        while True:
            now=time.monotonic();t=min(end,now-base)
            if s.dispatcher.stopped:
                reason='hard_error_or_budget_stop';break
            message=dict(epoch=s.clock.epoch,seq=s.clock.seq+1,media_s=t,video_presented_s=t,
                         audio_presented_s=t if asset.metadata['has_audio'] else None,state='playing',rate=1)
            if t>=end:
                with s.ledger.lock:
                    s.clock.notify(**message,wall_s=now)
                    s.ledger.event('player_release',{'clock_seq':s.clock.seq,'state':'ended','released':s.clock.released,'received_wall_s':now},t)
                break
            s.tick(message,now)
            if t-last_print>=10:
                print(json.dumps({'media_s':round(t,2),'budget':cap.budget.summary(),'local':len(s.ledger.records)}),flush=True);last_print=t
            time.sleep(.1)
    except Exception as error:
        reason=type(error).__name__+':'+str(error)
        # No credential values or authorization headers are ever included by this runner.
        print('run_error '+reason,flush=True)
    finally:
        if s:
            with s.ledger.lock:
                s.view(record=False);s.begin_drain(reason)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and (s.dispatcher.active or s.local_pipeline.pending or s.local_pipeline.decode_future):
                with s.ledger.lock:s.local_pipeline.collect(time.monotonic());s.view()
                time.sleep(.05)
            with s.ledger.lock:
                s.stop();s.view()
            if s.remote_pool:s.remote_pool.shutdown(wait=True,cancel_futures=True)
            with s.ledger.lock:s.view()
            s.local_pipeline.close()
        models.close()
        if work:
            work.finish(reason,s.dispatcher.log if s else [])
            save('execution-result.json',{'run_id':s.ledger.session if s else None,'reason':reason,'started':started,
                'end_media_s':s.clock.media_s if s else None,'budget_start':work.state['baseline'],'budget_end':cap.budget.summary(),
                'dispatcher':s.dispatcher.log if s else [],'models':models.public(),'key_read_by_authorized_backend':cap.key_file_read,
                'process_normal_cleanup':True,'clock_source':'simulated_player_realtime','browser_operation':False})
        if cap:cap.close()
        print('normal_cleanup_complete '+reason,flush=True)

if __name__=='__main__':run()
