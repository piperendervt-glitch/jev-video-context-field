import copy
import json
import math
from concurrent.futures import ThreadPoolExecutor
import pytest
from context_fields.clock import MediaClock, root_key, pts_seconds
from context_fields.config import CONFIG, json_text
from context_fields.fields import Hypothesis
from context_fields.session import Session
from context_fields.jev import (EvidenceRequest, DominanceRequest, mock_response, validate_answer,
                                distribution, rubric, DominanceReader)
from context_fields.dispatcher import Budget, Dispatcher, MockTransport, HttpTransport
from context_fields.inspection import LocalAdmission
from context_fields.replay import Replay, read_events

def release(s, t=6, wall=0):
    s.clock.notify(s.clock.epoch,s.clock.seq+1,t,t,"playing",wall,audio_presented_s=t)

def add(s, stamp=1, space="conversation", value="business", relation=None):
    obs,h = s.observation(stamp,space,value,"fixture quoted evidence")
    request = s.evidence.build([obs],[h])
    series=obs["id"]
    g=s.evidence.generation(series)
    raw=mock_response(request,relation)
    ev=s.evidence.accept(request,raw,[h],g,series,0,0,s.clock.media_s)[0]
    c=s.fields.contribute(ev["id"],h.id,s.clock.media_s,0)
    return obs,h,ev,c

def populated():
    s=Session();release(s)
    for space,value in (("conversation","business"),("place","day"),("person","smile")):
        add(s,1,space,value)
    return s

def read(s, snapshot=None, raw_edit=None):
    snapshot=snapshot or s.fields.snapshot(s.clock.media_s)
    request=s.dominance.build(snapshot)
    raw=mock_response(request)
    if raw_edit: raw_edit(raw)
    outputs=s.dominance.accept(request,raw,snapshot,s.clock,s.fields.scope_revision,0,1)
    return outputs,request,raw,snapshot

def tiny_request(role="evidence", text="引用"):
    cls=EvidenceRequest if role=="evidence" else DominanceRequest
    return cls(json_text({"model":"jev-1.13.0","state":text,"questions":{"q":{"type":"noul","instructions":"stateの引用を評価"}}}), ("one",))

def test_D01_nonexclusive_score_does_not_modify_field():
    s=populated();before=s.fields.snapshot(6).data()["spaces"]
    outputs,*_=read(s)
    c=next(r for r in outputs if r["space"]=="conversation")
    assert c["items"]=={"casual":20,"business":90}
    assert not c["exclusive"]
    assert s.fields.snapshot(6).data()["spaces"]==before

def test_D02_choice_preserves_all_candidates_and_unknown():
    s=populated();outputs,*_=read(s)
    r=next(r for r in outputs if r["space"]=="place")
    assert r["items"]=={"day":70,"night":10,"twilight":10,"unknown":10}
    assert r["exclusive"]
    empty=Session().dominance.display(MediaClock(),1)
    assert all(v is None for v in empty["place"]["items"].values())

def test_D03_confidence_is_not_score():
    s=populated()
    outputs,*_=read(s,raw_edit=lambda raw:raw["answers"]["conversation.casual"].update(confidence=.99999))
    assert next(r for r in outputs if r["space"]=="conversation")["items"]["casual"]==20

@pytest.mark.parametrize("probs", [{"assessable":.2,"insufficient":.7,"conflicting":.1},
                                  {"assessable":.45,"insufficient":.45,"conflicting":.1},
                                  {"assessable":.1,"insufficient":.2,"conflicting":.7}])
def test_D04_assessability_and_tie_are_null(probs):
    s=populated()
    def edit(raw): raw["answers"]["conversation.assessability"].update(probabilities=probs,choice=max(probs,key=probs.get))
    outputs,*_=read(s,raw_edit=edit)
    r=next(r for r in outputs if r["space"]=="conversation")
    assert all(v is None for v in r["items"].values())
    assert r["raw"]["answers"]["conversation.business"]["score"]==3.6

