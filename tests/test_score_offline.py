"""SC checks: synthetic/fake transport only, saved data never reclassified as LIVE."""
import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import Future
from decimal import Decimal
import pytest
from context_fields.jev import validate_answer, mock_response
from context_fields.dispatcher import Dispatcher, MockTransport, HttpTransport
from context_fields.response_archive import PrivateResponseArchive
from context_fields.local_pipeline import LocalPipeline
from context_fields.session import Session
from scripts.score_contract_offline import independent_score, merge_intervals, uncovered
from scripts.score_saved_fixtures import profile_frames, scaffold, saved_mixed_response
from test_contracts import populated, tiny_request


Q={'type':'score','criteria':['a','b','c','d','e']}


def answer(score=0):
    return {'type':'score','score':score,'confidence':.5,
            'legend':dict(zip(map(str,range(5)),Q['criteria'])),
            'probabilities':{'0':1.,'1':0.,'2':0.,'3':0.,'4':0.}}


@pytest.mark.parametrize('delta,accepted',[(math.nextafter(1e-6,0),True),(1e-6,True),(math.nextafter(1e-6,math.inf),False)])
def test_SC05_exact_zero_expectation_boundary(delta,accepted):
    a=answer(delta);ind=independent_score(a,Q)
    assert ind['raw_consistent']==accepted
    if accepted:assert validate_answer(a,Q)['score']==delta
    else:
        with pytest.raises(ValueError,match='score_expectation'):validate_answer(a,Q)


@pytest.mark.parametrize('edit',[
    lambda a:a['probabilities'].pop('4'),
    lambda a:a['probabilities'].update({'5':0}),
    lambda a:a['probabilities'].update({'0':float('nan')}),
    lambda a:a['probabilities'].update({'0':float('inf')}),
    lambda a:a['probabilities'].update({'0':-0.01,'1':1.01}),
    lambda a:a['probabilities'].update({'0':.99}),
    lambda a:a['probabilities'].update({'0':True}),
    lambda a:a['probabilities'].update({'0':'1'}),
    lambda a:a.update(score=4.1),lambda a:a.update(score=None),
    lambda a:a.update(confidence=-1),lambda a:a.update(confidence='0.5'),
    lambda a:a['legend'].update({'0':'wrong level'}),
    lambda a:a.update(type='choice'),
])
def test_SC05_invalid_synthetic_values_remain_rejected(edit):
    a=answer();edit(a)
    assert independent_score(a,Q)['errors']
    with pytest.raises((ValueError,KeyError,TypeError)):validate_answer(a,Q)


def test_SC04_independent_decimal_not_float_or_renormalization():
    a=answer(1.95);a['probabilities']=dict(zip(map(str,range(5)),map(Decimal,['.13','.20','.32','.28','.07'])))
    d=independent_score(a,Q)
    assert d['probability_sum']==1 and d['expected_raw']==Decimal('1.96')
    assert d['delta_raw']==Decimal('-.01') and d['delta_if_normalized']==Decimal('-.01')
    a=answer(0);a['probabilities']['0']=Decimal('0.9999995')
    d=independent_score(a,Q)
    assert d['probability_sum']!=1 and d['normalization_allowed'] and d['expected_raw']==0
    a['probabilities']['0']=Decimal('.99')
    d=independent_score(a,Q)
    assert not d['normalization_allowed'] and 'expected_if_normalized' not in d


def test_SC06_mixed_unit_error_preserves_place_and_stops_new_work():
    s=populated();snap=s.fields.snapshot(6);req=s.dominance.build(snap)
    raw=mock_response(req);raw['answers']['conversation.casual'].update(score=1.95,
        probabilities=dict(zip(map(str,range(5)),[.13,.2,.32,.28,.07])))
    before=copy.deepcopy(raw);out=[]
    transport=MockTransport(lambda _:raw);transport.external=True # FAKE; no socket.
    dispatcher=Dispatcher(transport)
    def done(response,a,b):
        out.extend(s.dominance.accept(req,response,snap,s.clock,1,a,b,mode='MOCK'))
        if any(r['status']=='error' for r in out):raise ValueError('live_readout_schema')
    dispatcher.submit(req,0,'mixed',done);dispatcher.run_one(0)
    assert raw==before and dispatcher.stopped and transport.calls==1
    assert next(r for r in out if r['space']=='place')['status']=='valid'
    bad=next(r for r in out if r['space']=='conversation')
    assert bad['reason']=='score_expectation' and all(v is None for v in bad['items'].values())
    assert dispatcher.log[-1]['reason']=='ValueError:live_readout_schema'
    with pytest.raises(ValueError,match='dispatcher_stopped'):dispatcher.submit(req,1,'later',done)


