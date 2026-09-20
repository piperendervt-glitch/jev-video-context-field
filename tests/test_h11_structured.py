"""H11 acceptance boundaries; all data is synthetic except the separate saved-run harness."""
import json
import sqlite3
from pathlib import Path
import pytest
from context_fields.structured_results import fields_for, present, source_refs
from context_fields.activity import ActivityBuilder, page, row_at
from context_fields.thumbnail_cache import ThumbnailCache
from test_h1_activity import fixture_events, make


def by_id(fields): return {f['id']: f for f in fields}


def test_h11_02_motion_uses_measurement_never_repr_and_no_invented_unit():
    p={'text': "{'flow_mean_px': 999}", 'measurement': {'flow_mean_px': 2.1311395, 'cut': False}}
    values=by_id(fields_for('MotionCut',p,'r:1:0'))
    assert values['flow_mean_px']['value']==2.1311395 and values['flow_mean_px']['unit'] is None
    assert values['cut']['value'] is False and len(values)==2
    assert '999' not in json.dumps(values)


def test_h11_03_audio_null_is_not_zero_or_sound_pressure():
    values=by_id(fields_for('AudioMeasure',{'measurement':{'dbfs':-23.3995,'pitch_hz':None,'pitch_reason':'unvoiced_or_unavailable','clip_fraction':.1}},'r:1:0'))
    assert values['dbfs']['unit']=='dBFS' and values['pitch_hz']['value'] is None
    assert values['pitch_hz']['missing_reason']=='unvoiced_or_unavailable'
    assert values['clip_fraction']['value']==.1
    assert 'physical_spl' not in values


def test_h11_04_known_image_fields_and_same_words_in_different_fields():
    fields=fields_for('FrameInterpreter',{'text':'same','model_raw':'{"description":"same","scene":"same","time_of_day":"day"}'},'r:1:0')
    assert [f['value'] for f in fields]==['same','same','day']
    assert len({f['id'] for f in fields})==3
    assert fields[-1]['display_value']=='昼'


def test_h11_05_text_and_quote_unchanged():
    words='<img src=x> original\nquote'
    f=fields_for('TextContext',{'model_raw':json.dumps({'topic':'topic','quote':words,'register':'formal'}),'text':'duplicated repr'},'r:1:0')
    assert by_id(f)['quote']['value']==words and by_id(f)['topic']['value']=='topic'
    assert 'duplicated repr' not in json.dumps(f)


@pytest.mark.parametrize('payload',["{'text': broken}",{'text':"{'scene': broken}"},{'model_raw':'not json'},{'model_raw':'["unknown"]'}])
def test_h11_06_no_legacy_repair_or_exec(payload):
    values=fields_for('FrameInterpreter',payload,'e:1')
    assert values[0]['value'] is None and values[0]['missing_reason']=='unsupported_schema'


def test_h11_07_13_cache_identity_has_all_transform_inputs():
    ref={'stream':'0','frame_id':'frame1','pts':1,'time_base':[1,30000],'interval':[.1,.1], 'media_origin_s':0,'roi':[1,2,3,4],'rotation':0}
    base=ThumbnailCache.identity('a'*64,ref)
    for k,v in [('stream','1'),('frame_id','frame2'),('pts',2),('time_base',[1,1000]),('roi',[2,2,3,4]),('rotation',90)]:
        assert ThumbnailCache.identity('a'*64,{**ref,k:v})!=base
    assert ThumbnailCache.identity('b'*64,ref)!=base
    assert base['size']==[128,88] and base['transform']


def test_h11_08_source_pts_not_merged_interval_start():
    db=sqlite3.connect(':memory:')
    refs=source_refs(db,{'source_refs':[{'modality':'video','interval':[.1,.3],'pts':9,'time_base':[1,30],'media_origin_s':0,'stream':'0'}]},1,1)
    assert refs[0]['media_s']==.3 and refs[0]['interval']==[.1,.3]