def test_D04_absent_and_disabled_adapter_no_mock_value():
    s=Session(mode="LOCAL")
    assert s.dominance.build(s.fields.snapshot(0)) is None
    assert all(r["status"]=="disabled" for r in s.view()["readouts"].values())
    assert s.dispatcher.budget.attempts==0

def test_D05_100_readouts_no_roots_mass_or_lifetime_gain():
    s=populated();before=s.fields.snapshot(6).data()
    for _ in range(100):read(s)
    after=s.fields.snapshot(6).data()
    assert before["spaces"]==after["spaces"]
    assert before["root_allocations"]==after["root_allocations"]
    r=next(iter(s.dominance.latest.values()))
    with pytest.raises(ValueError):s.fields.contribute(r["id"],next(iter(s.fields.hypotheses)),6)
    obs=copy.deepcopy(next(v for v in s.ledger.records.values() if v["kind"]=="observation"))
    obs.update(id="forged-observation",dependencies={r["id"]:1})
    with pytest.raises(ValueError,match="self_reinforcement"):s.ledger.add(obs,s.clock)

def test_D06_slow_readout_survives_10hz_snapshots():
    s=populated();snapshot=s.fields.snapshot(6)
    for i in range(1,21):s.fields.snapshot(6+i/10)
    release(s,8)
    read(s,snapshot)
    r=s.dominance.display(s.clock,1)["conversation"]
    assert r["status"]=="valid" and r["age_s"]==2 and r["as_of_media_s"]==6

def test_D07_old_response_epoch_scope_and_subject():
    s=populated();old=s.fields.snapshot(6);release(s,7);new=s.fields.snapshot(7)
    read(s,new);read(s,old)
    assert s.dominance.latest["conversation"]["as_of_media_s"]==7
    assert s.dominance.display(s.clock,1,"different-face")["person"]["status"]=="superseded"
    s.fields.cut(6.5,7)
    with pytest.raises(ValueError,match="old_epoch_or_scope"):read(s,old)
    s.seek(50)
    with pytest.raises(ValueError,match="old_epoch_or_scope"):read(s,new)

def test_D08_atomic_asr_split_merge_invalidates_entire_chain():
    s=Session();release(s)
    asr,h=s.observation(1,"conversation","business","ASR old")
    asr_id=asr["id"]
    derived=copy.deepcopy(asr);derived.update(id="text-derived",dependencies={asr_id:1},agent="TextContext")
    s.ledger.add(derived,s.clock)
    req=s.evidence.build([derived],[h]);g=s.evidence.generation("derived")
    ev=s.evidence.accept(req,mock_response(req),[h],g,"derived",0,0,6)[0]
    c=s.fields.contribute(ev["id"],h.id,6)
    read(s)
    r=s.dominance.latest["conversation"]
    children=[]
    for n in range(2):
        child=copy.deepcopy(asr);child.update(id=f"asr-split-{n}",revision=2,supersedes=[asr_id]);children.append(child)
    s.ledger.replace([asr_id],children,s.clock)
    assert not {asr_id,derived["id"],ev["id"],c["id"],r["id"]}&s.ledger.active
    assert s.fields.snapshot(6).data()["spaces"]["conversation"]["UNKNOWN"]==1
    assert s.dominance.display(s.clock,1)["conversation"]["status"]=="superseded"
    merged=copy.deepcopy(asr);merged.update(id="asr-merged",revision=3,supersedes=[x["id"] for x in children])
    s.ledger.replace([x["id"] for x in children],[merged],s.clock)
    assert "asr-merged" in s.ledger.active
    assert not {x["id"] for x in children}&s.ledger.active

def test_atomic_replacement_failure_has_no_partial_effect():
    s=Session();release(s);obs,*_=add(s)
    child=copy.deepcopy(obs);child.update(id="child",supersedes=[obs["id"]])
    bad=copy.deepcopy(child);bad["id"]="bad";bad["evidence_roots"]=[]
    active=set(s.ledger.active)
    with pytest.raises(ValueError):s.ledger.replace([obs["id"]],[child,bad],s.clock)
    assert s.ledger.active==active and "child" not in s.ledger.records