def test_SC06_original_saved_mixed_response_and_request_unchanged():
    from context_fields.jev import DominanceRequest,DominanceReader
    from context_fields.fields import FieldSnapshot
    from context_fields.ledger import Ledger
    from context_fields.clock import MediaClock
    case=saved_mixed_response();ev=case['evaluation'];body=json.loads(case['request'])
    ledger=Ledger('OFFLINE-SAVED-RESPONSE-CHECK',case['media_id'],ev['epoch'])
    ledger.records=copy.deepcopy(case['records']);ledger.active=set(ledger.records)
    reader=DominanceReader(ledger);reader.registry.profiles=copy.deepcopy(case['profiles'])
    reader.counter=100000 # New diagnostic IDs cannot collide with historical records.
    reader.request_dependencies={ev['input_hash']:ev['dependencies']}
    # Saved envelope scaffold, not a claim that a full historical snapshot was stored.
    snap=FieldSnapshot(json.dumps({'epoch':ev['epoch'],'scope_revision':1,
        'as_of_media_s':ev['as_of_media_s'],'snapshot_id':ev['input_snapshot_id'],
        'input_cursor':ev['input_cursor'],'dependency_versions':ev['dependencies']}))
    clock=MediaClock(epoch=ev['epoch'],media_s=ev['as_of_media_s'])
    request=DominanceRequest(case['request'],tuple(body['state']['units']))
    original=copy.deepcopy(ev['raw']);outputs=[]
    transport=MockTransport(lambda _:original);transport.external=True # No real connection/campaign.
    dispatcher=Dispatcher(transport)
    def done(raw,a,b):
        outputs.extend(reader.accept(request,raw,snap,clock,1,a,b,mode='MOCK'))
        if any(r['status']=='error' for r in outputs):raise ValueError('live_readout_schema')
    dispatcher.submit(request,0,'saved-response',done);dispatcher.run_one(0)
    assert dispatcher.stopped and dispatcher.log[-1]['reason']=='ValueError:live_readout_schema'
    assert next(r for r in outputs if r['space']=='place')['status']=='valid'
    bad=next(r for r in outputs if r['status']=='error')
    assert bad['reason']=='score_expectation' and all(v is None for v in bad['items'].values())
    assert original==ev['raw'] and hashlib.sha256(request.payload_json.encode()).hexdigest()==ev['input_hash']
    assert all(not p['runtime_verified'] for p in reader.registry.profiles.values())


@pytest.mark.parametrize('body,status,reason',[
    (b'{"answers":{"q":{"score":1.9500,"score":1.96}}}',200,None),
    (b'{invalid JSON',200,'JSON'),(b'{"error":"dummy"}',429,'http_429'),
    (b'x'*(1024*1024+1),200,'response_size')],
    ids=['duplicate-json-keys','invalid-json','http-error','oversize'])
def test_SC03_capture_preparse_bytes_fake_connection_only(tmp_path,body,status,reason):
    class Connection:
        closed=False
        def request(self,*a,**kw):pass
        def getresponse(self):return SimpleNamespace(status=status,read=lambda size:body[:size])
        def close(self):self.closed=True
    c=Connection();archive=PrivateResponseArchive(tmp_path/'private')
    t=HttpTransport('dummy-credential-not-real',authorized=True,connection_factory=lambda *a,**kw:c,response_archive=archive)
    if reason:
        with pytest.raises((ValueError,json.JSONDecodeError)):t.send(tiny_request(),5)
    else:
        assert t.send(tiny_request(),5)['answers']['q']['score']==1.96
    assert c.closed
    meta=json.loads(next((tmp_path/'private').glob('*.json')).read_text())
    assert (tmp_path/'private'/meta['body_ref']).read_bytes()==body
    assert meta['body_sha256']==hashlib.sha256(body).hexdigest() and meta['http_status']==status
    assert meta['stage']=='http_body_before_json_decode'
    assert 'dummy-credential' not in json.dumps(meta) and 'Authorization' not in json.dumps(meta)
    assert meta['truncated']==(len(body)>1024*1024)


