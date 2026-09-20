"""M3a contracts. No credentials, models, network or live budget are used."""
import copy
import json
from concurrent.futures import Future
import pytest
from context_fields.session import Session
from context_fields.fields import Hypothesis
from context_fields.jev import mock_response, validate_answer
from context_fields.transcript import TranscriptStore
from context_fields.dispatcher import Dispatcher, MockTransport, ResultDiscarded
from context_fields.replay import Replay
from context_fields.local_pipeline import LocalPipeline
from test_contracts import release, populated, read, tiny_request
from test_local_video import NoModels, ROOT
from context_fields.media import Asset, probe


def topic(s, n=1):
    base,_=s.observation(2,'conversation','business','We discuss body language and posture.',obs_id=f'asr{n}')
    obs=copy.deepcopy(base);obs.update(id=f'topic-obs{n}',agent='TextContext',facet='conversation.topic',
        asr_id=base['id'],quote='body language',dependencies={base['id']:1})
    s.ledger.add(obs,s.clock)
    h=Hypothesis(f'topic-h{n}','conversation','session',base['subject'],'conversation.topic',f'body language {n}','quoted_speech',f'body language {n}')
    s.fields.register(h);s.enqueue_evidence(obs,h,n)
    s.dispatcher.run_one(n)
    s.transcripts.ingest({'text':base['text'],'segments':[{'start_s':1,'end_s':3,'text':base['text']}]},
                         {'start_s':0,'end_s':6},base['id'],[{'root':next(iter(base['citations']))}])
    return base,obs,h


def test_UR01_transcript_correction_split_merge_cancel_and_epoch():
    s=Session();store=s.transcripts
    def put(parts,text='raw original'):
        return store.ingest({'text':text,'segments':[dict(start_s=a,end_s=b,text=t) for a,b,t in parts]}, {'start_s':0,'end_s':6},'asr')
    a=put([(0,4,'raw original')])[0]
    b=put([(0,4,'corrected original')])[0]
    assert (a['segment_id'],a['revision'])==(b['segment_id'],1) and b['revision']==2
    split=put([(0,2,'first'),(2,4,'second')])
    assert len(split)==2 and all(r['supersedes']==[{'segment_id':b['segment_id'],'revision':2}] for r in split)
    merged=put([(0,4,'merged')])[0]
    assert len(merged['supersedes'])==2 and 'words' not in merged
    put([],text='')
    assert all(r['status'] in {'no_speech_recognized','cancelled'} for r in store.public(s.clock.epoch)['current'])
    assert store.versions[0]['text']=='raw original'
    assert all(s.ledger.events[r['available_event_cursor']-1]['kind']=='transcript_version' for r in store.versions)
    s.seek(20);assert not store.public(s.clock.epoch)['current'] and len(store.versions)>0


def test_UR01_transcript_future_and_word_times():
    store=TranscriptStore(Session().ledger)
    with pytest.raises(ValueError,match='outside_released'):
        store.ingest({'text':'x','segments':[{'start_s':0,'end_s':7,'text':'x'}]},{'start_s':0,'end_s':6})
    words=[{'word':'hello','start_s':1,'end_s':1.4}]
    result=store.ingest({'text':'hello','segments':[{'start_s':1,'end_s':2,'text':'hello','words':words}]},{'start_s':0,'end_s':6})
    assert result[0]['words']==words


def test_UR02_unknown_flags_no_semantic_invention():
    s=Session();view=s.view()
    for space in view['unknown_triage']['spaces'].values():
        assert 'NO_OBSERVATION' in {f['reason_code'] for f in space['flags']}
        assert not {'CATEGORY_GAP','FACET_GAP','GENUINE_AMBIGUITY'} & {f['reason_code'] for f in space['flags']}
    release(s);obs,h=s.observation(1,'conversation','business','raw');s.enqueue_evidence(obs,h,0)
    flags=s.view()['unknown_triage']['spaces']['conversation']['flags']
    assert any(f['reason_code']=='EVALUATION_PENDING' and obs['id'] in f['refs'] for f in flags)