def test_D09_atomic_shared_budget_under_concurrency():
    budget=Budget(attempts=20,units=20)
    def reserve(i):
        try: budget.reserve(tiny_request("evidence" if i%2 else "dominance"));return True
        except ValueError:return False
    with ThreadPoolExecutor(8) as pool:results=list(pool.map(reserve,range(100)))
    assert sum(results)==20 and budget.attempts==20 and budget.units==20
    assert sum(r["units"] for r in budget.reservations)==20

def test_D10_seek_cannot_read_skipped_audio_or_future():
    c=MediaClock();c.seek(50)
    assert c.window(6,"audio") is None and not c.permits(44,50,"audio",c.epoch)
    c.notify(c.epoch,1,51,51,"playing",0,audio_presented_s=51)
    assert c.window(6,"audio")== (50,51)
    assert not c.permits(49.99,51,"audio",c.epoch)
    assert not c.permits(50,51.001,"audio",c.epoch)

def test_D11_late_cut_invalidates_old_and_misassigned_frames_keeps_audio():
    s=Session();release(s,12.4)
    old,*_=add(s,11.9,"place","day")
    wrong,*_=add(s,12.1,"person","smile")
    audio,*_=add(s,12.1,"conversation","business")
    before=s.fields.snapshot(12.4)
    s.ledger.event("display",{"snapshot":before.data()})
    s.fields.cut(12,12.4)
    assert old["id"] not in s.ledger.active and wrong["id"] not in s.ledger.active
    assert audio["id"] in s.ledger.active
    assert s.fields.snapshot(12.4).data()["spaces"]["place"]["UNKNOWN"]==1
    event=next(e for e in reversed(s.ledger.events) if e["kind"]=="cut")
    assert wrong["id"] in event["payload"]["reanalysis"]
    assert before.data()["spaces"]["place"]["UNKNOWN"]<1

def test_D11_late_old_frame_cannot_be_relabelled_as_new_shot():
    s=Session();release(s,12.4);s.fields.cut(12,12.4)
    obs,h=s.observation(11.9,"place","day","old frame assigned to new shot")
    req=s.evidence.build([obs],[h]);g=s.evidence.generation("late")
    ev=s.evidence.accept(req,mock_response(req),[h],g,"late",0,0,12.4)[0]
    with pytest.raises(ValueError,match="source_shot_mismatch"):
        s.fields.contribute(ev["id"],h.id,12.4)

def test_D12_larger_input_window_not_new_evidence_or_new_tau():
    s=Session();release(s,8)
    obs,h,ev,c=add(s,4,"conversation","business")
    before=s.fields.snapshot(8).data()["spaces"]
    derived=copy.deepcopy(obs);derived.update(id="larger-window",dependencies={obs["id"]:1})
    root=root_key(s.ledger.media_id,1,"audio","01",7)
    derived["input_roots"].append(root)
    derived["root_sources"][root]={"interval":[7,7],"modality":"audio","stream":"01","pts":7000,"time_base":[1,1000]}
    s.ledger.add(derived,s.clock)
    req=s.evidence.build([derived],[h]);g=s.evidence.generation("larger")
    ev2=s.evidence.accept(req,mock_response(req),[h],g,"larger",0,0,8)[0]
    s.fields.contribute(ev2["id"],h.id,8)
    after=s.fields.snapshot(8).data()
    assert len(after["root_allocations"])==1
    assert after["spaces"]["conversation"]["UNKNOWN"]==before["conversation"]["UNKNOWN"]
    assert all(src["tau"]==4 for src in after["spaces"]["conversation"]["hypotheses"][0]["sources"]["support"])

def test_D13_questions_name_target_in_instructions_and_states_are_separate():
    s=populated()
    obs=[o for o in s.ledger.records.values() if o["kind"]=="observation"]
    hs=[s.fields.hypotheses[next(e["hypothesis_id"] for e in s.ledger.records.values() if e["kind"]=="evidence_evaluation" and e["observation_id"]==o["id"])] for o in obs]
    req=s.evidence.build(obs,hs);payload=json.loads(req.payload_json)
    for i,h in enumerate(hs):
        for name in ("relation","relevance","routing"):
            instruction=payload["questions"][f"unit{i}.{name}"]["instructions"]
            assert f"state.unit{i}" in instruction and h.label in instruction and h.subject in instruction
    assert '"C":' not in req.payload_json and "UNKNOWN" not in req.payload_json
    dr=s.dominance.build(s.fields.snapshot(6))
    assert '"C":' in dr.payload_json

