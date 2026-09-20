import copy,json,time,threading,hashlib
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import pytest
from context_fields.parallel_dispatcher import ParallelDispatcher
from context_fields.dispatch_policy import DispatchPolicy,P1
from context_fields.dispatcher import MockTransport,Budget,ResultDiscarded
from context_fields.question_batch import WireRequest,split,merge,compatibility
from context_fields.config import json_text
from context_fields.score_policy import CD,policy_record
from context_fields.evaluation_lists import page,evaluation_at
from test_h1_activity import make

def req(i=0,role='evidence',questions=3):
    state={'unit0':{'observation_id':'o'+str(i),'quoted_data':'fixture '+str(i),'revision':1}}
    return WireRequest(json_text({'model':'jev-1.13.0','state':state,'questions':{f'unit0.q{n}':{'type':'noul','instructions':f'State unit0 test {n}'} for n in range(questions)}}),('o'+str(i),),role)
def response(r):return {'model':'jev-1.13.0','answers':{q:{'type':'noul','noul':.5} for q in json.loads(r.payload_json)['questions']}}
def settle(d,timeout=6):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        d.pump()
        if not d.workers and not any(d.queues.values()):d.drain();return
        time.sleep(.001)
    pytest.fail('fixture dispatcher did not settle')

@pytest.mark.parametrize('concurrency',[1,2,4,8,16,32])
def test_p1_parallel_completion_order_and_single_writer(concurrency):
    active=0;peak=0;lock=threading.Lock();threads=[];order=[]
    def fake(r):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        time.sleep(.05 if r.unit_ids[0]=='o0' else .01)
        with lock:active-=1
        return response(r)
    p=replace(P1,concurrency=concurrency,rps=1000,burst=100)
    d=ParallelDispatcher(MockTransport(fake),policy=p)
    try:
        for i in range(36):d.submit(req(i),time.monotonic(),str(i),lambda *a,i=i:(threads.append(threading.get_ident()),order.append(i)))
        settle(d)
        assert len(order)==36 and len(set(threads))==1 and peak<=concurrency
        if concurrency>1:assert order.index(1)<order.index(0) and peak>1
        if concurrency>=4:assert peak>2
        assert len({x['reservation']['attempt'] for x in d.log})==36
    finally:d.close()

def test_p1_batch_exact_invariants_and_equal_questions():
    r=req();one=replace(P1,request_questions=1,batch=False)
    parts=split(r,one);assert len(parts)==3
    packed=merge(parts,P1)
    assert json.loads(packed.payload_json)==json.loads(r.payload_json) and packed.unit_ids==r.unit_ids
    for changed in (req(2),replace(r,role='dominance'),replace(r,policy_json='{}')):
        with pytest.raises(ValueError):merge([r,changed],P1)
    for context in ({'epoch':2},{'dependencies':{'x':2}},{'cutoff':3},{'authorization_scope':'different'}):
        assert compatibility(r,context)!=compatibility(r,{})

def test_p1_queued_identical_inputs_bundle_without_wait():
    r=req();parts=split(r,replace(P1,request_questions=1));d=ParallelDispatcher(MockTransport(response),policy=replace(P1,rps=1000,burst=100))
    done=[]
    try:
        now=time.monotonic()
        for i,part in enumerate(parts):d.submit(part,now,'piece'+str(i),lambda *a:done.append(1),context={'epoch':1})
        settle(d)
        assert len(d.log)==1 and d.budget.units==1 and d.budget.questions==3 and len(done)==3
    finally:d.close()

def test_p1_stop_race_and_already_started_receive():
    barrier=threading.Event();started=[]
    def fake(r):started.append(r.unit_ids[0]);barrier.wait(1);return response(r)
    d=ParallelDispatcher(MockTransport(fake),policy=replace(P1,concurrency=4,rps=1000,burst=100))
    try:
        for i in range(12):d.submit(req(i),time.monotonic(),str(i),lambda *a:None)
        d.pump()
        deadline=time.monotonic()+1
        while len(started)<4 and time.monotonic()<deadline:time.sleep(.001)
        d.stop();count=len(started);barrier.set();settle(d)
        assert len(started)==count==4 and d.budget.attempts==4
        stopseq=next(e['lifecycle_seq'] for e in d.events if e['stage']=='global_stop')
        assert not any(e['stage']=='sent' and e['lifecycle_seq']>stopseq for e in d.events)
    finally:barrier.set();d.close()

def test_p1_hard_error_stops_queued_no_refund():
    def fail(r):raise ValueError('probability_sum')
    d=ParallelDispatcher(MockTransport(fail),policy=replace(P1,concurrency=1,rps=1000,burst=100))
    try:
        for i in range(10):d.submit(req(i),time.monotonic(),str(i),lambda *a:None)
        settle(d);assert d.stopped and d.budget.attempts==1
        assert any(e.get('hard_error') for e in d.events)
    finally:d.close()

