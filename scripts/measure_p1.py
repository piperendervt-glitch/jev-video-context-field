"""Finite saved-input performance diagnostic. No media inference or live adoption."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import copy,hashlib,json,time,math
from dataclasses import asdict,replace
from context_fields.config import json_text
from context_fields.score_policy import CD,policy_record,validate_dominance_answer
from context_fields.jev import validate_answer
from context_fields.question_batch import WireRequest,split
from context_fields.parallel_dispatcher import ParallelDispatcher
from context_fields.dispatch_policy import DispatchPolicy,P1
from context_fields.live import LiveCapability,CAMPAIGN_FILE
from context_fields.work_budget import WorkBudget
from context_fields.ledger import Ledger

WORK='jev-parallel-p1-20260920-v1'
OUT=ROOT/'artifacts/jev-parallel-p1'/WORK
PRIVATE=ROOT/'artifacts/local-private'/WORK
SOURCE='run-1789867504398998900'
LIMITS={'attempts':48,'units':144,'questions':576,'chars':576000}

def sha(data):return hashlib.sha256(data).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')

def prepare():
    source=ROOT/'artifacts/sessions'/(SOURCE+'.jsonl');records={};selected=[];counts={'evidence':0,'dominance':0};asset=None
    with source.open(encoding='utf8') as f:
        for line in f:
            e=json.loads(line);p=e['payload']
            if e['kind']=='local_asset':asset=p
            if e['kind']=='record':records[p['id']]=(e['event_seq'],p)
            if e['kind']=='atomic_replacement':
                for r in p['records']:records[r['id']]=(e['event_seq'],r)
            if e['kind']!='evaluation_requested' or counts[p['role']]>=2:continue
            req=json.loads(p['request']);role=p['role'];refs=[];texts=[];record_refs=[]
            if role=='evidence':oids=[u['observation_id'] for u in req['state'].values()]
            else:oids=list(dict.fromkeys(s['observation_id'] for u in req['state']['units'].values() for h in u['hypotheses'] for s in h['sources'].values()))
            for oid in oids:
                seq,r=records[oid];refs.extend(r.get('source_refs',[]));texts.append(r.get('text',''))
                record_refs.append({'id':oid,'revision':r['revision'],'event_seq':seq})
            selected.append({'source_run':SOURCE,'source_event_seq':e['event_seq'],'source_input_hash':sha(p['request'].encode()),
                'payload_json':p['request'],'unit_ids':p['unit_ids'],'role':role,'policy_json':json_text(p.get('policy_binding') or policy_record(CD)),
                'context':{'epoch':e['epoch'],'source_run':SOURCE,'source_event_seq':e['event_seq'],'policy':CD,
                    'authorization_scope':'P1-approved-saved-derived-text','source_records':record_refs},
                'source_refs':refs[:32],'source_text':'\n'.join(texts)[:4000]})
            counts[role]+=1
            if sum(counts.values())==4:break
    if counts!={'evidence':2,'dominance':2} or not asset:raise ValueError('insufficient_representative_input')
    conditions=[];allowed=set();cost={k:0 for k in LIMITS}
    for n,q in ((2,1),(4,1),(2,48),(4,48)):
        policy=replace(P1,id=f'p1-measure-c{n}-q{q}',concurrency=n,rps=4,burst=4,request_units=12,request_questions=q,batch=q>1)
        wires=[w for item in selected for w in split(WireRequest(item['payload_json'],tuple(item['unit_ids']),item['role'],item['policy_json']),policy)]
        for w in wires:
            body=json.loads(w.payload_json)
            if body['model']!='jev-1.13.0':raise ValueError('fixed_model_mismatch')
            allowed.add(sha(w.payload_json.encode()));cost['attempts']+=1;cost['units']+=len(w.unit_ids);cost['questions']+=len(body['questions']);cost['chars']+=len(w.payload_json)
        conditions.append({'id':policy.id,'policy':asdict(policy),'planned_requests':len(wires),'workload_original_hashes':[x['source_input_hash'] for x in selected]})
    if any(cost[k]>LIMITS[k] for k in cost):raise ValueError('planned_work_exceeds_cap')
    manifest={'work_id':WORK,'diagnostic_kind':'保存入力の性能診断','source_run':SOURCE,'source_sha256':sha(source.read_bytes()),'asset':asset,
        'inputs':selected,'conditions':conditions,'planned_total':cost,'limits':LIMITS,'allowed_payload_hashes':sorted(allowed),
        'policy':policy_record(CD),'retry':0,'warmup_requests':0,'repetitions_per_condition':1,'order':'c2 split → c4 split → c2 bundled → c4 bundled',
        'cache':'not controlled; repeated same inputs, no extra warmup or repetitions','deadline_s':5,'current_live_publication':False,
        'provider_public':{'model':'jev-1.13.0','url':'https://docs.typesafe.ai/models','rpm':1200,'tokens_per_second':250000,'context_total':64000,'state_plus_longest_question':32000,'dynamic_limits':True},
        'account_specific_limits':'unconfirmed','measurement_max_concurrency':8,'actual_condition_max_concurrency':4,
        'sources':{str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in sorted((ROOT/'context_fields').glob('*.py'))},
        'budget_at_prepare':json.loads(CAMPAIGN_FILE.read_text())}
    save(OUT/'measurement-manifest.json',manifest)
    print(json.dumps({'planned':cost,'conditions':[c['id'] for c in conditions],'inputs':len(selected)}))

def measure():
    if (PRIVATE/'work-state.json').exists() or (PRIVATE/'work-state.tmp').exists():raise ValueError('no_resume_or_second_work')
    manifest=json.loads((OUT/'measurement-manifest.json').read_text(encoding='utf8'))
    for name,digest in manifest['sources'].items():
        if sha((ROOT/name).read_bytes())!=digest:raise ValueError('code_changed_after_manifest')
    if sha((ROOT/'artifacts/sessions'/(SOURCE+'.jsonl')).read_bytes())!=manifest['source_sha256']:raise ValueError('source_changed')
    if manifest['policy']!=policy_record(CD):raise ValueError('policy_changed')
    cap=None;work=None;dispatcher=None;results=[];reason='setup_error';journal=None
    try:
        cap=LiveCapability(approved=True,phase='demo')
        # Per-request expansion is authorized for THIS diagnostic only. Aggregate
        # campaign caps remain the same and the campaign stays exclusively owned.
        cap.budget.request_limits=(12,48,12000)
        before=cap.budget.summary();manifest['budget_start']=before
        plan=manifest['planned_total']
        if before['attempts']+plan['attempts']>240 or before['units']+plan['units']>720 or before['chars']+plan['chars']>2880000:raise ValueError('fixed_comparison_does_not_fit_remaining')
        work=WorkBudget(cap.budget,PRIVATE,WORK,manifest,limits=LIMITS)
        run='run-'+str(time.time_ns());work.start(run);journal=Ledger(run,manifest['asset']['id'],path=ROOT/'artifacts/sessions'/(run+'.jsonl'))
        journal.event('local_asset',manifest['asset']);journal.event('run_manifest',manifest)
        transport=cap.transport(capture_directory=PRIVATE/'raw',work_id=WORK);transport.strict_score_json=True
        for condition in manifest['conditions']:
            policy=DispatchPolicy(**condition['policy']);dispatcher=ParallelDispatcher(transport,work,policy);cursor=0;adopted=[];references={}
            journal.event('diagnostic_condition',{'id':condition['id'],'dispatch_policy':policy.public()})
            def flush():
                nonlocal cursor
                with dispatcher.lock:items=copy.deepcopy(dispatcher.events[cursor:]);cursor=len(dispatcher.events)
                for event in items:
                    source=references.get(event.get('logical_id'))
                    if source and event['stage']=='requested':event.update(source_refs=source['source_refs'],source_text=source['source_text'],source_ref={k:source[k] for k in ('source_run','source_event_seq','source_input_hash')})
                    journal.event('jev_lifecycle',event)
            dispatcher.before_collect=flush
            start=time.monotonic()
            for i,item in enumerate(manifest['inputs']):
                logical=condition['id']+'-input-'+str(i);references[logical]=item
                request=WireRequest(item['payload_json'],tuple(item['unit_ids']),item['role'],item['policy_json'])
                def done(raw,a,b,item=item,logical=logical,request=request):
                    parsed=json.loads(request.payload_json);units=parsed['state'] if request.role=='evidence' else parsed['state']['units']
                    for unit in units:
                        values={}
                        for qid,q in parsed['questions'].items():
                            if not qid.startswith(unit+'.'):continue
                            value=validate_dominance_answer(raw['answers'][qid],q,CD) if request.role=='dominance' else validate_answer(raw['answers'][qid],q)
                            d=value.get('score_derivation')
                            values[qid]=({'provider_score':d['provider_score'],'mean_used':d['mean_used'],'numeric_delta':d['numeric_delta'],'numeric_discrepancy':d['numeric_discrepancy']} if d else value.get('probabilities') or {'noul':value['noul']})
                        journal.event('jev_diagnostic_result',{'logical_id':logical,'unit_name':unit,'role':request.role,'results':values,
                            'publication_eligible':False,'publication_reason':'historical_input_diagnostic_not_current_live',
                            'accepted_wall_s':a,'completed_wall_s':b,'source_refs':item['source_refs'],'source_text':item['source_text'],
                            'source_ref':{k:item[k] for k in ('source_run','source_event_seq','source_input_hash')}})
                    adopted.append(logical)
                dispatcher.submit(request,start,logical,done,context=item['context'],logical_id=logical)
            flush()
            while True:
                flush();dispatcher.pump();flush()
                if not dispatcher.workers and not any(dispatcher.queues.values()):dispatcher.drain();flush();break
                if time.monotonic()>start+5.2:dispatcher.stop('diagnostic_deadline')
                time.sleep(.005)
            dispatcher.close();flush()
            for entry in dispatcher.log:journal.event('dispatcher_result',entry)
            row={'condition':condition['id'],'actual_http_peak':dispatcher.max_active,'requests':len(dispatcher.log),'logical_numeric_completed':len(adopted),
                'current_live_eligible':0,'current_live_reason':'not_a_live_run','stopped':dispatcher.hard_stopped,'reason':dispatcher.stop_reason if dispatcher.hard_stopped else None,
                'log':dispatcher.log,'lifecycle':dispatcher.events,'elapsed_s':time.monotonic()-start}
            results.append(row);print(json.dumps({k:row[k] for k in ('condition','actual_http_peak','requests','logical_numeric_completed','stopped','reason')}),flush=True)
            if dispatcher.hard_stopped or any(e['stage']=='global_stop' and e['reason']!='manual_stop' for e in dispatcher.events):reason=dispatcher.stop_reason;break
            dispatcher=None
        else:reason='fixed_conditions_complete'
    except BaseException as exc:
        reason='diagnostic_exception:'+type(exc).__name__
        raise
    finally:
        try:
            if dispatcher:
                dispatcher.close();flush()
                if not any(r['condition']==condition['id'] for r in results):
                    results.append({'condition':condition['id'],'actual_http_peak':dispatcher.max_active,
                        'requests':len(dispatcher.log),'logical_numeric_completed':len(adopted),
                        'current_live_eligible':0,'stopped':True,'reason':reason,'log':dispatcher.log,
                        'lifecycle':dispatcher.events,'elapsed_s':time.monotonic()-start})
            if journal:journal.event('diagnostic_end',{'reason':reason})
            if work:
                work.finish(reason,[x for row in results for x in row['log']])
                save(OUT/'measurement-result.json',{'work_id':WORK,'run_id':journal.session if journal else None,'reason':reason,'conditions':results,
                    'unperformed':[c['id'] for c in manifest['conditions'] if c['id'] not in [r['condition'] for r in results]],
                    'budget_start':work.state['baseline'],'budget_end':cap.budget.summary(),'key_read_by_backend':cap.key_file_read,'current_live':False,'normal_close':True})
        finally:
            if cap:cap.close()
        print('measurement_closed '+reason,flush=True)

if __name__=='__main__':
    if sys.argv[1:]==['--prepare']:
        from scripts.offline_guard import install
        install();prepare()
    elif sys.argv[1:]==['--execute-approved']:measure()
    else:raise SystemExit('Use --prepare or --execute-approved')
