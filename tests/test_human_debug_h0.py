"""H0 tests only create human-shaped annotations in pytest fixture directories."""
import copy
import io
import json
import time
import urllib.request
import urllib.error
import threading
import uuid
import socket
from pathlib import Path
from fractions import Fraction
import pytest
from context_fields.debug_review import DebugReview, frame_at, feature_builds, DebugFeatureAdapter
from context_fields.debug_server import make_debug_server
from context_fields.human_review import HumanReviewStore, safe_export
from context_fields.media import Asset, probe
from context_fields.review_index import digest
from context_fields.session import Session

_create_connection = socket.create_connection
_socket_connect = socket.socket.connect


def ready(index, run):
    index.start(run)
    end = time.monotonic() + 20
    while index.status(run)['status'] == 'indexing' and time.monotonic() < end:
        time.sleep(.01)
    assert index.status(run)['status'] == 'ready', index.status(run)


@pytest.fixture
def service(tmp_path):
    r = DebugReview(tmp_path, fixture=True)
    folder = tmp_path / 'artifacts/sessions'
    folder.mkdir(parents=True)
    sample = Session('fixture-h0', 'MOCK', tmp_path / 'fixture.jsonl')
    frame = sample.view()
    sample.stop()
    transcript = {'segment_id': 'transcript:1:1', 'revision': 1, 'epoch': 1, 'text': '<script>fixture</script>',
                  'media_start_s': 0.1, 'media_end_s': .3, 'status': 'provisional', 'audio_sample_refs': []}
    frame['transcripts'] = {'state': 'ready', 'current': [transcript], 'history': []}
    rec = {'id': 'obs-1', 'revision': 1, 'kind': 'observation', 'agent': 'FrameInterpreter',
           'text': 'fixture', 'dependencies': {}, 'source_refs': [{'modality': 'video', 'root': 'root-1', 'pts': 0,
            'time_base': [1, 1000], 'interval': [0.0, 0.0]}]}
    events = [
        ('local_asset', {'id': 'asset-' + 'a' * 32, 'sha256': 'b' * 64, 'bytes': 100}),
        ('transcript_version', transcript),
        ('record', rec),
        ('display', frame),
        ('transcript_version', {**transcript, 'revision': 2, 'text': 'new revision'}),
        ('local_slot_requested', {'agent': 'SpeechASR', 'slot_first_requested_wall_s': 4}),
        ('decode_completed', {'audio_interval': [0, .3], 'completed_wall_s': 5}),
        ('local_model_admitted', {'agent': 'SpeechASR', 'source_available_wall_s': 5, 'slot_wait_s': 2, 'accepted_wall_s': 6}),
        ('local_model_completed', {'agent': 'SpeechASR', 'accepted_wall_s': 6, 'completed_wall_s': 7}),
        ('record', {'id': 'eval-1', 'kind': 'evidence_evaluation', 'revision': 1, 'dependencies': {'obs-1': 1}}),
        ('invalidation', {'ids': ['obs-1'], 'reason': 'cut_scope_reanalysis'}),
        ('epoch_reset', {'epoch': 2}),
        ('display', {**frame, 'clock': {**frame['clock'], 'epoch': 2}, 'transcripts': {'current': [], 'history': [], 'state': 'empty'}}),
        ('stop', {}),
    ]
    path = folder / 'run-1.jsonl'
    with path.open('w', encoding='utf8') as f:
        for i, (kind, payload) in enumerate(events, 1):
            f.write(json.dumps(dict(schema_version='3', event_seq=i, kind=kind, epoch=1 if i<12 else 2,
                                    media_s=.3, payload=payload)) + '\n')
    ready(r.index, 'run-1')
    return r


def media_asset(service, sha='b' * 64, size=100):
    asset = Asset('asset-' + 'a' * 32, service.assets.root / 'fixture.mp4', 'fixture.mp4', size, sha,
                  {'duration_s': 10, 'origin_s': 0, 'has_audio': True})
    service.assets.assets[asset.id] = asset
    return asset


def pinned(service, key='r:2:0', cursor=4):
    a = media_asset(service)
    return service.pin(dict(run='run-1', cursor=cursor, key=key, asset_id=a.id, playhead=.2))