def test_SC09_asr_slot_logs_actual_samples_and_coalescing_without_pcm():
    p=LocalPipeline.__new__(LocalPipeline);p.s=Session();p.pending={};p.waiting={}
    p.s.admission=SimpleNamespace(admit=lambda wall:None)
    p.models=SimpleNamespace(submit=lambda *a,**kw:Future())
    def audio(a,b,n):return {'start_s':a,'end_s':b,'samples':[0]*n,
        'chunks':[{'start_s':a,'samples':[0]*n,'sample_rate':16000,'pts':0,'time_base':[1,16000],'sample_offset':17}]}
    old=audio(7.3,7.5,3200);latest=audio(9.1,9.5,6400)
    p._submit('SpeechASR',old,{'audio':old},10)
    p._submit('SpeechASR',latest,{'audio':latest},12)
    p._dispatch_next(20)
    timing=p.pending['SpeechASR'][1]['timing']
    assert timing['input_audio_interval']==[9.1,9.5] and timing['input_sample_count']==6400
    assert timing['slot_wait_s']==10 and timing['latest_input_wait_s']==8
    assert p.pending['SpeechASR'][1]['audio'] is latest
    record=next(e['payload'] for e in p.s.ledger.events if e['kind']=='local_slot_coalesced')
    assert record['old_input']['input_audio_interval']==[7.3,7.5]
    assert record['replacement']['input_id']!=record['old_input']['input_id']
    assert '"samples"' not in json.dumps(p.s.ledger.events)


def test_SC09_interval_union_respects_gaps_and_overlaps():
    assert merge_intervals([[4,6],[0,2],[1,3]])==[[0,3],[4,6]]
    assert uncovered([[0,6],[10,12]],[[0,1],[.5,2],[3,6],[10,11]])==[[2,3],[11,12]]


@pytest.fixture(scope='module')
def saved_profiles():
    return profile_frames()


@pytest.mark.parametrize('pid',['topic-850e7202a05114d2','topic-b4e298c73f4f31ff'])
def test_SC08_exact_saved_profile_offline_connection(saved_profiles,pid):
    fixture=next(f for (run,key),f in saved_profiles.items() if key==pid)
    ledger,reader,clock,snap=scaffold(fixture);before=snap.data()
    profile=reader.registry.profiles[pid]
    assert profile['revision']==1 and not profile['runtime_verified']
    request=None
    for _ in range(6):
        r=reader.build(snap)
        if r and pid in r.unit_ids:request=r;break
    assert request is not None
    body=json.loads(request.payload_json);question=body['questions'][pid+'.topic']
    assert len(question['criteria'])==5 and profile['definition'] in question['instructions']
    assert body['state']['units'][pid]['profile']['revision']==1
    assert len(request.unit_ids)<=3 and len(body['questions'])<=12 and len(request.payload_json)<=12000
    scope=before['scope_revision'];raw=mock_response(request)
    out=reader.accept(request,raw,snap,clock,scope,0,1,mode='MOCK')
    target=next(r for r in out if r['profile_id']==pid)
    assert target['items']=={'topic':50.0} and target['mode']=='MOCK' and not target['exclusive']
    assert target['rubric_version']==profile['rubric_version'] and snap.data()==before
    assert not reader.registry.profiles[pid]['runtime_verified']
    raw['answers'][pid+'.assessability'].update(choice='insufficient',
        probabilities={'assessable':.1,'insufficient':.8,'conflicting':.1})
    target=next(r for r in reader.accept(request,raw,snap,clock,scope,0,1) if r['profile_id']==pid)
    assert target['status']=='insufficient' and target['items']=={'topic':None}
    assert reader.display(clock,scope)[pid]['items']=={'topic':None}
    reader.registry.profiles[pid]['revision']=2
    out=reader.accept(request,raw,snap,clock,scope,0,1)
    assert not any(r['profile_id']==pid for r in out)
    reader.registry.profiles[pid]['revision']=1
    dep=next(iter(reader.request_dependencies[hashlib.sha256(request.payload_json.encode()).hexdigest()]))
    ledger.active.remove(dep)
    with pytest.raises(ValueError,match='superseded_dependency'):reader.accept(request,raw,snap,clock,scope,0,1)
