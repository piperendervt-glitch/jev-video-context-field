"""Explicit fixed synthetic diagnostic; same LiveCapability factory and campaign.

No server controls, model loads or retries. Maximum three
reservations per persistent work ID, less on first failure/mismatch.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
WORK_ID='score-raw-capture-20260920-v1'
HASHES=['04468417d6b9a4b564167f7324dd7124dcee524f9aaa610cda22c569c1375d8a',
        '135e05c4ebda48045ded2be75a226c0abf840ee850b0e1f61424efab491d7109',
        '479329ae4439b68305cd25477b886841c8aa5eacadb43b8e44a5fe41ea98c389']


def run(directory,report_dir,capability_factory=None,*,resume=None):
    from context_fields.live import LiveCapability
    from context_fields.diagnostic_work import DiagnosticWork,DiagnosticBudget
    from context_fields.dispatcher import Dispatcher
    from context_fields.jev import DominanceRequest
    from context_fields.response_archive import PrivateResponseArchive
    from context_fields.score_diagnostics import analyze_body
    directory=Path(directory);report_dir=Path(report_dir);report_dir.mkdir(parents=True,exist_ok=True)
    if (report_dir/'execution.json').exists():raise ValueError('execution_record_already_exists')
    payloads=[]
    for i,h in enumerate(HASHES,1):
        data=(directory/f'case_{i:02}.request.json').read_bytes()
        if hashlib.sha256(data).hexdigest()!=h:raise ValueError('fixed_payload_hash')
        text=data.decode('utf-8');p=json.loads(text)
        if p['model']!='jev-1.13.0' or set(p)!= {'model','state','questions'} or len(p['questions'])!=2 or len(text)>12000:
            raise ValueError('fixed_payload_schema')
        types=sorted(q['type'] for q in p['questions'].values())
        if types!=['choice','score'] or any(len(q['criteria'])!=5 for q in p['questions'].values() if q['type']=='score'):
            raise ValueError('fixed_payload_primitive')
        payloads.append((text,p))
    work=DiagnosticWork(directory,WORK_ID,HASHES,inspect_for_resume=resume is not None);cap=None
    activated=resume is None
    report={'work_id':WORK_ID,'samples':[],'source_mode':'FIXED_SYNTHETIC_DIAGNOSTIC',
            'live_video_acceptance':False,'factory':'context_fields.live.LiveCapability.transport',
            'request_hashes':HASHES,'real_key_read':False,'classification':'BLOCKED_BEFORE_SEND'}
    report['source_sha256']={rel:hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() for rel in (
        'context_fields/live.py','context_fields/dispatcher.py','context_fields/response_archive.py',
        'context_fields/diagnostic_work.py','context_fields/score_diagnostics.py','scripts/capture_score_raw.py')}
    try:
        report['storage_preflight']=PrivateResponseArchive(directory/'responses',WORK_ID).preflight()
        # Constructor acquires the real single-writer lock BEFORE transport() can read the key.
        cap=(capability_factory or (lambda:LiveCapability(approved=True,phase='demo')))()
        report['budget_before']=cap.budget.summary()
        if resume is not None:
            report['resume_event']=work.resume_blocked(cap.budget,**resume)
            activated=True
        transport=cap.transport(capture_directory=directory/'responses',work_id=WORK_ID)
        report['real_key_read']=capability_factory is None
        dispatcher=Dispatcher(transport,DiagnosticBudget(cap.budget,work));last_sent=None
        for index,(text,payload) in enumerate(payloads):
            if dispatcher.stopped:break
            if last_sent is not None:
                time.sleep(max(0,2.01-(time.monotonic()-last_sent)))
            accepted=time.monotonic();request=DominanceRequest(text,('diagnostic',));analysis=None
            def callback(raw,a,b):
                nonlocal analysis
                meta=transport.last_capture
                if not meta or not meta['complete']:raise ValueError('incomplete_capture')
                body=(directory/'responses'/meta['body_ref']).read_bytes()
                analysis=analyze_body(body,payload)
                if analysis['classification']!='NO_MISMATCH_IN_BOUNDED_SAMPLE':raise ValueError('diagnostic_contract_rejected')
            dispatcher.submit(request,accepted,'bounded-score',callback)
            ran=dispatcher.run_one(accepted,time.monotonic);last_sent=accepted
            meta=transport.last_capture;entry=dispatcher.log[-1] if dispatcher.log else {}
            if analysis is None and meta and meta['complete']:
                try:analysis=analyze_body((directory/'responses'/meta['body_ref']).read_bytes(),payload)
                except (ValueError,TypeError,KeyError) as e:analysis={'classification':'SCHEMA_OR_JSON_ERROR','reason':type(e).__name__}
            classification=(analysis or {}).get('classification','INCOMPLETE_CAPTURE_OR_TRANSPORT_FAILURE')
            if not ran or entry.get('status')!='accepted':
                if entry.get('reason')!='ValueError:diagnostic_contract_rejected':
                    classification='INCOMPLETE_CAPTURE_OR_TRANSPORT_FAILURE'
            sample={'index':index+1,'classification':classification,'capture':meta,'dispatcher':entry,'analysis':analysis}
            sample['archive_return_wall_s']=getattr(transport._capture_state,'archive_return_wall_s',None)
            report['samples'].append(sample);report['classification']=classification
            proceed=classification=='NO_MISMATCH_IN_BOUNDED_SAMPLE' and index<2 and entry.get('status')=='accepted'
            work.result(sample,proceed)
            if not proceed:break
    except Exception as error:
        # Exception messages are not serialized; never leak credentials through an error.
        report['failure_type']=type(error).__name__
        report['failure_code']='campaign_already_open' if str(error)=='campaign_already_open' else 'preflight_or_execution_failure'
        if work.state['reservations']:report['classification']='INCOMPLETE_CAPTURE_OR_TRANSPORT_FAILURE'
        if activated:work.stop(report['classification'])
        else:
            report['classification']='RESUME_REJECTED_BEFORE_SEND'
            report['resume_rejection_code']=str(error) if isinstance(error,ValueError) and str(error).startswith('resume_') else 'resume_preflight_failure'
    finally:
        if cap:
            report['real_key_read']=bool(capability_factory is None and cap.key_file_read)
            report['credential_read_attempted']=bool(capability_factory is None and cap.key_read_attempted)
            report['budget_after']=cap.budget.summary();cap.close()
        report['work_state']=work.state;work.close()
        with (report_dir/'execution.json').open('x',encoding='utf-8') as f:
            json.dump(report,f,ensure_ascii=False,indent=2,default=str)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--approved-bounded-score-20260920',action='store_true')
    parser.add_argument('--resume-blocked-before-send',action='store_true')
    parser.add_argument('--expected-state-sha256')
    args=parser.parse_args()
    if not args.approved_bounded_score_20260920:raise PermissionError('explicit_bounded_authorization_required')
    report_dir=ROOT/'artifacts/score-raw-capture'/WORK_ID
    resume=None
    if args.resume_blocked_before_send:
        if not args.expected_state_sha256:parser.error('resume requires --expected-state-sha256')
        resume={'expected_state_sha256':args.expected_state_sha256,
                'previous_execution':report_dir/'execution.json','previous_budget':report_dir/'budget-after.json'}
        report_dir=report_dir/'resume-20260920-01'
    elif args.expected_state_sha256:parser.error('state hash requires explicit resume')
    r=run(ROOT/'artifacts/local-private'/WORK_ID,report_dir,resume=resume)
    print(json.dumps({'classification':r['classification'],'samples':len(r['samples']),
                      'real_key_read':r['real_key_read'],'failure_code':r.get('failure_code')}))
