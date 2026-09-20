import copy
import json
import math
import sqlite3
from decimal import Decimal
import pytest
from context_fields.score_policy import C0,CD,validate_dominance_answer,policy_record
from context_fields.jev import DominanceReader,mock_response
from context_fields.evaluation_status import EvaluationStatus,at
from context_fields.structured_results import fields_for
from context_fields.live import CampaignBudget
from context_fields.work_budget import WorkBudget,LIMITS
from context_fields.dispatcher import Dispatcher,MockTransport,unique_json_object
from test_contracts import populated,read,tiny_request,release

Q={'type':'score','criteria':['zero','one','two','three','four']}
def answer(score=2.69):
    return {'type':'score','score':score,'confidence':.8,'legend':dict(enumerate([]))|{str(i):v for i,v in enumerate(Q['criteria'])},'probabilities':{'0':0.,'1':0.,'2':.3,'3':.7,'4':0.}}

def test_cd_01_02_independent_decimal():
    a=answer();before=copy.deepcopy(a)
    with pytest.raises(ValueError,match='score_expectation'):validate_dominance_answer(a,Q,C0)
    d=validate_dominance_answer(a,Q,CD)['score_derivation']
    expected=sum(Decimal(k)*Decimal(str(v)) for k,v in a['probabilities'].items())
    assert expected==Decimal('2.7') and d['selected_score']==pytest.approx(float(expected))
    assert d['numeric_candidate_pct']==pytest.approx(67.5) and d['numeric_delta']==pytest.approx(-.01)
    assert d['warning_codes']==['NUMERIC_DISCREPANCY'] and a==before
    f=fields_for('DominanceReader',{'kind':'dominance_evaluation','adoption_policy_id':CD,'numeric_diagnostics':{'q':d}},'r:1:0')
    assert next(x for x in f if x['id']=='public')['value'] is None

def test_cd_03_extreme_difference():
    a=answer(4);a['probabilities']={str(i):float(i==0) for i in range(5)}
    d=validate_dominance_answer(a,Q,CD)['score_derivation']
    assert d['selected_score']==0 and d['numeric_discrepancy']

@pytest.mark.parametrize('sign',[-1,1])
@pytest.mark.parametrize('delta',[math.nextafter(1e-6,0),1e-6,math.nextafter(1e-6,math.inf),.999e-6,1.001e-6])
def test_cd_04_float_boundary(sign,delta):
    a=answer(2+sign*delta);a['probabilities']={str(i):float(i==2) for i in range(5)}
    flag=abs(2-a['score'])>1e-6
    assert validate_dominance_answer(a,Q,CD)['score_derivation']['numeric_discrepancy']==flag
    if flag:
        with pytest.raises(ValueError):validate_dominance_answer(a,Q,C0)
    else:validate_dominance_answer(a,Q,C0)

@pytest.mark.parametrize('value',[None,True,'2.69',float('nan'),float('inf'),-1,4.01])
def test_cd_05_invalid_score(value):
    a=answer();a['score']=value
    with pytest.raises((ValueError,TypeError)):validate_dominance_answer(a,Q,CD)

@pytest.mark.parametrize('change',['missing','prob_missing','prob_bool','prob_key','legend','sum','duplicate'])
def test_cd_06_07_hard(change):
    a=answer()
    if change=='duplicate':
        with pytest.raises(ValueError,match='duplicate'):json.loads('{"0":0,"0":1}',object_pairs_hook=unique_json_object)
        return
    if change=='missing':del a['score']
    if change=='prob_missing':del a['probabilities']['0']
    if change=='prob_bool':a['probabilities']['0']=False
    if change=='prob_key':a['probabilities']['5']=0
    if change=='legend':a['legend']['0']='different'
    if change=='sum':a['probabilities']['0']=.000002
    with pytest.raises((ValueError,KeyError,TypeError)):validate_dominance_answer(a,Q,CD)

def test_cd_07_normalization():
    a=answer();a['probabilities']['0']=.0000005
    d=validate_dominance_answer(a,Q,CD)['score_derivation']
    assert d['normalized'] and d['normalization_factor']==1/d['probability_sum']
    assert d['mean_used']==pytest.approx(d['mean_before']/d['probability_sum'])

def cd_session():
    s=populated();s.dominance=DominanceReader(s.ledger,CD);return s
def mismatch(raw):raw['answers']['conversation.casual']['score']=4