def test_D14_replay_reproduces_arrival_expiration_and_values_without_calls(tmp_path):
    path=tmp_path/"session.jsonl";s=Session(path=path)
    for i in range(321):
        s.tick(dict(epoch=1,seq=i,media_s=i/10,video_presented_s=i/10,audio_presented_s=i/10,state="playing"),wall_s=i/10)
    events=read_events(path);replay=Replay(events)
    for event in replay.frames[::17]:
        restored=replay.at(event_cursor=event["event_seq"])
        assert restored==event["payload"]
    assert replay.frames[-1]["payload"]["readouts"]["conversation"]["status"]=="stale"
    assert any(e["payload"]["readouts"]["conversation"]["status"]=="valid" for e in replay.frames)
    assert s.last_payload["external"]=={"attempts":0,"units":0,"usage":None}

@pytest.mark.parametrize("failure",[TimeoutError("timeout"),ValueError("http_429"),ValueError("schema")])
def test_D15_failure_no_retry_and_no_refund(failure):
    def fail(_):raise failure
    transport=MockTransport(fail);d=Dispatcher(transport)
    d.submit(tiny_request(),0,"one",lambda *args:None)
    assert d.run_one(0)
    d.run_one(1);d.run_one(2)
    assert transport.calls==1 and d.budget.attempts==1 and d.budget.units==1
    assert d.log[-1]["status"]=="error"

def test_D15_http_transport_one_actual_attempt_no_redirect_or_sdk_retry():
    connections=[]
    class Conn:
        def __init__(self,*a,**k): self.requests=0;connections.append(self)
        def request(self,*a,**k):self.requests+=1
        def getresponse(self):return self
        status=429
        def read(self,n):return b'{}'
        def close(self):pass
    t=HttpTransport("dummy-not-a-key",authorized=True,connection_factory=Conn)
    with pytest.raises(ValueError,match="http_429"):t.send(tiny_request(),1)
    assert len(connections)==1 and connections[0].requests==1 and t.retries==0
    with pytest.raises(PermissionError):HttpTransport("dummy")

@pytest.mark.parametrize("edit",[
    lambda raw:raw["answers"]["conversation.business"].update(score=3.1),
    lambda raw:raw["answers"]["conversation.assessability"]["probabilities"].pop("insufficient"),
    lambda raw:raw["answers"]["conversation.business"]["legend"].update({"4":"different rubric"}),
    lambda raw:raw["answers"]["conversation.business"].update(score=float('nan')),
])
def test_D16_invalid_unit_rejected_without_poisoning_other_units(edit):
    s=populated();outputs,*_=read(s,raw_edit=edit)
    c=next(r for r in outputs if r["space"]=="conversation")
    assert c["status"]=="error" and all(v is None for v in c["items"].values())
    assert next(r for r in outputs if r["space"]=="place")["status"]=="valid"

def test_mass_nonnegative_finite_and_root_caps_all_spaces():
    s=populated()
    for t in (6,12,35,90,170):
        snap=s.fields.snapshot(t).data()
        for space in snap["spaces"].values():
            values=[space["UNKNOWN"]]+[r["C"] for r in space["hypotheses"]]
            assert all(math.isfinite(v) and v>=0 for v in values)
            assert abs(sum(values)-1)<=1e-9
        assert all(x["allocated"]<=1 for x in snap["root_allocations"])

