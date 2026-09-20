"""LV contracts with synthetic media and fake models. No download/real inference."""
from concurrent.futures import Future
from pathlib import Path
import copy
import io
import json
import time
import pytest
from context_fields.media import AssetStore,Asset,PTSDecoder,probe,MAX_UPLOAD
from context_fields.local_pipeline import LocalPipeline,audio_sources,local_mock_response
from context_fields.session import Session
from context_fields.clock import MediaClock
from context_fields.jev import DominanceRequest

ROOT=Path(__file__).parents[1]

class NoModels:
    status='ready'
    def public(self):return {'models':{'SpeechASR':'TEST','FrameInterpreter':'TEST','TextContext':'TEST'},'status':'ready'}
    def submit(self,*args,**kwargs):return None

@pytest.fixture
def asset():
    path=ROOT/'fixtures/local-diagnostic.mp4'
    return Asset('diagnostic',path,path.name,path.stat().st_size,'test',probe(path))

def test_LV01_streamed_import_two_assets_same_bytes_and_delete(tmp_path):
    store=AssetStore(tmp_path)
    with (ROOT/'fixtures/synthetic.webm').open('rb') as f:
        a=store.import_stream(f,(ROOT/'fixtures/synthetic.webm').stat().st_size,'clip.webm')
    with (ROOT/'fixtures/local-diagnostic.mp4').open('rb') as f:
        b=store.import_stream(f,(ROOT/'fixtures/local-diagnostic.mp4').stat().st_size,'movie.mp4')
    assert a.id!=b.id and a.sha256!=b.sha256
    assert a.path.read_bytes()==(ROOT/'fixtures/synthetic.webm').read_bytes()
    store.delete(a.id);assert not a.path.exists() and b.path.exists()
    with pytest.raises(ValueError):store.get('../.env')

def test_LV01_import_size_failure_and_incomplete_copy_removed(tmp_path):
    store=AssetStore(tmp_path)
    with pytest.raises(ValueError):store.import_stream(io.BytesIO(),MAX_UPLOAD+1,'test.mp4')
    with pytest.raises(ValueError,match='incomplete'):store.import_stream(io.BytesIO(b'abc'),10,'test.mp4')
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError):store.import_stream(io.BytesIO(b'abc'),3,'secret.env')

def test_LV02_pts_audio_sample_mapping_and_cutoff(asset):
    d=PTSDecoder(asset);frame=d.frame(3.2,3.57)
    assert 3.2<=frame['media_s']<=3.57
    assert frame['media_s']==pytest.approx(frame['pts']*frame['time_base'][0]/frame['time_base'][1]-frame['media_origin_s'])
    a=d.audio(3.21,4.1234)
    assert a['start_s']>=3.21-1e-9 and a['end_s']<4.1234 and len(a['samples'])<=16000
    for chunk in a['chunks']:
        assert type(chunk['container_pts']) is int
        assert chunk['container_time_base'][1]>0

def test_LV03_asr_roots_text_dependencies_and_revision(asset):
    s=Session(mode='LOCAL');p=LocalPipeline(s,asset,NoModels())
    try:
        s.clock.strict_continuity=False  # Unit tests publish explicit slices; continuity tested separately.
        s.clock.notify(s.clock.epoch,1,5,5,'playing',0,audio_presented_s=5)
        audio=p.decoder.audio(0,4)
        out={'text':'quoted business meeting','segments':[{'start_s':.2,'end_s':3.2,'text':'quoted business meeting','words':[]}], 'model':'test'}
        p._asr(out,{'audio':audio},0)
        old=p.latest_asr
        text={'topic':'meeting','register':'business','mentioned_place':'unknown','quote':'business meeting','raw':'test'}
        p._text(text,{'asr_id':old['id']},0)
        derived=[r for r in s.ledger.records.values() if r.get('agent')=='TextContext']
        assert derived and all(old['id'] in r['dependencies'] for r in derived)
        assert all(r['origin']=='LOCAL' and r['basis']!='synthetic_fixture' for r in derived)
        p._asr(out,{'audio':audio},1)
        assert old['id'] not in s.ledger.active and all(r['id'] not in s.ledger.active for r in derived)
    finally:p.close()

