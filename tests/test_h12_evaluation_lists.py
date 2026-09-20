import copy
import hashlib
import json
import pytest
from test_h1_activity import make
from context_fields.evaluation_lists import page, evaluation_at


class Journal:
    def __init__(self):
        self.events = []
        self.add('display', {'clock': {'state':'playing','epoch':1,'media_s':0}})

    def add(self, kind, payload, epoch=1, wall=None):
        seq = len(self.events)+1
        self.events.append(dict(schema_version='3',event_seq=seq,kind=kind,payload=copy.deepcopy(payload),epoch=epoch,
            elapsed_s=seq,created_monotonic_s=wall if wall is not None else seq,media_s=seq))
        return seq

    def observation(self, oid='a', facet='place.scene_type', revision=1, wall=None, agent='FrameInterpreter', epoch=1):
        return self.add('record', dict(id=oid,kind='observation',epoch=epoch,revision=revision,agent=agent,
            facet=facet,text='same words',description='original',scene='room',time_of_day='day',subject='scene',dependencies={},
            source_refs=[dict(root='same-root',modality='video',interval=[.1,.1],pts=1,time_base=[1,10])]), epoch, wall)

    def candidate(self, oid='a', facet='place.scene_type', epoch=1):
        self.add('hypothesis', dict(id='h-'+oid,kind='hypothesis',revision=1,facet=facet,subject='scene',label='claim'),epoch)
        self.add('candidate_link', dict(observation_id=oid,hypothesis_id='h-'+oid),epoch)

    def request(self, oids=('a',), facets=('place.scene_type',), epoch=1):
        request = json.dumps({'state':{'unit'+str(i):dict(observation_id=oid,facet=facet,subject='scene',claim='claim') for i,(oid,facet) in enumerate(zip(oids,facets))}})
        self.add('evaluation_requested', dict(role='evidence',request=request,unit_ids=list(oids)),epoch)
        return hashlib.sha256(request.encode()).hexdigest()

    def evaluation(self, request_hash, oid='a', rev=1, epoch=1, error=False, support=.1, **extra):
        return self.add('record', dict(id='eval-'+oid+'-'+str(rev),kind='evidence_evaluation',revision=1,epoch=epoch,
            evaluation_kind='evidence',observation_id=oid,hypothesis_id='h-'+oid,dependencies={oid:rev},
            input_hash=request_hash,p_support=support,p_contradict=.2,r=.8,a=.9,
            parsed=None if error else {'relation':{'probabilities':{'support':support,'contradict':.2,'insufficient':.7}}},
            status='error' if error else 'valid',reason='score_expectation' if error else None,**extra),epoch)


def fixture(tmp_path, j):
    return make(tmp_path,j.events).index


def result(index, pos=999, **kw):return page(index,'run-123',pos,**kw)


def single():
    j=Journal();seq=j.observation();j.candidate();h=j.request();return j,seq,h


def test_09_direct_completion_and_18_rewind(tmp_path):
    j,seq,h=single();before=len(j.events);arrived=j.evaluation(h);index=fixture(tmp_path,j)
    pending=result(index,before)
    assert pending['logs']['items'][0]['evaluation_state']=='評価中'
    assert not pending['evaluations']['items']
    done=result(index,arrived)
    assert not done['logs']['items']
    assert len(done['evaluations']['items'])==1
    card=evaluation_at(index,'run-123',arrived,done['evaluations']['items'][0]['row_id'])
    assert card['direct_target']['verified'] and card['direct_target']['unit']=='unit0'
    assert f'e:{before}' in card['members']
    assert card['original_targets']==[f'r:{seq}:0']
    assert card['original_activities'][0]['row_id']==f'r:{seq}:0'
    assert card['original_fields'] and card['source_refs']
    assert result(index,before)==pending


@pytest.mark.parametrize('support',[0,.01])
def test_10_negative_or_insufficient_is_completion(tmp_path,support):
    j,_,h=single();j.evaluation(h,support=support);p=result(fixture(tmp_path,j))
    assert not p['logs']['items']
    assert p['evaluations']['items'][0]['evaluation_state']=='評価済み'