def test_cd_08_binding_and_model():
    s=cd_session();snap=s.fields.snapshot(6);req=s.dominance.build(snap);raw=mock_response(req)
    assert 'adoption_policy_id' not in req.payload_json and json.loads(req.policy_json)['adoption_policy_id']==CD
    with pytest.raises(ValueError,match='model'):s.dominance.accept(req,raw,snap,s.clock,1,0,1,mode='LIVE-JEV')
    raw['model']=json.loads(req.payload_json)['model'];raw['answers']['unknown']={}
    with pytest.raises(ValueError,match='question'):s.dominance.accept(req,raw,snap,s.clock,1,0,1,mode='LIVE-JEV')
    s.dominance=DominanceReader(s.ledger,C0)
    with pytest.raises(ValueError,match='policy'):s.dominance.accept(req,raw,snap,s.clock,1,0,1)
    with pytest.raises(ValueError):policy_record('unknown')

@pytest.mark.parametrize('assess',[{'assessable':.2,'insufficient':.7,'conflicting':.1},{'assessable':.1,'insufficient':.2,'conflicting':.7},{'assessable':.45,'insufficient':.45,'conflicting':.1}])
def test_cd_09_null(assess):
    s=cd_session()
    def edit(raw):
        mismatch(raw);raw['answers']['conversation.assessability'].update(probabilities=assess,choice=max(assess,key=assess.get))
    results,*_=read(s,raw_edit=edit);r=next(x for x in results if x['space']=='conversation')
    assert all(v is None for v in r['items'].values()) and r['warning_codes']

def test_cd_10_11_14_isolated_warning_and_expiry():
    s=cd_session();before=s.fields.snapshot(6).data()
    results,*_=read(s,raw_edit=mismatch)
    assert all(x['status']=='valid' for x in results)
    assert next(x for x in results if x['space']=='conversation')['items']['casual']==20
    assert s.fields.snapshot(6).data()['spaces']==before['spaces']
    assert s.fields.snapshot(6).data()['root_allocations']==before['root_allocations']
    release(s,11);assert all(v is None for v in s.dominance.display(s.clock,1)['conversation']['items'].values())

def test_cd_12_warning_does_not_hide_hard_global_stop():
    s=cd_session();snap=s.fields.snapshot(6);req=s.dominance.build(snap);raw=mock_response(req);mismatch(raw)
    raw['answers']['place.choice']['probabilities']['day']=2
    transport=MockTransport(lambda r:raw);transport.external=True;d=Dispatcher(transport)
    def callback(raw,a,b):
        values=s.dominance.accept(req,raw,snap,s.clock,1,a,b)
        assert next(x for x in values if x['space']=='conversation')['warning_codes']
        if any(x['status']=='error' for x in values):raise ValueError('live_readout_schema')
    d.submit(req,0,'test',callback);d.run_one(0)
    assert d.stopped and d.log[-1]['status']=='error'

def test_cd_15_status_cursor_and_sticky_reason():
    db=sqlite3.connect(':memory:');b=EvaluationStatus(db)
    for seq,kind,p in [(1,'evaluation_requested',{}),(2,'dispatcher_result',{'status':'error','reason':'ValueError:live_readout_schema'}),(3,'evaluation_end',{'reason':'range_complete'}),(4,'stop',{})]:
        b.consume({'event_seq':seq,'kind':kind,'payload':p})
    assert at(db,0)['state']=='unknown' and at(db,1)['state']=='waiting'
    assert at(db,4)['event_seq']==2 and '応答形式' in at(db,4)['label']

def test_cd_20_work_budget_no_restart_or_refund(tmp_path,monkeypatch):
    c=CampaignBudget(tmp_path/'campaign.json',phase='demo')
    try:
        w=WorkBudget(c,tmp_path/'work','fixture',{});w.start('one');req=tiny_request()
        w.reserve(req);assert c.attempts==1
        with pytest.raises(ValueError):WorkBudget(c,tmp_path/'work','fixture',{})
        with pytest.raises(ValueError):w.start('two')
        monkeypatch.setitem(LIMITS,'attempts',1)
        with pytest.raises(ValueError,match='exhausted'):w.reserve(req)
        assert c.attempts==1
    finally:c.close()

def test_cd_20_storage_error_stops_dispatcher():
    class Broken:
        def reserve(self,r):raise OSError('fixture')
    t=MockTransport(lambda r:pytest.fail('must not send'));d=Dispatcher(t,Broken())
    d.submit(tiny_request(),0,'test',lambda *a:None);d.run_one(0)
    assert d.stopped and d.log[-1]['status']=='budget_stop'