def test_LV04_LV05_real_frame_provenance_mock_evaluation_to_field(asset):
    s=Session(mode='LOCAL');p=LocalPipeline(s,asset,NoModels())
    try:
        s.clock.strict_continuity=False  # Unit tests publish explicit slices; continuity tested separately.
        s.clock.notify(s.clock.epoch,1,3,3,'playing',0,audio_presented_s=3)
        frame=p.decoder.frame(0,2)
        p._visual({'description':'test observed color','scene':'test scene','time_of_day':'unknown','raw':'test'},
                  {'frame':frame,'scope':s.fields.shot},0)
        s.dispatcher.run_one(0)
        snap=s.fields.snapshot(3).data()
        assert snap['spaces']['place']['UNKNOWN']<1
        obs=next(r for r in s.ledger.records.values() if r['kind']=='observation')
        assert obs['frame_id']==frame['frame_id'] and obs['source_refs'][0]['pts']==frame['pts']
        ev=next(r for r in s.ledger.records.values() if r['kind']=='evidence_evaluation')
        assert ev['mode']=='MOCK' and ev['returned_model']=='MOCK-local-wiring-v1'
    finally:p.close()

def test_LV06_synthetic_observations_forbidden_local_and_dominance_not_20_90():
    s=Session(mode='LOCAL')
    with pytest.raises(ValueError,match='synthetic_observation'):s.observation(1,'place','day','test')
    req=DominanceRequest(json.dumps({'questions':{'test':{'type':'choice','criteria':{'assessable':'','insufficient':'','conflicting':''}}}}),('conversation',))
    raw=local_mock_response(req)
    assert raw['answers']['test']['probabilities']['insufficient']==1

def test_LV07_seek_gate_prevents_old_window(asset):
    s=Session(mode='LOCAL');p=LocalPipeline(s,asset,NoModels(),start=8)
    try:
        assert s.clock.window(6,'audio') is None
        s.clock.strict_continuity=False  # Unit tests publish explicit slices; continuity tested separately.
        s.clock.notify(s.clock.epoch,1,9,9,'playing',0,audio_presented_s=9)
        start,end=s.clock.window(6,'audio');audio=p.decoder.audio(start,end)
        assert start==8 and audio['start_s']>=8
        sources=audio_sources(s,audio)
        obs=p._observation('SpeechASR','test',sources,facet='conversation.transcript',subject='audio-stream01',scope='session',reference_mode='quoted_speech')
        s.ledger.add(obs,s.clock)
        assert all(src['interval'][0]>=8 for src in sources.values())
        epoch=s.clock.epoch;s.seek(10)
        assert s.clock.epoch>epoch and not s.ledger.active and s.clock.window(6,'audio') is None
    finally:p.close()

def test_LV08_long_asset_allows_late_start_but_bounds_analysis(asset):
    long=Asset(asset.id,asset.path,asset.name,asset.size,asset.sha256,{**asset.metadata,'duration_s':3600})
    s=Session(mode='LOCAL');p=LocalPipeline(s,long,NoModels(),start=300)
    try:
        assert s.clock.media_s==300 and p.end==420
        assert s.clock.window(6,'audio') is None
    finally:p.close()

def test_LV08_no_audio_decoder_and_no_face_creation():
    path=ROOT/'fixtures/synthetic.webm';asset=Asset('silent',path,'silent.webm',path.stat().st_size,'',probe(path))
    assert not asset.metadata['has_audio'] and PTSDecoder(asset).audio(0,2) is None
    s=Session(mode='LOCAL');p=LocalPipeline(s,asset,NoModels())
    try:
        s.clock.strict_continuity=False  # Unit tests publish explicit slices; continuity tested separately.
        s.clock.notify(s.clock.epoch,1,1,1,'playing',0)
        frame=p.decoder.frame(0,.5)
        p._decoded({'frame':frame,'audio':None,'motion':{'cut':False},'faces':{'tracks':{}},'scope':s.fields.shot},0)
        assert not any(h.space=='person' for h in s.fields.hypotheses.values())
    finally:p.close()

def test_LV09_expired_output_and_old_epoch_never_contribute(asset):
    s=Session(mode='LOCAL');p=LocalPipeline(s,asset,NoModels())
    try:
        f=Future();f.set_result({'kind':'FrameInterpreter','output':{'description':'old'},'accepted':0,'started':0,'completed':6,'expired':True})
        p.pending['FrameInterpreter']=(f,{},s.clock.epoch)
        p.collect(6)
        assert p.drop==1 and not s.ledger.records
    finally:p.close()