def test_UR04_topic_connection_original_ids_no_mass_change():
    s=Session();release(s);base,obs,h=topic(s)
    before=s.fields.snapshot(6).data();request=s.dominance.build(s.fields.snapshot(6))
    assert request and len(request.unit_ids)==1
    p=s.dominance.registry.public()[0]
    assert p['category_ids']==[h.id] and p['runtime_verified'] is False
    assert s.fields.snapshot(6).data()['spaces']==before['spaces']
    body=json.loads(request.payload_json)
    assert h.value in body['questions'][p['id']+'.topic']['instructions']
    # Exact immutable snapshot must match the request.
    snap=s.fields.snapshot(6);req=s.dominance.build(snap)
    out=s.dominance.accept(req,mock_response(req),snap,s.clock,1,0,1)[0]
    assert out['items']=={'topic':50} and out['profile']['revision']==1
    assert not s.dominance.registry.public()[0]['runtime_verified']
    view=s.view();mapped=next(r for r in view['unknown_triage']['unmapped_observation_index'] if r['observation_id']==obs['id'])
    assert mapped['hypothesis_ids']==[h.id] and mapped['transcript_refs'] and mapped['readout_ids']==[out['id']]
    assert mapped['processing_state']=='evaluated' and mapped['semantic_status']=='not_inferred'


def test_UR04_label_without_quote_does_not_register():
    s=Session();release(s);_,obs,h=topic(s)
    s.ledger.records[obs['id']]['quote']='not in original'
    s.dominance.build(s.fields.snapshot(6))
    assert not s.dominance.registry.public()
    flags=s.view()['unknown_triage']['spaces']['conversation']['flags']
    assert any(f['reason_code']=='READOUT_NOT_REGISTERED' for f in flags)


def test_UR05_08_profile_registration_not_evidence_and_score_not_distribution():
    s=populated();topic(s);snap=s.fields.snapshot(6);before=snap.data()['spaces']
    req=s.dominance.build(snap)
    out=s.dominance.accept(req,mock_response(req),snap,s.clock,1,0,1)
    assert s.fields.snapshot(6).data()['spaces']==before
    assert next(r for r in out if r['facet']=='conversation.register')['items']=={'casual':20,'business':90}
    assert all(r['value_kind']=='graded_score_pct' for r in out if r['space']=='conversation')


def test_UR07_09_profile_version_and_seek_invalidation():
    s=Session();release(s);topic(s);snap=s.fields.snapshot(6);req=s.dominance.build(snap)
    pid=req.unit_ids[0];s.dominance.registry.profiles[pid]['revision']=2
    assert s.dominance.accept(req,mock_response(req),snap,s.clock,1,0,1)==[]
    s.seek(30)
    assert s.dominance.registry.public()==[] and s.view()['transcripts']['current']==[]


def test_UR10_dynamic_capacity_rotation_and_shared_request_limit():
    s=Session();release(s)
    for n in range(1,6):topic(s,n)
    seen=set()
    for _ in range(6):
        req=s.dominance.build(s.fields.snapshot(6));assert len(req.unit_ids)<=2
        seen.update(req.unit_ids)
        body=json.loads(req.payload_json);assert len(body['questions'])<=12 and len(req.payload_json)<=12000
    assert len(seen)==5
    req=s.dominance.build(s.fields.snapshot(6));occupied=set(req.unit_ids)
    assert s.dominance.build(s.fields.snapshot(6),occupied=occupied) is None
    assert sum(p['status']=='scheduled' for p in s.dominance.registry.public())<=2


def test_UR11_slot_first_and_latest_wait_survive_coalescing():
    path=ROOT/'fixtures/synthetic.webm';asset=Asset('silent',path,path.name,path.stat().st_size,'',probe(path))
    s=Session(mode='LOCAL');p=LocalPipeline(s,asset,NoModels())
    try:
        p._submit('FrameInterpreter',{}, {'frame':{'media_s':1},'scope':s.fields.shot},10)
        p._submit('FrameInterpreter',{}, {'frame':{'media_s':3},'scope':s.fields.shot},14)
        f=Future()
        p.models.submit=lambda *args,**kwargs:f
        p._dispatch_next(20)
        timing=p.pending['FrameInterpreter'][1]['timing']
        assert timing['slot_wait_s']==10 and timing['latest_input_wait_s']==6
        assert timing['source_media_s']==3 and timing['coalesced_count']==1
    finally:p.close()


def test_A07_local_obsolescence_not_api_failure_but_schema_still_stops():
    d=Dispatcher(MockTransport(mock_response))
    def discard(*args):raise ResultDiscarded('old_epoch')
    d.submit(tiny_request(),0,'old',discard);d.run_one(0)
    assert d.log[-1]['status']=='discarded' and not d.stopped
    def bad(*args):raise ValueError('score_expectation')
    d.submit(tiny_request(),1,'bad',bad);d.run_one(1)
    assert d.stopped and d.budget.attempts==2