def test_p1_atomic_budget_and_unsent_not_error_banner():
    b=Budget(attempts=3);d=ParallelDispatcher(MockTransport(lambda r:(time.sleep(.01),response(r))[1]),b,replace(P1,concurrency=16,rps=1000,burst=100))
    try:
        for i in range(16):d.submit(req(i),time.monotonic(),str(i),lambda *a:None)
        settle(d);assert b.attempts==3 and b.units==3 and b.questions==9
        assert not any(e.get('hard_error') for e in d.events)
    finally:d.close()

def test_p1_no_rejuvenation_duplicate_notification_queue_capacity():
    now=[100.];p=replace(P1,queue_requests=2)
    d=ParallelDispatcher(MockTransport(response),policy=p,clock=lambda:now[0])
    try:
        a=d.submit(req(),100,'same',lambda *a:None,logical_id='same-id')
        assert d.submit(req(),101,'same',lambda *a:None,logical_id='same-id')==a
        now[0]=104;d.submit(req(1),104,'same',lambda *a:None)
        owner=list(d.owners.values())[-1];assert owner['accepted']==100
        now[0]=105;d.pump();assert d.budget.attempts==0 and owner['status']=='cancelled'
    finally:d.close()

def test_p1_dynamic_more_than_two_and_no_field_change():
    from test_m3a import topic
    from test_contracts import release
    from context_fields.session import Session
    s=Session();release(s)
    for i in range(1,7):topic(s,i)
    snap=s.fields.snapshot(6);before=snap.data()['spaces']
    requests=s.dominance.build(snap,dispatch_policy=replace(P1,request_units=12,request_questions=48))
    assert len({u for r in requests for u in r.unit_ids})==6
    assert s.fields.snapshot(6).data()['spaces']==before

def test_p1_lifecycle_rows_and_future_error_not_visible(tmp_path):
    events=[]
    def event(kind,p):
        seq=len(events)+1;events.append({'schema_version':'3','event_seq':seq,'epoch':1,'kind':kind,'payload':p,'elapsed_s':seq,'media_s':0})
    event('display',{'clock':{'state':'paused','epoch':1,'media_s':0}})
    r=req();h=hashlib.sha256(r.payload_json.encode()).hexdigest();common={'logical_id':'actual','role':'evidence','unit_ids':['o0'],'request_hash':h}
    event('jev_lifecycle',{**common,'stage':'requested','request':r.payload_json})
    event('jev_lifecycle',{**common,'stage':'sent','wire_id':'http-one'})
    event('jev_diagnostic_result',{'logical_id':'actual','unit_name':'unit0','results':{'q':{'noul':.5}}})
    event('jev_lifecycle',{'stage':'global_stop','hard_error':True,'reason':'ValueError:probability_sum'})
    event('stop',{})
    index=make(tmp_path,events).index
    a=page(index,'run-123',2)['evaluations']['items'][0];b=page(index,'run-123',3)['evaluations']['items'][0];c=page(index,'run-123',4)['evaluations']['items'][0]
    assert a['row_id']==b['row_id']==c['row_id'] and a['result_seq']==b['result_seq']<c['result_seq']
    assert a['evaluation_state']=='評価待ち' and b['evaluation_state']=='応答待ち'
    assert not any(f['value']==.5 for f in a['result_fields'])
    assert not page(index,'run-123',4)['evaluation_progress'].get('hard_error')
    assert page(index,'run-123',6)['evaluation_progress']['hard_error']

def test_p1_concurrent_raw_capture_same_production_factory(tmp_path):
    from context_fields.live import LiveCapability
    from test_score_raw_capture import FakeConnection,FakeResponse
    class Connection(FakeConnection):
        def request(self,*a,**kw):
            self.calls+=1
            payload=json.loads(kw['body']);data=json.dumps({'model':payload['model'],'answers':{q:{'type':'noul','noul':.5} for q in payload['questions']}}).encode()
            self.response=FakeResponse(data)
    key=tmp_path/'dummy-key';key.write_text('JEV_API_KEY=DUMMY_SECRET_NEVER_COPY')
    cap=LiveCapability(approved=True,phase='demo',key_file=key,campaign_file=tmp_path/'campaign.json')
    try:
        t=cap.transport(capture_directory=tmp_path/'raw',work_id='fixture',connection_factory=lambda *a,**k:Connection(None));t.strict_score_json=True
        d=ParallelDispatcher(t,cap.budget,replace(P1,concurrency=8,rps=1000,burst=100))
        for i in range(16):d.submit(req(i),time.monotonic(),str(i),lambda *a:None)
        settle(d);d.close()
        assert cap.budget.attempts==16 and len(d.log)==16 and not d.hard_stopped
        metas=[json.loads(p.read_text()) for p in (tmp_path/'raw').glob('*.json')]
        assert len(metas)==16 and len({m['request_context']['attempt'] for m in metas})==16
        for m in metas:
            assert m['complete'] and hashlib.sha256((tmp_path/'raw'/m['body_ref']).read_bytes()).hexdigest()==m['body_sha256']
            assert 'DUMMY_SECRET_NEVER_COPY' not in json.dumps(m)
            entry=next(x for x in d.log if x['reservation']['attempt']==m['request_context']['attempt'])
            assert m['request_sha256']==entry['request_hash'] and m['request_context']['unit_ids']==entry['unit_ids']
    finally:cap.close()

