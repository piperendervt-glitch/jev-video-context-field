"""Offline independent raw arithmetic, provenance and timing review. No transport."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.offline_guard import install
install()
import json,hashlib,collections
from decimal import Decimal
from context_fields.replay import Replay,read_events
WORK='cd-display-acceptance-20260920-v1';OUT=ROOT/'artifacts/cd-display-acceptance'/WORK
PRIVATE=ROOT/'artifacts/local-private'/WORK

def main():
    execution=json.loads((OUT/'execution-result.json').read_text(encoding='utf8'))
    path=ROOT/'artifacts/sessions'/(execution['run_id']+'.jsonl')
    events=read_events(path);replay=Replay(events)
    requests={};records=[];raw_rows=[];score_rows=[]
    for e in events:
        if e['kind']=='evaluation_requested':
            p=e['payload'];requests[hashlib.sha256(p['request'].encode()).hexdigest()]={'seq':e['event_seq'],**p,'parsed_request':json.loads(p['request'])}
        if e['kind']=='record':records.append(e['payload'])
        elif e['kind']=='atomic_replacement':records.extend(e['payload']['records'])
    for p in sorted((PRIVATE/'raw').glob('*.json')):
        meta=json.loads(p.read_text(encoding='utf8'));body=(p.parent/meta['body_ref']).read_bytes()
        assert hashlib.sha256(body).hexdigest()==meta['body_sha256']
        req=requests[meta['request_sha256']];payload=req['parsed_request']
        row={k:meta[k] for k in ('capture_id','complete','http_status','request_sha256','body_sha256','body_ref','http_request_id','request_context')}
        row.update(request_event_seq=req['seq'],role=req['role']);raw_rows.append(row)
        if not meta['complete'] or meta['http_status']!=200:continue
        raw=json.loads(body,parse_float=Decimal,parse_int=Decimal)
        for qid,a in raw.get('answers',{}).items():
            if a.get('type')!='score':continue
            q=payload['questions'][qid];total=sum(a['probabilities'].values());mean=sum(Decimal(k)*v for k,v in a['probabilities'].items())/total
            score=a['score'];unit=next(u for u in meta['request_context']['unit_ids'] if qid.startswith(u+'.'))
            adopted=[r for r in records if r.get('kind')=='dominance_evaluation' and r.get('input_hash')==meta['request_sha256'] and qid in r.get('numeric_diagnostics',{})]
            diagnostics=[r['numeric_diagnostics'][qid] for r in adopted]
            assert all(abs(float(mean)-d['mean_used'])<1e-12 for d in diagnostics)
            score_rows.append({'capture_id':meta['capture_id'],'request_hash':meta['request_sha256'],'request_event_seq':req['seq'],
                'unit':unit,'question':qid,'provider_score':str(score),'decimal_probability_sum':str(total),'decimal_mean':str(mean),
                'decimal_delta':str(score-mean),'decimal_discrepancy':abs(score-mean)>Decimal('0.000001'),
                'saved_diagnostics':diagnostics,'evaluation_ids':[r['id'] for r in adopted],
                'not_adopted':not bool(adopted)})
    admissions=[e['payload'] for e in events if e['kind']=='local_model_admitted']
    asr=[p for p in admissions if p['agent']=='SpeechASR']
    union=[]
    for a,b in sorted(p['input_audio_interval'] for p in asr):
        assert 0<=a<=b<=execution['end_media_s']+1e-6
        if union and a<=union[-1][1]+.0001:union[-1][1]=max(b,union[-1][1])
        else:union.append([a,b])
    gaps=[];end=0
    for a,b in union:
        if a>end+.0001:gaps.append([end,a])
        end=b
    if end<execution['end_media_s']:gaps.append([end,execution['end_media_s']])
    for e in replay.frames:assert replay.at(event_cursor=e['event_seq'])==e['payload']
    readouts=[r for r in records if r['kind']=='dominance_readout']
    report={'run_id':execution['run_id'],'execution':execution,'events':len(events),'replay_frames':len(replay.frames),
        'raw_samples':raw_rows,'score_samples':score_rows,'requests':len(requests),
        'record_kinds':dict(collections.Counter(r['kind'] for r in records)),
        'admitted_agents':dict(collections.Counter(p['agent'] for p in admissions)),
        'asr_inputs':asr,'asr_admitted_union':union,'asr_not_admitted_gaps':gaps,
        'slot_wait_max':max((p['slot_wait_s'] for p in admissions),default=None),
        'readouts':[{k:r.get(k) for k in ('id','evaluation_id','space','profile_id','as_of_media_s','input_snapshot_id','input_cursor','status','reason','items','warning_codes','adoption_policy_id')} for r in readouts],
        'evidence':[{k:r.get(k) for k in ('id','observation_id','hypothesis_id','input_hash','input_cutoff_s','status','reason','dependencies')} for r in records if r['kind']=='evidence_evaluation'],
        'profile_events':[e for e in events if e['kind'].startswith('profile_')],
        'inspection_events':[e for e in events if e['kind'].startswith('inspection_')],
        'local_errors':[e for e in events if e['kind'] in ('local_error','local_model_failed','local_result_discarded')],
        'budget_delta':{k:execution['budget_end'][k]-execution['budget_start'][k] for k in execution['budget_start']},
        'browser_operation':False,'human_semantic_check':False}
    (OUT/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:report[k] for k in ('run_id','events','replay_frames','requests','record_kinds','admitted_agents','budget_delta')},ensure_ascii=False))
    print('raw',len(raw_rows),'scores',len(score_rows),'numeric_discrepancies',sum(x['decimal_discrepancy'] for x in score_rows))

if __name__=='__main__':main()
