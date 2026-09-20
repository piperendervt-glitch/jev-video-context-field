"""Independent decimal checks and timing reconstruction from immutable artifacts."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.offline_guard import install
install()
import json,hashlib,math
from decimal import Decimal as D
from collections import Counter
OUT=ROOT/'artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1'
PRIVATE=ROOT/'artifacts/local-private/jev-parallel-p1-20260920-v1'
def read(path):return json.loads(path.read_text(encoding='utf8'))
def sha(b):return hashlib.sha256(b).hexdigest()
def pctl(v,p):
    v=sorted(v);return v[max(0,math.ceil(len(v)*p)-1)] if v else None
def summary(v):return {'n':len(v),'p50':pctl(v,.5),'p95':pctl(v,.95),'method':'nearest rank; small sample'}
measurement=read(OUT/'measurement-result.json');manifest=read(OUT/'measurement-manifest.json')
samples=[];conditions=[]
for c in measurement['conditions']:
    intervals=[];waits=[];latencies=[];endtoend=[]
    for entry in sorted(c['log'],key=lambda x:x['reservation']['attempt']):
        raw=entry['raw_capture'];body=(PRIVATE/'raw'/raw['body_ref']).read_bytes()
        assert raw['complete'] and sha(body)==raw['body_sha256'] and raw['request_sha256']==sha(entry['request'].encode())
        assert raw['request_context']['attempt']==entry['reservation']['attempt']
        req=json.loads(entry['request']);response=json.loads(body,parse_float=D,parse_int=D)
        assert response['model']==req['model'] and set(response['answers'])==set(req['questions'])
        origins=[]
        for item in manifest['inputs']:
            old=json.loads(item['payload_json'])
            if old['state']==req['state'] and old['model']==req['model'] and all(q in old['questions'] and old['questions'][q]==v for q,v in req['questions'].items()):
                origins.append({k:item[k] for k in ('source_run','source_event_seq','source_input_hash')})
        assert len(origins)==1
        checks=[]
        for qid,a in response['answers'].items():
            probs=a.get('probabilities');row={'question_id':qid,'type':a['type']}
            if probs:
                total=sum(probs.values(),D(0));row.update(probability_sum=str(total),sum_valid=abs(total-1)<=D('0.000001'))
                assert all(D(0)<=v<=D(1) for v in probs.values())
                if a['type']=='score':
                    mean=sum(D(k)*v for k,v in probs.items());delta=a['score']-mean
                    row.update(provider_score=str(a['score']),distribution_expectation=str(mean),score_delta=str(delta),
                        relation_valid=abs(delta)<=D('0.000001'),cd_warning_only=abs(delta)>D('0.000001') and row['sum_valid'])
            checks.append(row)
        sent=entry['sent_wall_s'];received=raw['timings']['received_wall_s']
        intervals.extend([(sent,1),(received,-1)]);waits.append(entry['queue_wait_s']);latencies.append(received-sent)
        endtoend.append(entry['completed_wall_s']-entry['accepted_wall_s'])
        samples.append({'attempt':entry['reservation']['attempt'],'wire_id':entry['wire_id'],'role':entry['role'],
            'units':entry['unit_ids'],'question_ids':list(req['questions']),'request_hash':entry['request_hash'],
            'body_ref':str((PRIVATE/'raw'/raw['body_ref']).relative_to(ROOT)),'body_sha256':raw['body_sha256'],
            'http_request_id':raw['http_request_id'],'http_status':raw['http_status'],'source':origins[0],
            'checks':checks,'status':entry['status'],'received_after_stop':entry.get('received_after_stop'),
            'accepted_wall_s':entry['accepted_wall_s'],'sent_wall_s':sent,'raw_body_received_wall_s':received,
            'writer_completed_wall_s':entry['completed_wall_s'],'queue_wait_s':entry['queue_wait_s']})
    running=peak=0
    for _,change in sorted(intervals):running+=change;peak=max(peak,running)
    stop=next((e for e in c['lifecycle'] if e['stage']=='global_stop' and e.get('hard_error')),None)
    assert not stop or not any(e['stage']=='sent' and e['lifecycle_seq']>stop['lifecycle_seq'] for e in c['lifecycle'])
    per_role={role:summary([x['queue_wait_s'] for x in c['log'] if x['role']==role]) for role in ('evidence','dominance')}
    conditions.append({'id':c['condition'],'actual_send_admission_to_body_peak':peak,'reported_peak':c['actual_http_peak'],
        'concurrency_definition':'unique started transport admissions overlapping until complete raw body; not cards, reservations alone, provider internals or packet capture',
        'queue_wait_s':summary(waits),'http_body_s':summary(latencies),'accepted_to_writer_s':summary(endtoend),'queue_by_role':per_role,
        'wire_status':dict(Counter(x['status'] for x in c['log'])),'units_numeric_completed':c['logical_numeric_completed'],
        'logical_demand':4,'questions_received':sum(len(json.loads(x['request'])['questions']) for x in c['log']),
        'questions_demand':12,'current_live_eligible':None,'current_live_reason':'historical diagnostic, eligibility not tested',
        'lifecycle_counts':dict(Counter(e['stage'] for e in c['lifecycle'])),'stop':stop,
        'no_new_send_after_stop':True,'all_responses_within_5s':all(x<=5 for x in endtoend)})
budget_delta={k:measurement['budget_end'][k]-measurement['budget_start'][k] for k in measurement['budget_start']}
assert budget_delta['attempts']==len(samples)==8
assert sum(len(x['question_ids']) for x in samples)==budget_delta['questions']
result={'samples':samples,'conditions':conditions,'budget_delta':budget_delta,'unperformed':measurement['unperformed'],
    'old_and_new_inputs_immutable':True,'independent_method':'decimal JSON literals; no application score validator imported',
    'raw_body_not_tls_capture':True,'real_browser':False}
(OUT/'independent-analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'conditions':conditions,'budget_delta':budget_delta,'numeric_checks':[x['checks'] for x in samples]},ensure_ascii=False))