def test_A07_unrelated_asr_does_not_invalidate_place_only_readout():
    s=populated();snap=s.fields.snapshot(6);req=s.dominance.build(snap)
    # Force next request's eligible inputs to place only.
    for hid,h in list(s.fields.hypotheses.items()):
        if h.space!='place':
            for cid in list(s.ledger.active):
                if s.ledger.records[cid].get('hypothesis_id')==hid:s.ledger.invalidate([cid],'test_remove')
    snap=s.fields.snapshot(6);req=s.dominance.build(snap)
    assert req.unit_ids==('place',)
    unrelated,_=s.observation(2,'conversation','casual','new ASR')
    s.ledger.invalidate([unrelated['id']],'asr_revision')
    assert s.dominance.accept(req,mock_response(req),snap,s.clock,1,0,1)[0]['status']=='valid'


def test_UR20_age_and_original_schema_error_remain_visible():
    s=populated();snap=s.fields.snapshot(6);req=s.dominance.build(snap);raw=mock_response(req)
    raw['answers']['conversation.casual']['score']=1.95
    s.dominance.accept(req,raw,snap,s.clock,1,0,1)
    release(s,36.6)
    out=s.dominance.display(s.clock,1)['conversation']
    assert out['status']=='stale' and out['original_reason']=='score_expectation' and out['age_s']==pytest.approx(30.6)


def test_UR28_replay_no_retroactive_profiles_transcripts():
    s=Session();release(s);first=s.view();cursor=len(s.ledger.events)
    topic(s);s.dominance.build(s.fields.snapshot(6));s.view()
    replay=Replay(s.ledger.events)
    assert replay.at(event_cursor=cursor)==first
    assert replay.at(event_cursor=cursor)['profiles']==[]
    legacy=copy.deepcopy(s.ledger.events)
    for e in legacy:
        if e['kind']=='display':
            for key in ('transcripts','profiles','unknown_triage'):e['payload'].pop(key,None)
    assert 'transcripts' not in Replay(legacy).at()
    assert replay.record_at('asr1',cursor) is None
    assert replay.record_at('asr1',len(s.ledger.events))['text']=='We discuss body language and posture.'


def test_A08_strict_score_contract_not_relaxed_for_saved_live_failure():
    q={'type':'score','criteria':['a','b','c','d','e']}
    a={'type':'score','confidence':.27,'legend':dict(zip(map(str,range(5)),q['criteria'])),
       'probabilities':{'0':.13,'1':.2,'2':.32,'3':.28,'4':.07},'score':1.95}
    with pytest.raises(ValueError,match='score_expectation'):validate_answer(a,q)


def test_UR11_failed_and_cancelled_jobs_keep_slot_provenance():
    path=ROOT/'fixtures/synthetic.webm';asset=Asset('silent',path,path.name,path.stat().st_size,'',probe(path))
    s=Session(mode='LOCAL');p=LocalPipeline(s,asset,NoModels())
    try:
        f=Future();f.set_exception(ValueError('model_output_not_json'))
        p.pending['FrameInterpreter']=(f,{'timing':{'slot_first_requested_wall_s':10,'accepted_wall_s':20}},s.clock.epoch)
        p.collect(21)
        event=next(e for e in s.ledger.events if e['kind']=='local_model_failed')
        assert event['payload']['slot_first_requested_wall_s']==10 and event['payload']['collected_wall_s']==21
        p._submit('FrameInterpreter',{}, {'frame':{'media_s':1},'scope':s.fields.shot},22)
        p.cancel('session_stopped')
        event=s.ledger.events[-1]
        assert event['kind']=='local_job_cancelled' and event['payload']['phase']=='pre_admission_slot'
        assert not p.waiting
    finally:p.close()


def test_UR31_dynamic_readout_failure_debits_same_durable_budget(tmp_path):
    from context_fields.live import CampaignBudget
    s=Session();release(s);topic(s,1);topic(s,2)
    req=s.dominance.build(s.fields.snapshot(6))
    path=tmp_path/'campaign.json';budget=CampaignBudget(path,phase='demo')
    def fail(_):raise TimeoutError('fake timeout, no network')
    transport=MockTransport(fail);transport.external=True
    d=Dispatcher(transport,budget)
    try:
        d.submit(req,0,'topics',lambda *args:None);d.run_one(0)
        assert d.stopped and budget.attempts==1 and budget.units==2 and transport.calls==1
    finally:budget.close()
    reopened=CampaignBudget(path,phase='demo')
    try:assert reopened.attempts==1 and reopened.units==2
    finally:reopened.close()