def test_11_multifacet_row_requires_all_registered_targets(tmp_path):
    j=Journal();job=j.add('local_model_completed',dict(agent='FrameInterpreter',output={'description':'job','scene':'room','time_of_day':'day'}),wall=100)
    j.observation(wall=100);j.observation('b','place.apparent_time_of_day',wall=100)
    j.candidate();j.candidate('b','place.apparent_time_of_day');h=j.request(('a','b'),('place.scene_type','place.apparent_time_of_day'))
    partial=j.evaluation(h);complete=j.evaluation(h,'b');index=fixture(tmp_path,j)
    row=result(index,partial)['logs']['items'][0]
    assert row['row_id']==f'e:{job}' and row['evaluation_state']=='一部評価済み'
    assert not result(index,complete)['logs']['items']
    assert len(result(index,complete)['evaluations']['items'])==2


@pytest.mark.parametrize('failure',['record','rejection','dispatcher'])
def test_12_13_same_request_does_not_complete_bad_unit(tmp_path,failure):
    j=Journal();j.observation();b=j.observation('b');j.candidate();j.candidate('b');h=j.request(('a','b'),('place.scene_type',)*2)
    j.evaluation(h)
    if failure=='record':j.evaluation(h,'b',error=True)
    elif failure=='rejection':j.add('evaluation_rejected',dict(role='evidence',unit='b',reason='score_expectation'))
    else:j.add('dispatcher_result',dict(status='error',unit_ids=['b'],reason='timeout'))
    p=result(fixture(tmp_path,j));assert len(p['logs']['items'])==1
    assert p['logs']['items'][0]['row_id']==f'r:{b}:0'
    assert p['logs']['items'][0]['evaluation_state']=='評価エラー'
    assert p['evaluations']['items'][0]['status']=='error'


@pytest.mark.parametrize('change',['missing_revision','old_revision','different_epoch','wrong_hash','unregistered','wrong_hypothesis','wrong_facet'])
def test_15_no_words_time_root_or_invalid_reference_join(tmp_path,change):
    j,_,h=single();seq=j.evaluation(h);p=j.events[seq-1]['payload']
    if change=='missing_revision':p['dependencies']={}
    if change=='old_revision':p['dependencies']={'a':0}
    if change=='different_epoch':j.events[-1]['epoch']=2;p['epoch']=2
    if change=='wrong_hash':p['input_hash']='other'
    if change=='unregistered':j.events=[e for e in j.events if e['kind']!='candidate_link'];[e.update(event_seq=i,elapsed_s=i) for i,e in enumerate(j.events,1)]
    if change=='wrong_hypothesis':p['hypothesis_id']='other'
    if change=='wrong_facet':j.events[2]['payload']['facet']='different'
    result_=result(fixture(tmp_path,j));assert len(result_['logs']['items'])==1
    assert not result_['logs']['items'][0]['evaluation']['complete']


def test_14_dominance_alias_and_asr_dependency_are_not_direct(tmp_path):
    j,_,h=single();j.observation('asr',agent='SpeechASR');j.observation('text',agent='TextContext')
    p=dict(id='dom',kind='dominance_evaluation',revision=1,status='valid',dependencies={'a':1},parsed={})
    at=j.add('record',p)
    j.add('record',dict(id='readout',kind='dominance_readout',revision=1,evaluation_id='dom',dependencies={'dom':1,'a':1},status='valid',items={'day':20},profile_id='place',expires_after_media_s=2))
    j.add('record',p)
    j.add('display',{'clock':{'epoch':1,'media_s':50,'state':'playing'}})
    index=fixture(tmp_path,j);r=result(index)
    assert len(r['logs']['items'])==3 and len(r['evaluations']['items'])==1
    assert r['evaluations']['items'][0]['result_seq']==at
    assert '期限切れ' in r['evaluations']['items'][0]['evaluation_state']
    assert len(result(index,at)['evaluations']['items'])==1


def test_17_new_revision_ignores_old_completion_and_ttl_no_rewait(tmp_path):
    j,seq,h=single();j.evaluation(h);old=len(j.events)
    j.add('invalidation',dict(ids=['eval-a-1'],reason='expired'))
    expired=len(j.events);j.observation(revision=2)
    index=fixture(tmp_path,j)
    assert not result(index,old)['logs']['items'] and not result(index,expired)['logs']['items']
    assert '失効' in result(index,expired)['evaluations']['items'][0]['evaluation_state']
    assert len(result(index)['logs']['items'])==1
    assert not result(index)['logs']['items'][0]['evaluation']['complete']