def test_cd_capture_production_construction_and_drain(tmp_path):
    from types import SimpleNamespace
    from context_fields.live import LiveCapability
    from context_fields.session import Session
    from context_fields.local_pipeline import LocalPipeline
    from test_score_raw_capture import FakeConnection,FakeResponse,body,request
    import time,hashlib
    key=tmp_path/'fake-key';key.write_text('JEV_API_KEY=DUMMY_ONLY')
    cap=LiveCapability(approved=True,phase='demo',key_file=key,campaign_file=tmp_path/'campaign.json')
    try:
        original=cap.transport;data=body().replace(b'"score": 2',b'"score": 1, "score": 2')
        conn=FakeConnection(FakeResponse(data))
        cap.transport=lambda **kw:original(connection_factory=lambda *a,**k:conn,**kw)
        s=Session(mode='LOCAL',adoption_policy_id=CD)
        pipe=LocalPipeline.__new__(LocalPipeline);pipe.s=s;pipe.start=0;pipe.pending={};pipe.waiting={};pipe.decode_future=None
        s.local_pipeline=pipe;s.media_info={}
        s.enable_live(cap,capture_directory=tmp_path/'raw',work_id='fixture')
        now=time.monotonic();s.dispatcher.submit(request(),now,'test',lambda *a:pytest.fail('duplicate must not be accepted'))
        s.dispatcher.pump()
        deadline=time.monotonic()+1
        while s.dispatcher.workers and time.monotonic()<deadline:time.sleep(.001)
        s.dispatcher.drain()
        assert s.dispatcher.stopped and conn.calls==1 and conn.closed
        meta=s.dispatcher.log[-1]['raw_capture']
        assert (tmp_path/'raw'/meta['body_ref']).read_bytes()==data and meta['complete']
        assert meta['request_sha256']==hashlib.sha256(request().payload_json.encode()).hexdigest()
        pipe.waiting={'SpeechASR':(None,{'timing':{}},False)}
        s.begin_drain('range_complete')
        assert not pipe.waiting and not pipe.accepting
        assert not pipe._submit('SpeechASR',None,{},now)
        assert not s.stopped # In-flight callbacks retain their original deadlines until explicit stop.
        s.dispatcher.close()
    finally:cap.close()

@pytest.mark.parametrize('fault',['epoch','scope','ttl','deadline','dependency','profile_binding'])
def test_cd_08_10_existing_publication_gates(fault):
    from dataclasses import replace
    s=cd_session();snap=s.fields.snapshot(6);req=s.dominance.build(snap);raw=mock_response(req);mismatch(raw)
    scope=s.fields.scope_revision;completed=1
    if fault=='epoch':s.clock.epoch+=1
    if fault=='scope':scope+=1
    if fault=='ttl':s.clock.media_s=10.001
    if fault=='deadline':completed=5.001
    if fault=='dependency':
        deps=s.dominance.request_dependencies[next(iter(s.dominance.request_dependencies))]
        s.ledger.invalidate([next(iter(deps))],'fixture_new_version')
    if fault=='profile_binding':
        binding=json.loads(req.policy_json);binding['profiles']={};req=replace(req,policy_json=json.dumps(binding))
    with pytest.raises(ValueError):s.dominance.accept(req,raw,snap,s.clock,scope,0,completed)

def test_cd_20_remaining_campaign_and_ambiguous_intent(tmp_path):
    c=CampaignBudget(tmp_path/'campaign.json',phase='demo')
    try:
        c.attempts=240
        w=WorkBudget(c,tmp_path/'work','fixture',{});w.start('one')
        with pytest.raises(ValueError,match='shared_budget'):w.reserve(tiny_request())
        assert c.attempts==240 and w.state['reservations'][0]['status']=='intent'
        with pytest.raises(ValueError,match='work_not_running'):w.reserve(tiny_request())
    finally:c.close()

def test_cd_15_normal_end_kept_during_shutdown():
    db=sqlite3.connect(':memory:');b=EvaluationStatus(db)
    for seq,kind,p in [(1,'evaluation_requested',{}),(2,'evaluation_end',{'reason':'range_complete'}),(3,'dispatcher_result',{'status':'accepted'}),(4,'stop',{})]:
        b.consume({'event_seq':seq,'kind':kind,'payload':p})
    assert at(db,4)['state']=='ended' and at(db,4)['event_seq']==2