def request(pin):
    return {'review_id': pin['review_id'], 'expected_revision': 0, 'idempotency_key': uuid.uuid4().hex, 'reviewer': 'TEST FIXTURE',
            'judgment': {'review_status': 'submitted', 'semantic_verdict': 'consistent_with_evidence',
                         'numeric_check': {'status': 'unknown', 'evidence_ids': [], 'check_version': None},
                         'pipeline_check': {'status': 'unknown', 'evidence_ids': [], 'check_version': None},
                         'issue_tags': ['ui_problem'], 'severity': 'minor', 'expected_text': 'fixture reference', 'actual_text': None,
                         'note': 'fixture only', 'requested_action': 'investigate'},
            'exposure': {'human_evidence_confirmation': True, 'reviewed_evidence': ['transcript_read'],
                         'review_basis': 'hindsight', 'future_content_exposure': 'seen'}}


def test_cursor_epoch_and_revision_no_future(service):
    old = service.index.display('run-1', 4)
    assert old['frame']['transcripts']['current'][0]['revision'] == 1
    assert service.index.target('run-1', 4, 'r:2:0')['revision'] == '1'
    with pytest.raises(ValueError, match='missing'):
        service.index.target('run-1', 4, 'r:5:0')
    assert service.index.display('run-1', 13)['frame']['clock']['epoch'] == 2
    assert service.index.display('run-1', 13)['frame']['transcripts']['current'] == []


@pytest.mark.parametrize('sha,size,expected', [('b'*64,100,'hash_verified'),('b'*64,101,'mismatch'),('c'*64,100,'mismatch')])
def test_media_match(service,sha,size,expected):
    asset=media_asset(service,sha,size)
    assert service.match('run-1',asset.id)['media_match']==expected


def test_legacy_hash_missing(service):
    asset=media_asset(service)
    del service.index.jobs['run-1']['asset']['sha256']
    assert service.match('run-1',asset.id)['media_match']=='unverified'
    with pytest.raises(ValueError,match='LEGACY_MISSING'):
        service.register_managed('run-1')


def test_pin_immutable_old_revision_restore_idempotence(service):
    pin=pinned(service);req=request(pin)
    service.index.display('run-1',13)
    ack=service.reviews.save(req)
    assert ack['record']['target']['event_cursor']==4
    assert ack['record']['target']['record_version']=='1'
    assert ack['record']['provenance']['is_test_fixture'] is True
    assert service.reviews.save(req)['duplicate'] is True
    loaded=HumanReviewStore(service.reviews.root,fixture=True).list()
    assert loaded[0]['annotation']['record_id']==ack['record']['record_id']
    edited=copy.deepcopy(req);edited['judgment']['note']='edit'
    with pytest.raises(ValueError,match='idempotency_conflict'):service.reviews.save(edited)
    edited['idempotency_key']=uuid.uuid4().hex
    with pytest.raises(ValueError,match='revision_conflict'):service.reviews.save(edited)
    edited['expected_revision']=1
    second=service.reviews.save(edited)['record']
    assert second['supersedes_record_id']==ack['record']['record_id']
    assert len((service.reviews.root/pin['review_id']/'annotations.jsonl').read_text().splitlines())==2


def test_required_evidence_and_exposure(service):
    req=request(pinned(service));req['exposure']['human_evidence_confirmation']=False
    with pytest.raises(ValueError):service.reviews.save(req)
    req['exposure']['human_evidence_confirmation']=True;req['exposure']['model_output_exposure']='not_shown_before_annotation'
    with pytest.raises(ValueError,match='cannot_be_undone'):service.reviews.save(req)


def test_mismatched_media_cannot_claim_semantic_success(service):
    pin=pinned(service);pin['target']['media_match']='mismatch'
    new=service.reviews.pin(pin['target'],pin['context'])
    with pytest.raises(ValueError,match='verify_media'):service.reviews.save(request(new))


def test_original_unchanged_by_views_annotation_export(service):
    path=service.index.path('run-1');before=digest(path)
    service.index.display('run-1',14);service.index.targets('run-1',14);service.index.timeline('run-1',14)
    service.reviews.save(request(pinned(service)))
    service.reviews.export()
    assert digest(path)==before


