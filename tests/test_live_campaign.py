"""Dummy-only live checks: no real keys, sockets, or models."""
import json
from pathlib import Path
import pytest
from context_fields.live import CampaignBudget,LiveCapability
from context_fields.jev import EvidenceRequest

def request(units=1):
    return EvidenceRequest(json.dumps({'questions':{'test':{'type':'noul'}}}),tuple(str(i) for i in range(units)))

def test_no_capability_rejects_before_reading_key(tmp_path):
    with pytest.raises(PermissionError):LiveCapability(key_file=tmp_path/'never-read',campaign_file=tmp_path/'budget.json')
    assert not list(tmp_path.iterdir())

def test_campaign_initial_and_demo_share_durable_debits(tmp_path):
    path=tmp_path/'budget.json';b=CampaignBudget(path)
    for _ in range(20):b.reserve(request())
    with pytest.raises(ValueError,match='initial_connection'):b.reserve(request())
    b.close()
    demo=CampaignBudget(path,'demo')
    assert demo.attempts==demo.units==20
    for _ in range(220):demo.reserve(request())
    with pytest.raises(ValueError,match='shared_budget'):demo.reserve(request())
    demo.close()
    resumed=CampaignBudget(path,'demo');assert resumed.attempts==240;resumed.close()

def test_campaign_one_process_lock_and_request_limit(tmp_path):
    path=tmp_path/'budget.json';b=CampaignBudget(path)
    try:
        with pytest.raises(ValueError,match='campaign_already'):CampaignBudget(path)
        with pytest.raises(ValueError,match='request_limit'):b.reserve(request(4))
        assert b.attempts==0
    finally:b.close()

def test_backend_key_reader_only_explicit_dummy_file(tmp_path):
    key=tmp_path/'dummy.env';key.write_text('JEV_API_KEY=dummy-not-real\nUNRELATED=ignored\n',encoding='utf-8')
    cap=LiveCapability(approved=True,key_file=key,campaign_file=tmp_path/'budget.json')
    try:assert cap.transport().external
    finally:cap.close()

def test_dummy_two_roles_reach_fields_and_replay(tmp_path):
    from types import SimpleNamespace
    from context_fields.session import Session
    from context_fields.media import Asset,probe
    from context_fields.local_pipeline import LocalPipeline
    from context_fields.dispatcher import MockTransport
    from context_fields.jev import mock_response
    from context_fields.replay import Replay
    path=Path(__file__).parents[1]/'fixtures/local-diagnostic.mp4'
    asset=Asset('dummy-live',path,path.name,path.stat().st_size,'dummy',probe(path))
    models=SimpleNamespace(public=lambda:{'models':{k:'dummy' for k in ('SpeechASR','FrameInterpreter','TextContext')}},status='ready')
    s=Session(mode='LOCAL');s.local_pipeline=LocalPipeline(s,asset,models)
    transport=MockTransport(lambda r:{**mock_response(r),'model':'jev-1.13.0'});transport.external=True
    budget=CampaignBudget(tmp_path/'budget.json')
    cap=SimpleNamespace(transport=lambda:transport,budget=budget)
    try:
        s.enable_live(cap)
        import time
        virtual=[0.];s.dispatcher.clock=lambda:virtual[0];s.dispatcher.token_time=0
        def collect():
            s.dispatcher.pump(virtual[0])
            deadline=time.monotonic()+1
            while s.dispatcher.workers and time.monotonic()<deadline:time.sleep(.001)
            s.dispatcher.drain()
        s.clock.strict_continuity=False  # Unit tests publish explicit slices; continuity tested separately.
        s.clock.notify(s.clock.epoch,1,2,2,'playing',0,audio_presented_s=2)
        frame=s.local_pipeline.decoder.frame(0,1)
        s.local_pipeline._visual({'description':'dummy lamp','scene':'unknown','time_of_day':'day','raw':'dummy'}, {'frame':frame,'scope':s.fields.shot},0)
        collect()
        assert any(r.get('mode')=='LIVE-JEV' for r in s.ledger.records.values())
        snap=s.fields.snapshot(2);req=s.dominance.build(snap)
        assert req and req.role=='dominance'
        s.dispatcher.submit(req,1,'dummy-read',lambda raw,a,b:s.dominance.accept(req,raw,snap,s.clock,s.fields.scope_revision,a,b,mode='LIVE-JEV'))
        virtual[0]=1;collect()
        before=s.view();assert before['external']['attempts']==2
        assert before['readouts']['place']['mode']=='LIVE-JEV'
        replay=Replay(s.ledger.events);assert replay.frames
        count=transport.calls;replay.at(event_cursor=replay.frames[-1]['event_seq']);assert transport.calls==count
        with pytest.raises(ValueError,match='fresh_selected'):s.enable_live(cap)
    finally:s.stop();s.close_remote();s.local_pipeline.close();budget.close()