def test_unannounced_seek_rejected_before_publishing():
    clock=MediaClock(strict_continuity=True)
    clock.notify(1,0,0,0,'playing',0,audio_presented_s=0)
    clock.notify(1,1,.2,.2,'playing',.2,audio_presented_s=.2)
    with pytest.raises(ValueError,match='discontinuity'):
        clock.notify(1,2,8,8,'playing',.3,audio_presented_s=8)
    assert clock.released['audio']==[(0,.2)]

def test_heavy_slots_coalesce_and_prioritize_valid_asr_text(asset):
    class Models(NoModels):
        def __init__(self):self.calls=[]
        def submit(self,kind,data,accepted):
            self.calls.append((kind,data,accepted));return Future()
    models=Models();s=Session(mode='LOCAL');p=LocalPipeline(s,asset,models)
    try:
        p._submit('SpeechASR',{'version':1},{},0)
        p._submit('SpeechASR',{'version':2},{},1)
        assert len(p.waiting)==1 and not models.calls and not s.admission.accepted
        p._dispatch_next(2)
        assert models.calls==[('SpeechASR',{'version':2},2)] and len(s.admission.accepted)==1
        p._submit('FrameInterpreter',{'version':3},{},3);p._dispatch_next(3)
        assert len(models.calls)==1
    finally:p.close()

def test_asr_numpy_timestamps_become_json_native():
    import numpy as np
    from types import SimpleNamespace
    from context_fields.local_models import LocalModels
    class ASR:
        def transcribe(self,*a,**k):
            seg=SimpleNamespace(start=np.float64(0),end=np.float64(.2),text='hello',words=[])
            return iter([seg]),SimpleNamespace(language='en')
    runtime=LocalModels(autostart=False);runtime.asr=ASR()
    try:
        out=runtime._infer('SpeechASR',{'samples':np.ones(4000,dtype=np.float32)*.1,'start_s':0,'end_s':.25})
        assert type(out['segments'][0]['end_s']) is float
        json.dumps(out)
    finally:runtime.close()

def test_LV02_vfr_offset_pts_and_real_long_container(tmp_path):
    import av
    import numpy as np
    from fractions import Fraction
    path=tmp_path/'long-vfr.mkv'
    with av.open(str(path),'w') as out:
        stream=out.add_stream('ffv1',rate=10);stream.width=64;stream.height=64;stream.pix_fmt='bgr0';stream.time_base=Fraction(1,1000)
        for pts in (5000,5300,6100,305000,306200):
            frame=av.VideoFrame.from_ndarray(np.zeros((64,64,3),dtype=np.uint8),format='bgr24');frame.pts=pts;frame.time_base=Fraction(1,1000)
            for packet in stream.encode(frame):out.mux(packet)
        for packet in stream.encode():out.mux(packet)
    meta=probe(path);assert meta['duration_s']>120
    asset=Asset('vfr',path,path.name,path.stat().st_size,'',meta)
    decoded=PTSDecoder(asset).frame(.2,.9)
    assert decoded is not None and decoded['media_s']==pytest.approx(.3)
    assert not decoded['image'].flags.writeable

def test_reader_latest_published_target_is_real_new_root_and_completes(asset):
    s=Session(mode='LOCAL');p=LocalPipeline(s,asset,NoModels())
    try:
        s.clock.strict_continuity=False
        s.clock.notify(s.clock.epoch,1,5,5,'playing',5,audio_presented_s=5)
        old=p.decoder.frame(0,.5)
        out={'description':'test daylight','scene':'unknown','time_of_day':'day','raw':'test'}
        p._visual(out,{'frame':old,'scope':s.fields.shot},0);s.dispatcher.run_one(0)
        for t in (1,2,3,4):s.admission.admit(t)
        fresh=p.decoder.frame(0,4.9)
        job=s.reader.propose(s.fields.snapshot(5),s.clock,5,target_frame=fresh)
        assert job and job['new_roots']==1 and job['target_media_s']==fresh['media_s']
        assert next(iter(job['target_sources'].values()))['pts']==fresh['pts']
        p._visual(out,{'frame':fresh,'scope':s.fields.shot,'job':job},5);s.dispatcher.run_one(5)
        assert any(e['kind']=='inspection_completed' and e['payload']['new_roots']==1 for e in s.ledger.events)
        assert any(r.get('frame_id')==old['frame_id'] for r in s.ledger.records.values())
        s.clock.media_s=p.end;s.clock.state='ended';p.pump(10)
        assert s.stopped and s.analysis_status=='range_complete'
    finally:p.close()