def test_actual_reference_edges(service):
    target=service.target('run-1',10,'r:3:0')
    assert any(x['key']=='r:10:0' for x in target['links']['items'])
    assert not service.target('run-1',4,'r:3:0')['links']['items']
    invalid=service.target('run-1',11,'r:3:0')
    assert any(x['key']=='e:11' for x in invalid['links']['items'])


def test_slot_coverage_distinct(service):
    result=service.index.timeline('run-1',14)
    assert result['asr_inputs'][0]['interval']==[0,.3]
    assert result['asr_inputs'][0]['basis'].startswith('derived:')
    assert next(x for x in result['states'] if x['seq']==8)['slot_wait_s']==2
    assert next(x for x in result['states'] if x['seq']==9)['inference_s']==1


def test_export_sanitizes_paths_secrets_no_execution(service):
    req=request(pinned(service));req['judgment']['note']='C:\\Users\\Private\\movie.mp4 token=abc <script>bad</script> ``` cmd /c'
    service.reviews.save(req);out=service.reviews.export()
    assert 'C:\\Users' not in out['markdown']
    assert 'token=abc' not in out['jsonl']
    assert '[PRIVATE_PATH]' in out['markdown']
    assert out['sent'] is False
    assert '``` cmd' not in out['markdown']


def test_issue_requires_new_human_record_not_auto_pass(service):
    p=pinned(service);rid=p['review_id'];service.reviews.save(request(p))
    service.reviews.issue(dict(review_id=rid,expected_revision=0,status='open'))
    service.reviews.issue(dict(review_id=rid,expected_revision=1,status='triaged'))
    service.reviews.issue(dict(review_id=rid,expected_revision=2,status='fixed_pending_human',reason='fixture fix',fix_build=service.build))
    with pytest.raises(ValueError,match='new_human_review'):
        service.reviews.issue(dict(review_id=rid,expected_revision=3,status='verified_by_human'))
    p2=pinned(service);ack=service.reviews.save(request(p2))
    final=service.reviews.issue(dict(review_id=rid,expected_revision=3,status='verified_by_human',verification_record_id=ack['record']['record_id']))
    assert final['revision']==4


def test_planned_and_checks_remain_pending(service):
    adapter=DebugFeatureAdapter(service,'future_asr_correction')
    assert adapter.capability()['status']=='planned'
    assert adapter.list_targets('run-1',14)['items']==[]
    assert service.checks()['human_acceptance']=='pending'
    assert all(c['human_operation']=='pending' for c in service.checks()['checks'])


def test_diagnostic_case_never_acquires_video_clock(service):
    path=service.root/'artifacts/score-contract-offline/decision-prep-20260920'
    path.mkdir(parents=True)
    (path/'comparison.jsonl').write_text(json.dumps({'sample_id':'raw-case-01','run':None,'numeric':{'provider_score':'2.69','mean_after':'2.7'},'evidence_stage':'RAW_HTTP_BODY'})+'\n')
    pin=service.pin({'diagnostic_id':'raw-case-01','playhead':59,'run':'run-1','cursor':13})
    assert pin['target']['target_kind']=='diagnostic_record'
    assert pin['target']['run_id'] is None
    assert pin['context']['review_playhead_media_s'] is None
    assert pin['context']['recorded_display_media_s'] is None


def test_nonexistent_cursor_rejected(service):
    with pytest.raises(ValueError,match='event_missing'):pinned(service,cursor=999)


def test_duplicate_evidence_rejected(service):
    req=request(pinned(service));req['exposure']['reviewed_evidence']=['transcript_read','transcript_read']
    with pytest.raises(ValueError,match='unique'):service.reviews.save(req)


def test_adjacent_display_uses_events_not_media_time(service):
    assert service.index.adjacent_display('run-1',4,'next')['cursor']==13
    assert service.index.adjacent_display('run-1',13,'prev')['cursor']==4


def test_concurrent_revisions_do_not_overwrite(service):
    from concurrent.futures import ThreadPoolExecutor
    pin=pinned(service)
    def save(_):
        try:return service.reviews.save(request(pin))['record']['record_revision']
        except ValueError as error:return str(error)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(save,range(2)))
    assert sorted(map(str,results))==['1','revision_conflict_reload_required']