def test_02_19_duplicates_and_state_do_not_change_arrival_order(tmp_path):
    j,seq,h=single();b=j.observation('b');j.add('record',j.events[seq-1]['payload']);j.evaluation(h)
    p=result(fixture(tmp_path,j),keep=f'r:{seq}:0')
    assert [r['row_id'] for r in p['logs']['items']]==[f'r:{b}:0',f'r:{seq}:0']
    assert p['logs']['items'][-1]['result_seq']==seq


def test_08_20_filter_before_page_and_keep_older_anchor(tmp_path):
    j,seq,h=single();j.evaluation(h)
    for i in range(95):j.observation('later'+str(i))
    index=fixture(tmp_path,j);r=result(index)
    assert r['logs']['total']==95 and len(r['logs']['items'])==30
    old=result(index,log_offset=90)
    assert len(old['logs']['items'])==5
    retained=result(index,log_anchor=f'r:{seq}:0')
    assert any(x['row_id']==f'r:{seq}:0' and x['evaluation']['complete'] for x in retained['logs']['items'])
    assert len(json.dumps(retained,ensure_ascii=False).encode())<=300000
    assert result(index)['logs']['total']==95


def test_16_cpu_only_and_unknown_no_invented_denominator(tmp_path):
    j=Journal();j.observation(agent='MotionCut');j.observation('asr',agent='SpeechASR')
    r=result(fixture(tmp_path,j));assert {x['evaluation_state'] for x in r['logs']['items']}=={'計測のみ','対応未確認'}
    assert all('ratio' not in x['evaluation'] for x in r['logs']['items'])


def test_multifacet_unknown_member_keeps_partial(tmp_path):
    j=Journal();j.add('local_model_completed',dict(agent='FrameInterpreter',output={'description':'job'}),wall=100)
    j.observation(wall=100);j.observation('missing','unregistered',wall=100);j.candidate();h=j.request();j.evaluation(h)
    p=result(fixture(tmp_path,j));assert p['logs']['items'][0]['evaluation_state']=='一部評価済み'
    assert not p['logs']['items'][0]['evaluation']['target_set_known']


def test_job_output_without_facet_record_is_not_auxiliary(tmp_path):
    j=Journal();j.add('local_model_completed',dict(agent='FrameInterpreter',output={'scene':'room','time_of_day':'day'}),wall=100)
    j.observation(wall=100);j.candidate();h=j.request();j.evaluation(h)
    p=result(fixture(tmp_path,j));assert p['logs']['items'][0]['evaluation_state']=='一部評価済み'
    assert not p['logs']['items'][0]['evaluation']['target_set_known']


def test_stopped_is_not_success_or_cpu_failure(tmp_path):
    j,_,_=single();j.add('stop',dict(reason='manual'));p=result(fixture(tmp_path,j))
    assert p['logs']['items'][0]['evaluation_state']=='未評価 · 停止中'
    assert not p['logs']['items'][0]['evaluation']['complete']


def test_upper_original_job_is_fixed_to_evaluation_cursor(tmp_path):
    j,seq,h=single();ev=j.evaluation(h);j.observation(revision=2)
    index=fixture(tmp_path,j);p=result(index);card=p['evaluations']['items'][0]
    assert '旧版' in card['evaluation_state']
    full=evaluation_at(index,'run-123',len(j.events),card['row_id'])
    assert full['original_activities'][0]['inspection_cursor']==ev
    assert full['original_activities'][0]['members']==[f'r:{seq}:0']


def test_unknown_request_hash_is_not_labeled_numeric_failure(tmp_path):
    j,_,_=single();j.evaluation('unknown')
    p=result(fixture(tmp_path,j));assert p['logs']['items'][0]['evaluation_state']=='対応未確認'
    card=evaluation_at(fixture(tmp_path/'second',j),'run-123',99,p['evaluations']['items'][0]['row_id'])
    assert 'request_hash_unit' in card['direct_target']['missing']


def test_each_item_mapping_is_available_in_original_detail(tmp_path):
    from context_fields.activity import row_at
    j,seq,h=single();j.evaluation(h);index=fixture(tmp_path,j)
    row=row_at(index,'run-123',len(j.events),f'r:{seq}:0')
    assert row['evaluation']['complete']
    assert row['evaluation']['targets'][0]['source_key']==f'r:{seq}:0'