def test_p1_shared_work_budget_parallel_no_overspend(tmp_path):
    from context_fields.live import CampaignBudget
    from context_fields.work_budget import WorkBudget
    c=CampaignBudget(tmp_path/'campaign.json',phase='demo')
    try:
        w=WorkBudget(c,tmp_path/'work','fixture',{},limits={'attempts':5,'units':5,'questions':15,'chars':100000});w.start('one')
        d=ParallelDispatcher(MockTransport(response),w,replace(P1,concurrency=16,rps=1000,burst=100))
        for i in range(16):d.submit(req(i),time.monotonic(),str(i),lambda *a:None)
        settle(d);d.close();w.finish('done',d.log)
        assert c.summary()['attempts']==5 and c.questions==15 and len(w.state['reservations'])==5
        with pytest.raises(ValueError):WorkBudget(c,tmp_path/'work','fixture',{})
    finally:c.close()


def test_p1_partially_sent_batch_expires_without_waiting_for_next_rps():
    now=[100.]
    d=ParallelDispatcher(MockTransport(response),policy=replace(P1,request_questions=1,batch=False,burst=1,rps=.01),clock=lambda:now[0])
    try:
        d.submit(req(),100,'one',lambda *a:pytest.fail('incomplete batch callback'))
        d.pump()
        end=time.monotonic()+1
        while d.workers and time.monotonic()<end:time.sleep(.001)
        d.drain();now[0]=105;d.pump()
        assert not any(d.queues.values()) and d.budget.attempts==1
        assert any(e['stage']=='expired' for e in d.events)
    finally:d.close()


def test_p1_production_journal_does_not_duplicate_legacy_request_card(tmp_path):
    from context_fields.session import Session
    from test_contracts import release
    s=Session();release(s)
    s.dispatcher=ParallelDispatcher(MockTransport(response));s.dispatcher.before_collect=s.flush_lifecycle
    try:
        obs,h=s.observation(1,'place','day','fixture')
        s.enqueue_evidence(obs,h,time.monotonic())
        index=make(tmp_path,s.ledger.events).index
        result=page(index,'run-123',len(s.ledger.events))
        assert result['evaluations']['total']==1
    finally:s.dispatcher.close()


def test_p1_split_request_tracks_each_unit_in_saved_lifecycle(tmp_path):
    events=[]
    def event(kind,p):
        n=len(events)+1;events.append({'schema_version':'3','event_seq':n,'epoch':1,'kind':kind,'payload':p,'elapsed_s':n,'media_s':0})
    event('display',{'clock':{'state':'paused','epoch':1,'media_s':0}})
    payload=json_text({'model':'jev-1.13.0','state':{'units':{'a':{'facet':'a'},'b':{'facet':'b'}}},
        'questions':{'a.q':{'type':'noul'},'b.q':{'type':'noul'}}})
    common={'logical_id':'split','role':'dominance','request_hash':hashlib.sha256(payload.encode()).hexdigest()}
    event('jev_lifecycle',{**common,'stage':'requested','request':payload})
    event('jev_lifecycle',{**common,'stage':'batch_finalized','wire_id':'wire-a','question_ids':['a.q']})
    event('jev_lifecycle',{**common,'stage':'sent','wire_id':'wire-a'})
    event('jev_lifecycle',{**common,'stage':'unsent','reason':'global_failure'})
    event('jev_lifecycle',{**common,'stage':'error','wire_id':'wire-a','reason':'probability_sum'})
    index=make(tmp_path,events).index
    sent={r['unit_name']:r['evaluation_state'] for r in page(index,'run-123',4)['evaluations']['items']}
    assert sent=={'a':'応答待ち','b':'評価待ち'}
    cancelled={r['unit_name']:r['evaluation_state'] for r in page(index,'run-123',5)['evaluations']['items']}
    assert cancelled=={'a':'一部送信・中止','b':'未送信'}
    failed={r['unit_name']:r['evaluation_state'] for r in page(index,'run-123',6)['evaluations']['items']}
    assert failed=={'a':'評価エラー','b':'未送信'}