def test_selective_feature_builds(tmp_path):
    root=tmp_path/'src';(root/'context_fields').mkdir(parents=True)
    (root/'context_fields/transcript.py').write_text('a')
    before=feature_builds(root)
    (root/'context_fields/transcript.py').write_text('b')
    after=feature_builds(root)
    assert before['asr_raw']!=after['asr_raw']
    assert before['review_export']==after['review_export']


@pytest.mark.parametrize('name',['../run-1','run-1/../../.env','run-1.jsonl','C:\\run-1','run-x'])
def test_run_allowlist(service,name):
    with pytest.raises(ValueError):service.index.path(name)


def test_source_change_fail_closed(service):
    with service.index.path('run-1').open('a') as f:f.write('\n')
    with pytest.raises(ValueError,match='source_changed'):service.index.display('run-1',14)


def test_explicit_reindex_after_new_local_run_append(service):
    with service.index.path('run-1').open('a',encoding='utf8') as stream:
        stream.write(json.dumps({'schema_version':'3','event_seq':15,'kind':'stop','epoch':2,'media_s':.3,'payload':{}})+'\n')
    with pytest.raises(ValueError,match='source_changed'):service.index.display('run-1',14)
    ready(service.index,'run-1')
    assert service.index.status('run-1')['events']==15
    assert service.index.display('run-1',15)['cursor']==13


def test_cancel_and_restart_preserves_source(service,monkeypatch):
    source=service.index.path('run-1');before=digest(source)
    service.index.jobs['run-1']['status']='cancelled'
    original=service.index._build
    gate=threading.Event()
    def delayed(run):
        gate.wait(2);original(run)
    monkeypatch.setattr(service.index,'_build',delayed)
    service.index.start('run-1');service.index.cancel('run-1');gate.set()
    end=time.monotonic()+3
    while service.index.status('run-1')['status']=='indexing' and time.monotonic()<end:time.sleep(.01)
    assert service.index.status('run-1')['status']=='cancelled'
    assert digest(source)==before
    monkeypatch.setattr(service.index,'_build',original)
    ready(service.index,'run-1')


def test_paging_is_bounded(service):
    assert len(service.index.targets('run-1',14,limit=1)['items'])==1
    assert service.index.targets('run-1',14,offset=100)['items']==[]
    assert service.index.events('run-1',14,limit=1)[0]['seq']==14


def make_vfr(tmp_path):
    import av
    import numpy as np
    path=tmp_path/'vfr.mkv'
    with av.open(str(path),'w') as out:
        stream=out.add_stream('ffv1',rate=25);stream.width=32;stream.height=24;stream.pix_fmt='yuv420p';stream.time_base=Fraction(1,1000)
        for pts,color in [(2500,20),(2540,70),(2630,170),(3000,220)]:
            frame=av.VideoFrame.from_ndarray(np.full((24,32,3),color,dtype=np.uint8),format='rgb24')
            frame.pts=pts;frame.time_base=Fraction(1,1000)
            for packet in stream.encode(frame):out.mux(packet)
        for packet in stream.encode():out.mux(packet)
    return Asset('fixture-vfr',path,'vfr.mkv',path.stat().st_size,digest(path),probe(path))


def test_decoded_adjacent_vfr_nonzero_pts(tmp_path):
    asset=make_vfr(tmp_path)
    assert asset.metadata['origin_s']>0
    first=frame_at(asset,0)
    following=frame_at(asset,first['media_s'],'next')
    second=frame_at(asset,following['media_s'],'next')
    assert first['pts']<following['pts']<second['pts']
    assert following['pts']-first['pts'] != second['pts']-following['pts']
    assert frame_at(asset,second['media_s'],'prev')['pts']==following['pts']
    assert 'reconstructed' in first['pixel_provenance']