def test_h11_09_audio_has_no_invented_image(tmp_path):
    s=make(tmp_path,fixture_events())
    row=row_at(s.index,'run-123',5,'e:2')
    assert row['thumbnail_state']=='audio'
    assert all(r['modality']=='audio' for r in row['source_refs'])


def test_h11_11_incremental_projection_no_future_or_total():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE targets(key TEXT,seq INTEGER,epoch INTEGER,kind TEXT,id TEXT,revision TEXT,label TEXT,body TEXT)')
    builder=ActivityBuilder(db)
    # The completion payload does not exist until after the pending-state assertion.
    builder.consume({'event_seq':1,'kind':'local_model_admitted','epoch':1,'elapsed_s':0,
                     'created_monotonic_s':1,'payload':{'agent':'SpeechASR','accepted_wall_s':1,'input_cursor':None}})
    pending=json.loads(db.execute('SELECT body FROM activity WHERE seq=1').fetchone()[0])
    assert pending['status']=='running' and not pending.get('result_fields')
    output={'text':'newly arrived original'}
    builder.consume({'event_seq':2,'kind':'local_model_completed','epoch':1,'elapsed_s':1,
                     'created_monotonic_s':2,'payload':{'agent':'SpeechASR','accepted_wall_s':1,'input_cursor':None,'output':output}})
    arrived=present(json.loads(db.execute('SELECT body FROM activity WHERE seq=2').fetchone()[0]),'run-1')
    assert arrived['row_id']=='e:1' and arrived['result_fields'][0]['value']==output['text']
    assert json.loads(db.execute('SELECT body FROM activity WHERE seq=1').fetchone()[0])==pending


def test_h11_14_saved_result_time_distinct_from_source(tmp_path):
    s=make(tmp_path,fixture_events());row=row_at(s.index,'run-123',8,'e:6')
    assert row['available_at']==3 and row['source_refs'][0]['media_s']==.4


def test_h11_15_cpu_latest_prefix_and_anomaly_not_aggregated(tmp_path):
    events=fixture_events()
    events[15]['payload']['measurement']={'flow_mean_px':2,'cut':True}
    s=make(tmp_path,events)
    early=page(s.index,'run-123',5.06)
    late=page(s.index,'run-123',5.11)
    rows1=[r for r in early['items'] if r['agent']=='MotionCut']
    rows2=[r for r in late['items'] if r['agent']=='MotionCut']
    assert len(rows1)==1 and len(rows2)==2
    assert rows1[0]['result_fields'][0]['value']==1
    assert any(by_id(r['result_fields'])['cut']['value'] is True for r in rows2)
    assert rows1[0]['aggregation']['method']=='latest_arrived'


def test_h11_15_transcript_versions_stay_distinct(tmp_path):
    events=fixture_events()
    for seq,rev in [(22,1),(23,2)]:
        events.append({'schema_version':'3','event_seq':seq,'kind':'transcript_version','epoch':3,'elapsed_s':seq,
                       'created_monotonic_s':seq,'payload':{'segment_id':'segment-a','revision':rev,'text':'unchanged text'}})
    s=make(tmp_path,events)
    old=page(s.index,'run-123',22);later=page(s.index,'run-123',23)
    assert not any(r['target_ref']=='r:23:0' for r in old['items'])
    assert sum(r['target_ref'] in ('r:22:0','r:23:0') for r in later['items'])==2


def test_h11_18_evaluation_does_not_become_public_readout():
    evaluation={'status':'valid','kind':'dominance_evaluation','profile_id':'topic','parsed':{'topic':{'score':3.47}}}
    values=by_id(fields_for('DominanceReader',evaluation,'r:1:0'))
    assert values['public']['value'] is None
    rejected=fields_for('DominanceReader',{'status':'error','reason':'score_expectation','raw':{'score':2.69}},'r:1:0')
    assert by_id(rejected)['public']['value'] is None and '2.69' not in json.dumps(rejected)
    readout={'kind':'dominance_readout','status':'valid','items':{'topic':None},'item_labels':{'topic':'話題'}}
    assert by_id(fields_for('DominanceReader',readout,'r:1:0'))['topic']['value'] is None