def test_root_cap_many_hypotheses_and_duplicates():
    s=Session();release(s)
    obs,h,ev,c=add(s)
    before=s.fields.snapshot(6).data()["spaces"]["conversation"]["UNKNOWN"]
    for i in range(100):
        clone=copy.deepcopy(ev);clone.update(id=f"copy:{i}",series=f"copy:{i}")
        s.ledger.add(clone);s.fields.contribute(clone["id"],h.id,6)
    assert s.fields.snapshot(6).data()["spaces"]["conversation"]["UNKNOWN"]==before
    for i in range(30):
        new_h=Hypothesis(f"h{i}","conversation","session",h.subject,h.facet,f"value{i}",h.reference_mode,f"label{i}")
        s.fields.register(new_h)
        clone=copy.deepcopy(ev);clone.update(id=f"multi:{i}",hypothesis_id=new_h.id)
        s.ledger.add(clone);s.fields.contribute(clone["id"],new_h.id,6)
    assert all(x["allocated"]<=1 for x in s.fields.snapshot(6).data()["root_allocations"])

def test_zero_candidate_non_dilution_alias_and_limit():
    s=populated();before=s.fields.snapshot(6).data()["spaces"]["person"]["UNKNOWN"]
    base=next(h for h in s.fields.hypotheses.values() if h.space=="person")
    alias=Hypothesis("alias",base.space,base.scope,base.subject,base.facet,base.value,base.reference_mode,base.label)
    assert s.fields.register(alias)==base.id
    for i in range(127):s.fields.register(Hypothesis(f"zero{i}","person",base.scope,base.subject,base.facet,str(i),base.reference_mode,str(i)))
    assert s.fields.register(Hypothesis("overflow","person",base.scope,base.subject,base.facet,"overflow",base.reference_mode,"overflow")) is None
    assert s.fields.snapshot(6).data()["spaces"]["person"]["UNKNOWN"]==before

def test_pure_evaporation_fixed_set_and_no_retention():
    s=populated();last={space:0 for space in CONFIG["half_life"]}
    for t in range(6,100):
        for space,data in s.fields.snapshot(t).data()["spaces"].items():
            assert data["UNKNOWN"]>=last[space];last[space]=data["UNKNOWN"]

def test_select_max_before_decay_not_new_weak_value():
    s=Session();release(s,7);old,h,ev,c=add(s,0,"place","day",{"support":1,"contradict":0,"insufficient":0})
    newer=copy.deepcopy(old);newer.update(id="weak-new")
    root=newer["evidence_roots"][0];newer["citations"][root]=1
    newer["root_sources"][root].update(interval=[0,1],pts=1000)
    s.ledger.add(newer,s.clock)
    req=s.evidence.build([newer],[h]);g=s.evidence.generation("weak")
    new_ev=s.evidence.accept(req,mock_response(req,{"support":.999,"contradict":0,"insufficient":.001}),[h],g,"weak",0,0,7)[0]
    s.fields.contribute(new_ev["id"],h.id,7)
    selected=s.fields.snapshot(7).data()["spaces"]["place"]["hypotheses"][0]["sources"]["support"][0]
    assert selected["tau"]==0 and selected["evaluation_id"]==ev["id"]

def test_pause_ttl_exact_boundary_and_new_snapshot_immutable():
    s=populated();outputs,_,_,snap=read(s)
    release(s,10)
    assert s.dominance.display(s.clock,1)["conversation"]["status"]=="valid"
    s.clock.notify(1,99,10,10,"paused",100,audio_presented_s=10)
    original=s.fields.snapshot(10).data()["spaces"]
    s.clock.notify(1,100,10,10,"paused",200,audio_presented_s=10)
    assert s.fields.snapshot(10).data()["spaces"]==original
    assert s.dominance.display(s.clock,1)["conversation"]["status"]=="valid"
    release(s,10.001)
    assert s.dominance.display(s.clock,1)["conversation"]["status"]=="stale"
    changed=snap.data();changed["spaces"]["person"]["UNKNOWN"]=99
    assert snap.data()["spaces"]["person"]["UNKNOWN"]!=99