def test_http_non_live_boundaries_and_budget_zero(tmp_path, monkeypatch):
    (tmp_path/'artifacts/sessions').mkdir(parents=True)
    server=make_debug_server(0,tmp_path,fixture=True)
    from scripts.offline_guard import ALLOWED_PORTS
    ALLOWED_PORTS.add(server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def loopback_create(address, *args, **kwargs):
        assert address == ('127.0.0.1', server.server_port)
        return _create_connection(address, *args, **kwargs)
    def loopback_connect(sock, address):
        assert address == ('127.0.0.1', server.server_port)
        return _socket_connect(sock, address)
    monkeypatch.setattr(socket, 'create_connection', loopback_create)
    monkeypatch.setattr(socket.socket, 'connect', loopback_connect)
    def call(path,body=None,token='',origin=None):
        headers={'X-Session-Token':token,'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        return urllib.request.urlopen(req,timeout=10)
    try:
        boot=json.load(call('/api/bootstrap'));key=boot['token'];assert boot['debug'] and not boot['live_enabled']
        for path in ['/api/snapshot','/api/clock','/api/live/start','/api/session','/api/control']:
            with pytest.raises(urllib.error.HTTPError) as e:call(path,{} if path!='/api/snapshot' else None,key)
            assert e.value.code==404
        with pytest.raises(urllib.error.HTTPError) as e:call('/api/debug/runs',token=key,origin='https://evil.invalid')
        assert e.value.code==403
        assert json.load(call('/api/debug/checks',token=key))['human_acceptance']=='pending'
        assert not hasattr(server.app,'session') and not hasattr(server.app,'live_capability')
        assert not (tmp_path/'artifacts/local-private').exists()
    finally:
        server.shutdown();server.server_close();thread.join(5)
        ALLOWED_PORTS.discard(server.server_port)


def test_local_start_is_explicit_selected_media_child_only(service,monkeypatch):
    import context_fields.debug_server as module
    app=module.DebugApp(service.root,fixture=True)
    asset=media_asset(app.review)
    with pytest.raises(ValueError,match='explicit'):app.start_local({'asset_id':asset.id})
    created=[]
    class Child:
        pid=123
        def poll(self):return None
    class Probe:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,address):assert address==('127.0.0.1',8877)
    monkeypatch.setattr(module,'ROOT',service.root)
    monkeypatch.setattr(module.socket,'socket',Probe)
    monkeypatch.setattr(module.subprocess,'Popen',lambda args,**kwargs:(created.append((args,kwargs)) or Child()))
    state=app.start_local({'asset_id':asset.id,'start_s':1,'mode_confirmation':'LOCAL_REAL_EVAL_MOCK'})
    assert state['new_jev_allowed'] is False and state['analysis_interval']==[1,10]
    assert 'context_fields.debug_local_server' in created[0][0]
    assert '--approved-live-v02' not in created[0][0]
    assert asset.sha256 in created[0][0]
    with pytest.raises(ValueError,match='already_running'):app.start_local({'asset_id':asset.id,'mode_confirmation':'LOCAL_REAL_EVAL_MOCK'})


def test_selected_local_app_has_no_synthetic_initial_session(tmp_path,monkeypatch):
    from context_fields import server
    from context_fields.debug_local_server import SelectedLocalApp
    from context_fields.media import AssetStore
    monkeypatch.setattr(server,'AssetStore',lambda unused:AssetStore(tmp_path/'assets'))
    app=SelectedLocalApp(log_dir=tmp_path/'logs',load_models=False,live_capability=None)
    try:
        assert app.session is None
        assert not (tmp_path/'logs').exists()
        assert app.live_capability is None
        class Models:
            status='ready'
            def public(self):return {'status':'ready','models':{k:'TEST-FIXTURE' for k in ('SpeechASR','FrameInterpreter','TextContext')}}
            def submit(self,*args,**kwargs):raise AssertionError('no inference before explicit playback')
        app.models=Models()
        asset=make_vfr(tmp_path);app.assets.assets[asset.id]=asset
        frame=app.new_session('LOCAL',asset.id,0)
        assert frame['media']['id']==asset.id
        assert frame['media']['evaluation_mode']=='MOCK'
        assert frame['clock']['state']=='paused'
        assert not frame['clock']['released']['video']
        assert len(list((tmp_path/'logs').glob('run-*.jsonl')))==1
    finally:
        if app.session:
            app.session.stop()
            if app.session.local_pipeline:app.session.local_pipeline.close()
        app.pool.close()
