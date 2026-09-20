"""Guarded read-only corpus comparison. Writes only the separately named artifact.

Never creates LiveCapability, transports, models, budgets or a running Viewer.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.offline_guard import install,ROOT,COUNTS
install()
from scripts.score_contract_offline import inspect_run,frozen_events,digest
from scripts.score_adoption_compare import compare_response
import json
import hashlib
import csv
from collections import Counter

OUT=ROOT/'artifacts/score-contract-offline/decision-prep-20260920'
OLD=ROOT/'artifacts/score-contract-offline/20260919T145159Z'
RAW=ROOT/'artifacts/local-private/score-raw-capture-20260920-v1'
MISSING='LEGACY_MISSING'


def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


def enrich(path,size,report):
    groups=report['response_groups'];byseq={n:i for i,g in enumerate(groups) for n in g['evaluation_events']}
    requests={};evaluations={};records={};active=set();contexts={i:{} for i in range(len(groups))};evgroup={}
    for e,line in frozen_events(path,size):
        kind,p,seq=e['kind'],e['payload'],e['event_seq']
        if kind=='evaluation_requested':requests[seq]=p['request']
        if kind=='epoch_reset':active.clear()
        if kind=='record':records[p['id']]=p;active.add(p['id'])
        if kind=='invalidation':active.difference_update(p['ids'])
        if kind=='atomic_replacement':
            active.difference_update(p['invalidated'])
            for r in p['records']:records[r['id']]=r;active.add(r['id'])
        if seq in byseq:
            i=byseq[seq];evaluations.setdefault(i,p);evgroup[p['id']]=i
        if kind!='display':continue
        for uid,r in p.get('readouts',{}).items():
            i=evgroup.get(r.get('evaluation_id'))
            if i is None or uid in contexts[i]:continue
            ev=records[r['evaluation_id']];g=groups[i]
            if g['request_event'] not in requests:continue
            req=json.loads(requests[g['request_event']]);state=req['state']
            state=json.loads(state) if isinstance(state,str) else state
            unit=state.get('units',{}).get(uid,{})
            profile=unit.get('profile');clock=p['clock'];snapshot=p['snapshot']
            actual_profile=next((q for q in p.get('profiles',[]) if profile and q['id']==profile['id']),None)
            if profile:
                profile_ok=(actual_profile is not None and all(profile.get(k)==actual_profile.get(k) for k in
                    ('revision','candidate_set_version','rubric_version')) and actual_profile.get('epoch')==clock['epoch'])
            else:profile_ok=True if r.get('profile_version') is not None and r.get('candidate_set_version') is not None else None
            deps=ev.get('dependencies')
            valid=all(k in active and records[k]['revision']==v for k,v in deps.items()) if isinstance(deps,dict) else None
            contexts[i][uid]={
                'source_display_event':seq,'historical_status':r.get('status'),'historical_reason':r.get('reason'),
                'source_readout_id':r['id'],'dependency_versions':deps,
                'as_of_media_s':r.get('as_of_media_s'),'display_media_s':clock.get('media_s'),
                'expires_after_media_s':r.get('expires_after_media_s'),
                'snapshot_match':state.get('input_snapshot_id')==r.get('input_snapshot_id')==ev.get('input_snapshot_id'),
                'epoch_match':r.get('epoch')==clock.get('epoch'),
                'scope_match':r.get('scope_revision')==snapshot.get('scope_revision') if 'scope_revision' in r and 'scope_revision' in snapshot else None,
                'profile_match':profile_ok,'dependencies_valid':valid,
                'ttl_valid':clock['media_s']<=r['expires_after_media_s'] if 'expires_after_media_s' in r else None,
                'deadline_valid':ev['completed_wall_s']<=ev['accepted_wall_s']+5 if 'completed_wall_s' in ev and 'accepted_wall_s' in ev else None,
                'readout_active':r['id'] in active and r.get('status')!='superseded',
                'profile_version':r.get('profile_version',MISSING),'candidate_set_version':r.get('candidate_set_version',MISSING),
                'rubric_version':r.get('rubric_version',MISSING),'registry_revision':r.get('registry_revision',MISSING)}
    return requests,evaluations,contexts


def collect():
    manifest=json.loads((OLD/'manifest-before.json').read_text(encoding='utf-8'))
    rows=[];responses=[];collection=[]
    for rel,info in manifest['fixed_inputs'].items():
        path=ROOT/rel;h=hashlib.sha256()
        with path.open('rb') as f:
            remaining=info['size']
            while remaining:
                part=f.read(min(1048576,remaining));assert part;h.update(part);remaining-=len(part)
        assert h.hexdigest()==info['sha256']
        report=inspect_run(path,info['size'])
        old=json.loads((OLD/(path.stem+'.json')).read_text(encoding='utf-8'))
        old_ids={(s['request_hash'],tuple(s['evaluation_events']),s['question_id']) for s in old['scores']}
        new_ids={(s['request_hash'],tuple(s['evaluation_events']),s['question_id']) for s in report['scores']}
        assert old_ids==new_ids
        requests,evals,contexts=enrich(path,info['size'],report)
        collection.append({'run':path.stem,'fixed_prefix_sha256':info['sha256'],'fixed_prefix_bytes':info['size'],
            'current_extra_bytes':path.stat().st_size-info['size'],'original_score_copies':report['score_record_copies'],
            'copies_excluded':report['score_record_duplicate_copies'],'score_questions':len(report['scores']),
            'response_groups_including_choice_only':len(report['response_groups']),'old_sample_identity_set_equal':True})
        for i,g in enumerate(report['response_groups']):
            selected=[s for s in report['scores'] if s['evaluation_events']==g['evaluation_events']]
            if g['request_event'] not in requests:raise ValueError('unmatched_saved_request_requires_explicit_missing_row')
            reqtext=requests[g['request_event']];assert digest(reqtext.encode())==g['request_hash']
            request=json.loads(reqtext);ev=evals[i]
            # These are application-reserialized values, never claimed as original HTTP tokens.
            body=json.dumps(ev['raw'],ensure_ascii=False,allow_nan=False).encode()
            state=request['state'];state=json.loads(state) if isinstance(state,str) else state
            result=compare_response(request,body,stage='APPLICATION_RESERIALIZED',contexts=contexts[i],unit_ids=list(state['units']),
                expected_returned_model='MOCK-local-wiring-v1' if g['mode']=='MOCK' else None)
            response_id=f'{path.stem}:evaluation-events:{"-".join(map(str,g["evaluation_events"]))}'
            responses.append({'response_id':response_id,'mode':g['mode'],'evidence_stage':'APPLICATION_RESERIALIZED',
                'request_hash':g['request_hash'],'attempt':g['attempt'],'unit_count':g['unit_count'],
                'actual_dispatcher_record':next((d for d in report['dispatch'] if d['reservation']['attempt']==g['attempt'] and d['reservation']['role']=='dominance'),None),
                'policies':result['policies'],'question_checks':result['question_checks']})
            assert {r['question_id'] for r in result['rows']}=={s['question_id'] for s in selected}
            for row in result['rows']:
                source=next(s for s in selected if s['question_id']==row['question_id'])
                row.update(sample_id=response_id+':'+row['question_id'],response_id=response_id,run=path.stem,mode=g['mode'],
                    request_hash=g['request_hash'],response_record_hash=g['reserialized_response_hash'],
                    request_event=g['request_event'],evaluation_events=g['evaluation_events'],readout_events=g['readout_events'],
                    requested_model=request['model'],returned_model=ev['raw'].get('model'),
                    attempt=g['attempt'],attempt_join_basis=g['attempt_join_basis'],possible_attempts=g['possible_attempts_by_request_shape_only'],
                    profile=source['profile'],profile_version=source['profile_version'],rubric_version=source['rubric_version'],
                    candidate_set_version=source['candidate_set_version'],snapshot_id=g['snapshot_id'],
                    criteria=request['questions'][row['question_id']]['criteria'],legend=ev['raw']['answers'][row['question_id']].get('legend'),
                    original_numeric_lexemes='UNAVAILABLE_AFTER_PARSE',original_duplicate_keys='UNDETERMINABLE_AFTER_PARSE',
                    historical_unit_status=contexts[i].get(row['unit_id'],{}).get('historical_status',MISSING))
                rows.append(row)
    execution_path=ROOT/'artifacts/score-raw-capture/score-raw-capture-20260920-v1/resume-20260920-01/execution.json'
    execution=json.loads(execution_path.read_text(encoding='utf-8'));sample=execution['samples'][0];meta=sample['capture']
    raw_path=RAW/'responses'/meta['body_ref'];body=raw_path.read_bytes();reqbytes=(RAW/'case_01.request.json').read_bytes()
    assert digest(body)==meta['body_sha256'] and len(body)==meta['body_bytes']==int(meta['response_headers']['content-length'])
    assert digest(reqbytes)==meta['request_sha256']==execution['request_hashes'][0]
    assert meta['complete'] and not meta['truncated'] and meta['capture_failure'] is None
    assert json.loads(raw_path.with_suffix('.json').read_text(encoding='utf-8'))==meta
    request=json.loads(reqbytes)
    result=compare_response(request,body,stage='RAW_HTTP_BODY',contexts={'diagnostic':{'diagnostic_only':True}},
                            unit_ids=['diagnostic'],http_status=meta['http_status'])
    response_id='score-raw-capture-20260920-v1:attempt:53'
    responses.append({'response_id':response_id,'mode':'LIVE-JEV-DIAGNOSTIC','evidence_stage':'RAW_HTTP_BODY',
        'request_hash':meta['request_sha256'],'attempt':53,'unit_count':1,'actual_dispatcher_record':sample['dispatcher'],
        'policies':result['policies'],'question_checks':result['question_checks']})
    for row in result['rows']:
        row.update(sample_id=response_id+':'+row['question_id'],response_id=response_id,run=None,mode='LIVE-JEV-DIAGNOSTIC',
            request_hash=meta['request_sha256'],body_sha256=meta['body_sha256'],body_bytes=len(body),complete=True,
            request_id=meta['http_request_id'],attempt=53,attempt_join_basis='private capture context + work reservation + request hash',
            profile='DIAGNOSTIC_NOT_REGISTERED',profile_version='NOT_APPLICABLE',rubric_version='fixed-payload-sha256',
            candidate_set_version='NOT_APPLICABLE',snapshot_id=None,criteria=request['questions'][row['question_id']]['criteria'],
            original_numeric_lexemes=result['numeric_lexemes'],original_duplicate_keys=result['duplicate_keys'],
            requested_model=request['model'],returned_model=sample['analysis']['returned_model'])
        rows.append(row)
    return rows,responses,collection


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows,responses,collection=collect()
    assert len({r['sample_id'] for r in rows})==len(rows)
    for name,items in [('comparison.jsonl',rows),('response-units.jsonl',responses)]:
        with (OUT/name).open('w',encoding='utf-8') as f:
            for item in items:f.write(json.dumps(item,ensure_ascii=False,default=str)+'\n')
    groups={}
    for row in rows:
        key=f'{row["evidence_stage"]}/{row["mode"]}'
        group=groups.setdefault(key,{'questions':0,'policies':{p:Counter() for p in ('C0','C1','CP','CD')}});group['questions']+=1
        for p,v in row['policies'].items():
            count=group['policies'][p]
            count['numeric_candidates']+=v['candidate_score'] is not None
            count['unit_numeric_accepted']+=bool(v.get('unit_numeric_accepted'))
            count['public_nonnull_at_saved_cursor']+=v['public_value_pct'] is not None
            count['public_not_evaluated']+=v['public_reason'] in ('NOT_EVALUATED','NOT_EVALUATED_SPEC_MISSING')
    summary={'score_questions':len(rows),'response_groups_including_choice_only':len(responses),'by_stage_mode':groups,
        'by_profile':dict(Counter(f'{r["mode"]}/{r["unit_id"]}/v{r["profile_version"]}' for r in rows)),
        'eligibility':dict(Counter(r['eligibility']['status'] for r in rows)),
        'discrepant_questions':[r['sample_id'] for r in rows if r['numeric']['relation_status']=='NUMERIC_DISCREPANCY'],
        'source_collection':collection,'copy_rule':'same run+epoch+request hash+accepted/completed+reserialized response hash; evaluation/readout copies merged, distinct transmission times kept',
        'excluded_evidence_stages':['REPORT_EXCERPT','SYNTHETIC_FIXTURE'],
        'no_counterfactual_live_history':True,'real_jev_requests':0,'real_reservations':0,'real_key_reads':0,'guard':COUNTS}
    dump(OUT/'collection-manifest.json',summary)
    with (OUT/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['sample_id','stage','mode','attempt','unit','profile_version','provider_score','mean_before','mean_after','delta','eligibility']+[f'{p}_{k}' for p in ('C0','C1','CP','CD') for k in ('candidate','public','reason')]
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in rows:
            n=r['numeric'];flat=dict(zip(fields[:11],[r['sample_id'],r['evidence_stage'],r['mode'],r['attempt'],r['unit_id'],r['profile_version'],n.get('provider_score'),n.get('mean_before'),n.get('mean_after'),n.get('delta_after'),r['eligibility']['status']]))
            for p,v in r['policies'].items():flat.update({p+'_candidate':v['candidate_value_pct'],p+'_public':v['public_value_pct'],p+'_reason':v['public_reason']})
            writer.writerow(flat)
    print(json.dumps({'questions':len(rows),'by_stage_mode':groups,'eligibility':summary['eligibility'],'guard':COUNTS},default=str))


if __name__=='__main__':main()