def test_clock_order_pause_future_root_and_pts():
    c=MediaClock();assert c.notify(1,1,2,2,"playing",0)
    assert not c.notify(1,1,3,3,"playing",1)
    assert not c.notify(0,2,3,3,"playing",1)
    assert c.window(6,"audio") is None
    c.notify(1,2,2,2,"paused",1)
    with pytest.raises(ValueError,match="paused_clock"):c.notify(1,3,3,3,"paused",2)
    assert pts_seconds(90090,[1,90000])==pytest.approx(1.001)
    assert root_key("m",1,"video","s",2).endswith(":1")
    assert root_key("m",1,"video","s",1.999).endswith(":0")

def test_generation_rejects_late_old_evaluation():
    s=Session();release(s);obs,h=s.observation(1,"conversation","business","quoted")
    req=s.evidence.build([obs],[h]);old=s.evidence.generation("series");s.evidence.generation("series")
    with pytest.raises(ValueError,match="old_generation"):
        s.evidence.accept(req,mock_response(req),[h],old,"series",0,0,6)

def test_budget_counts_full_unicode_json_and_limits():
    req=tiny_request(text="あ😀"*200);b=Budget();b.reserve(req)
    assert b.chars==len(req.payload_json) and b.bytes==len(req.payload_json.encode()) and b.bytes>b.chars
    with pytest.raises(ValueError,match="request_limit"):b.reserve(tiny_request(text="あ"*12000))
    with pytest.raises(ValueError,match="request_limit"):b.reserve(EvidenceRequest(req.payload_json,tuple(range(4))))
    p=json.loads(req.payload_json);p["questions"]={str(i):{"type":"noul","instructions":"state"} for i in range(13)}
    with pytest.raises(ValueError):b.reserve(EvidenceRequest(json_text(p),("a",)))

def test_dispatcher_role_rates_queue_coalescing_deadline_and_cancel():
    t=MockTransport(lambda r:{});d=Dispatcher(t)
    for role in ("evidence","dominance"):
        for i in range(3):d.submit(tiny_request(role),0,f"{role}{i}",lambda *args:None)
    assert d.run_one(0) and d.run_one(0)
    assert not d.run_one(.5)
    assert d.run_one(1)
    assert d.run_one(2)
    assert d.last_role["dominance"]==2
    d.submit(tiny_request(),2,"replace",lambda *args:None)
    d.submit(tiny_request(),2.1,"replace",lambda *args:None)
    assert sum(p.series=="replace" for p in d.queues["evidence"])==1
    before=d.budget.attempts;d.run_one(10)
    assert d.budget.attempts==before
    d.stop()
    with pytest.raises(ValueError,match="stopped"):d.submit(tiny_request(),11,"x",lambda *a:None)

def test_dispatcher_expired_in_flight_budget_not_refunded():
    d=Dispatcher(MockTransport(lambda r:{}));called=[]
    d.submit(tiny_request(),0,"x",lambda *a:called.append(True));d.run_one(1,finish_clock=lambda:5.001)
    assert not called and d.budget.attempts==1
    assert d.log[-1]["status"]=="error"

def test_local_reader_admission_80_percent_and_duplicate_suppression():
    a=LocalAdmission()
    assert not a.admit(0,reader=True,identity="one")[0]
    for i in range(4):assert a.admit(i)[0]
    assert a.admit(4,reader=True,identity="one")[0]
    assert not a.admit(4.1,reader=True,identity="one")[0]
    assert not a.admit(4.2,reader=True,identity="two")[0]
    assert not a.admit(15,reader=True,identity="two")[0]

def test_fixture_context_loop_and_root_integrity():
    s=Session()
    for i in range(201):s.tick(dict(epoch=1,seq=i,media_s=i/10,video_presented_s=i/10,audio_presented_s=i/10,state="playing"),i/10)
    loops=[e for e in s.ledger.events if e["kind"]=="inspection_completed"]
    assert loops
    for e in loops:
        p=e["payload"];job=s.ledger.records[p["job_id"]];obs=s.ledger.records[p["observation_id"]]
        assert job["source_roots"]==obs["evidence_roots"] and p["new_roots"]==0
        assert p["job_id"] not in obs["dependencies"]

