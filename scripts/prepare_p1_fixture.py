"""Explicit synthetic UI lifecycle fixture. Never evidence of real concurrency."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.offline_guard import install
install()
import hashlib,json,time
from context_fields.media import AssetStore,PTSDecoder
from context_fields.config import json_text
OUT=ROOT/'artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1'
if (OUT/'fixture.json').exists():raise SystemExit('fixture already prepared')
media=ROOT/'fixtures/synthetic.webm'
with media.open('rb') as stream:asset=AssetStore(ROOT/'artifacts/media').import_stream(stream,media.stat().st_size,'synthetic.webm')
frame=PTSDecoder(asset).frame(0,1)
refs=[{'modality':'video','frame_id':frame['frame_id'],'pts':frame['pts'],'time_base':frame['time_base'],
       'stream':frame['stream'],'media_origin_s':frame['media_origin_s'],'interval':[frame['media_s'],frame['media_s']],
       'root':asset.id+':fixture-only'}]
run='run-'+str(time.time_ns());events=[]
def event(kind,payload,at):
    seq=len(events)+1
    events.append({'schema_version':'3','event_id':run+':event:'+str(seq),'event_seq':seq,'session':run,'epoch':1,
        'media_id':asset.id,'media_s':None,'elapsed_s':at,'created_monotonic_s':at,'kind':kind,'payload':payload})
event('local_asset',asset.public(),0)
event('run_manifest',{'diagnostic_kind':'合成fixture・並列行の状態確認（実Jevではない）','fixture':True,'current_live_publication':False},0)
requests={}
for i in range(6):
    payload=json_text({'model':'jev-1.13.0','state':{'unit0':{'observation_id':'fixture-'+str(i),'claim':'合成の表示対象 '+str(i)}},
        'questions':{'unit0.q0':{'type':'noul','instructions':'Synthetic UI fixture only'}}})
    requests[i]={'logical_id':'fixture-'+str(i),'role':'evidence','unit_ids':['fixture-'+str(i)],'request_hash':hashlib.sha256(payload.encode()).hexdigest()}
    event('jev_lifecycle',{**requests[i],'stage':'requested','request':payload,'source_refs':refs,'source_text':'合成表示fixture。実動画の解析結果ではありません。'},1+i*.1)
for i in range(4):event('jev_lifecycle',{**requests[i],'stage':'sent','wire_id':'synthetic-http-'+str(i)},2+i*.1)
for i in (2,0):
    event('jev_diagnostic_result',{'logical_id':'fixture-'+str(i),'unit_name':'unit0','results':{'unit0.q0':{'noul':.5}}},4 if i==2 else 5)
    event('jev_lifecycle',{**requests[i],'stage':'settled'},4.1 if i==2 else 5.1)
event('jev_lifecycle',{**requests[4],'stage':'expired','reason':'deadline_before_send'},6)
event('jev_lifecycle',{**requests[5],'stage':'cancelled','reason':'new_input_version'},6.1)
event('jev_lifecycle',{**requests[1],'stage':'discarded','reason':'superseded_dependency'},7)
event('jev_lifecycle',{'stage':'global_stop','reason':'ValueError:probability_sum','hard_error':True},8)
event('jev_lifecycle',{**requests[3],'stage':'error','reason':'ValueError:probability_sum'},8.1)
event('diagnostic_end',{'reason':'synthetic_fixture_complete'},10)
path=ROOT/'artifacts/sessions'/(run+'.jsonl')
with path.open('x',encoding='utf8') as f:
    for e in events:f.write(json_text(e)+'\n')
(OUT/'fixture.json').write_text(json.dumps({'run_id':run,'fixture':True,'asset_id':asset.id,'source':str(path.relative_to(ROOT)),
    'meaning':'hand-authored synthetic state timeline, not real concurrency or model results','sha256':hashlib.sha256(path.read_bytes()).hexdigest()},ensure_ascii=False,indent=2),encoding='utf8')
print(run)