def test_h11_16_page_limit_no_large_original_payload(tmp_path):
    s=make(tmp_path,fixture_events());p=page(s.index,'run-123',9,limit=1000)
    assert len(p['items'])<=60
    assert all('outputs' not in row and len(row['result_fields'])<=4 for row in p['items'])


def test_h11_04_item_targets_do_not_get_replaced_by_sibling_facet(tmp_path):
    events=fixture_events()
    events[6]['payload']['output']={'description':'same','scene':'industrial','time_of_day':'day'}
    raw=json.dumps(events[6]['payload']['output'])
    events[7]['payload'].update(facet='place.scene_type',model_raw=raw,text='same')
    events[8]['payload'].update(facet='place.apparent_time_of_day',model_raw=raw,text='same')
    s=make(tmp_path,events);row=row_at(s.index,'run-123',9,'e:6')
    fields=by_id(row['result_fields'])
    assert len(fields)==3
    assert fields['scene']['target_ref']=='r:8:0'
    assert fields['time_of_day']['target_ref']=='r:9:0'
    assert len(row['source_refs'])==1


def test_h11_07_12_13_thumbnail_validation_and_bounded_cache(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import base64, io
    from PIL import Image
    image=Image.new('RGB',(32,18),(25,70,100));buf=io.BytesIO();image.save(buf,format='JPEG')
    jpeg=base64.b64encode(buf.getvalue()).decode()
    calls=[]
    def decode(*args,**kwargs):
        calls.append(kwargs)
        return dict(jpeg=jpeg,pts=4,time_base='1/10',source_dimensions=[32,18],media_s=.4)
    monkeypatch.setattr('context_fields.debug_review.frame_at',decode)
    row={'revision':2,'epoch':1,'source_refs':[{'ref_id':'a','modality':'video','pts':4,'time_base':[1,10],
         'stream':'0','interval':[.1,.4],'media_s':.4,'media_origin_s':0,'frame_id':'frame-A','roi':None}]}
    monkeypatch.setattr('context_fields.activity.row_at',lambda *args:row)
    asset=SimpleNamespace(sha256='a'*64)
    review=SimpleNamespace(root=tmp_path,index=None,assets=SimpleNamespace(get=lambda _:asset),match=lambda *args:{'media_match':'hash_verified'})
    cache=ThumbnailCache(review,max_files=1,max_bytes=16000)
    cold=cache.get('run-1',2,'r',2,'a','asset','gen1')
    warm=cache.get('run-1',2,'r',2,'a','asset','gen2')
    assert cold['jpeg']==warm['jpeg'] and warm['cached'] and len(calls)==1
    assert warm['generation']=='gen2' and warm['revision']==2
    with pytest.raises(ValueError,match='revision'):cache.get('run-1',2,'r',1,'a','asset',1)
    with pytest.raises(ValueError,match='source'):cache.get('run-1',2,'r',2,'wrong','asset',1)
    row['source_refs'][0]['roi']=[0,0,8,8]
    cropped=cache.get('run-1',2,'r',2,'a','asset',1)
    assert cropped['cache_key']!=cold['cache_key'] and len(list(cache.root.glob('*.json')))==1
    row['source_refs'][0]['pts']=5
    failed=cache.get('run-1',2,'r',2,'a','asset',1)
    assert failed['state']=='missing' and failed['reason']=='decoded_pts_mismatch'
    review.match=lambda *args:{'media_match':'mismatch'}
    with pytest.raises(ValueError,match='verify_media'):cache.get('run-1',2,'r',2,'a','asset',1)