def test_replay_double_as_of_boundary():
    s=populated();s.view();cursor=len(s.ledger.events);release(s,7);read(s);s.view()
    r=Replay(s.ledger.events)
    assert r.as_of(6,cursor)["snapshot"]["as_of_media_s"]==6
    assert r.as_of(5,cursor) is None
    assert r.as_of(7,cursor)["readouts"]["conversation"]["status"]=="pending"

def test_field_initial_unknown_and_support_contradiction_cancellation():
    s=Session();assert all(v["UNKNOWN"]==1 for v in s.fields.snapshot(0).data()["spaces"].values())
    release(s);obs,h,ev,c=add(s,1,"place","day",{"support":.4,"contradict":.6,"insufficient":0})
    row=s.fields.snapshot(6).data()["spaces"]["place"]["hypotheses"][0]
    assert row["S"]>0 and row["R"]>0 and row["E"]==0 and row["conflict"]

def test_evidence_ttl_and_invalid_lineage():
    s=Session();release(s,12)
    obs,h=s.observation(1,"place","day","old")
    req=s.evidence.build([obs],[h]);g=s.evidence.generation("old")
    assert s.evidence.accept(req,mock_response(req),[h],g,"old",0,0,12)==[None]
    bad=copy.deepcopy(obs);bad.update(id="bad-root",evidence_roots=["made-up"])
    with pytest.raises(ValueError,match="root_lineage"):s.ledger.add(bad,s.clock)

def test_choice_no_clipping_missing_keys_or_nan():
    for p in ({"a":1.1,"b":-.1},{"a":float('nan'),"b":0},{"a":1}):
        with pytest.raises(ValueError):distribution(p,["a","b"])
    p,normalized=distribution({"a":.5000001,"b":.5},["a","b"])
    assert normalized and sum(p.values())==1

def test_multiple_roots_split_mass_and_per_sign_timestamps():
    s=Session();release(s,6)
    obs,h=s.observation(1,"place","day","two separately quoted roots")
    second=copy.deepcopy(obs);second.update(id="two-roots")
    root=root_key(s.ledger.media_id,1,"video","01",3)
    second["input_roots"].append(root);second["evidence_roots"].append(root);second["citations"][root]=3
    second["root_sources"][root]={"interval":[3,3],"modality":"video","stream":"01","pts":3000,"time_base":[1,1000]}
    s.ledger.add(second,s.clock)
    req=s.evidence.build([second],[h]);g=s.evidence.generation("two")
    ev=s.evidence.accept(req,mock_response(req,{"support":.8,"contradict":.2,"insufficient":0}),[h],g,"two",0,0,6)[0]
    s.fields.contribute(ev["id"],h.id,6)
    row=s.fields.snapshot(6).data()["spaces"]["place"]["hypotheses"][0]
    expected_s=.9*.9*.8/2*(2**(-5/12)+2**(-3/12))
    expected_r=.9*.9*.2/2*(2**(-5/12)+2**(-3/12))
    assert row["S"]==pytest.approx(expected_s,abs=1e-12)
    assert row["R"]==pytest.approx(expected_r,abs=1e-12)
    assert row["C"]==pytest.approx((expected_s-expected_r)/(1+expected_s-expected_r),abs=1e-12)

def test_model_adapters_disabled_and_stateless_gated_contract():
    from context_fields.observers import SpeechASR,FrameInterpreter,TextContext
    c=MediaClock()
    for cls in (SpeechASR,FrameInterpreter,TextContext):assert cls().observe({},c)["value"] is None
    c.seek(50)
    adapter=SpeechASR(lambda data:data,model_id="test-model")
    data={"interval":[44,50],"modality":"audio","epoch":c.epoch,"input_roots":[],"dependency_versions":{},"data":"test"}
    with pytest.raises(ValueError,match="unreleased_model_input"):adapter.observe(data,c)

def test_invalid_clock_message_does_not_partially_release_video():
    c=MediaClock()
    with pytest.raises(ValueError,match="future_audio"):c.notify(1,1,2,2,"playing",0,audio_presented_s=3)
    assert c.released=={"audio":[],"video":[]} and c.seq==-1
