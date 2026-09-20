"""PC-01..16 comparison-only tests; existing D-16 remains unchanged."""
import copy
import json
from decimal import Decimal as D
from pathlib import Path

import pytest
from scripts.score_adoption_compare import compare_response,check_answer,decode,qualification


def fixture(score=2):
    criteria=['zero','one','two','three','four']
    request={'model':'jev-1.13.0','questions':{
        'u.value':{'type':'score','criteria':criteria},
        'u.assessability':{'type':'choice','criteria':{'assessable':'a','insufficient':'b','conflicting':'c'}}}}
    raw={'model':request['model'],'answers':{
        'u.value':{'type':'score','score':score,'confidence':.17,'legend':dict(zip(map(str,range(5)),criteria)),
                   'probabilities':{'0':0,'1':0,'2':1,'3':0,'4':0}},
        'u.assessability':{'type':'choice','choice':'assessable','confidence':.2,
                          'probabilities':{'assessable':1,'insufficient':0,'conflicting':0}}}}
    context={k:True for k in ['snapshot_match','epoch_match','scope_match','profile_match','dependencies_valid','ttl_valid','deadline_valid','readout_active']}
    return request,raw,context


def compare(request,raw,context):
    return compare_response(request,json.dumps(raw),stage='SYNTHETIC_FIXTURE',contexts={'u':context})


@pytest.mark.parametrize('sign',[-1,1])
@pytest.mark.parametrize('gap',['0.000000999999','0.000001','0.000001000001'])
def test_PC04_relation_boundary_and_float_contract_are_separate(sign,gap):
    request,raw,ctx=fixture();text=json.dumps(raw).replace('"score": 2,','"score": '+str(D(2)+sign*D(gap))+',')
    result=compare_response(request,text,stage='SYNTHETIC_FIXTURE',contexts={'u':ctx})
    row=result['rows'][0];numeric=row['numeric']
    assert numeric['relation_status']==('NUMERIC_DISCREPANCY' if D(gap)>D('1e-6') else 'CONSISTENT')
    assert numeric['probability_sum']==1
    assert (row['policies']['C0']['candidate_score'] is not None)==(numeric['production']['status']=='accepted')
    assert row['policies']['CP']['candidate_score']==D(2)+sign*D(gap)
    if numeric['production']['status']=='rejected':assert row['policies']['CP']['warnings']


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True,'2',None,-.1,4.1])
def test_PC05_PC06_invalid_score_not_rescued_by_distribution(bad):
    req,raw,ctx=fixture(bad);result=compare(req,raw,ctx)
    for p in ('C0','CP','CD'):
        assert result['rows'][0]['policies'][p]['candidate_score'] is None
        assert result['policies'][p]['stop_new_sends']


@pytest.mark.parametrize('defect',['missing-score','missing-probability','extra-probability','bad-legend','bool-probability','negative-probability','string-confidence','missing-answer','extra-answer','model'])
def test_PC05_PC06_common_hard_errors(defect):
    req,raw,ctx=fixture();a=raw['answers']['u.value']
    if defect=='missing-score':del a['score']
    if defect=='missing-probability':del a['probabilities']['0']
    if defect=='extra-probability':a['probabilities']['5']=0
    if defect=='bad-legend':a['legend']['0']='wrong'
    if defect=='bool-probability':a['probabilities']['0']=False
    if defect=='negative-probability':a['probabilities']['0']=-.01
    if defect=='string-confidence':a['confidence']='.5'
    if defect=='missing-answer':del raw['answers']['u.value']
    if defect=='extra-answer':raw['answers']['unknown']=copy.deepcopy(a)
    if defect=='model':raw['model']='different'
    result=compare(req,raw,ctx)
    for p in ('C0','CP','CD'):
        assert result['policies'][p]['stop_new_sends']
        assert result['rows'][0]['policies'][p]['public_value_pct'] is None


def test_PC05_duplicate_keys_before_json_collapse():
    req,raw,ctx=fixture();text=json.dumps(raw).replace('"score": 2','"score": 4, "score": 2')
    result=compare_response(req,text,stage='SYNTHETIC_FIXTURE',contexts={'u':ctx})
    assert result['duplicate_keys']==['score']
    assert result['policies']['CP']['stop_new_sends']


@pytest.mark.parametrize('extra',['0.0000005','0.0000011'])
def test_PC07_sum_normalization_and_no_clipping(extra):
    req,raw,ctx=fixture();a=raw['answers']['u.value'];a['probabilities']['0']=float(extra)
    result=compare(req,raw,ctx);r=result['rows'][0]
    if D(extra)<=D('1e-6'):
        assert r['numeric']['normalized'] is True
        assert r['numeric']['mean_before']==2 and r['numeric']['mean_after']<2
        assert r['policies']['CD']['candidate_score']==r['numeric']['mean_after']
    else:
        assert r['numeric']['structural_errors']==['probability_sum']
        assert r['policies']['CD']['candidate_score'] is None


@pytest.mark.parametrize('condition',['insufficient','tie','dependencies_valid','epoch_match','ttl_valid','profile_match','scope_match','deadline_valid','missing'])
def test_PC08_public_eligibility_is_independent_of_numeric_policy(condition):
    req,raw,ctx=fixture(2.1)
    if condition in ('insufficient','tie'):
        p=raw['answers']['u.assessability'];p['choice']='insufficient'
        p['probabilities']={'assessable':.5 if condition=='tie' else 0,'insufficient':.5 if condition=='tie' else 1,'conflicting':0}
    elif condition=='missing':ctx.pop('profile_match')
    else:ctx[condition]=False
    result=compare(req,raw,ctx);r=result['rows'][0]
    for p in ('CP','CD'):
        assert r['policies'][p]['candidate_score'] is not None
        assert r['policies'][p]['public_value_pct'] is None
        assert not r['policies'][p]['stop_new_sends']


def test_PC09_PC10_mixed_unit_null_and_other_choice_with_global_stop():
    req,raw,ctx=fixture()
    req['questions']['u.other']=copy.deepcopy(req['questions']['u.value'])
    raw['answers']['u.other']=copy.deepcopy(raw['answers']['u.value']);raw['answers']['u.other']['score']=True
    req['questions']['place.choice']={'type':'choice','criteria':{'day':'day','night':'night'}}
    raw['answers']['place.choice']={'type':'choice','choice':'day','confidence':.9,'probabilities':{'day':1,'night':0}}
    result=compare(req,raw,ctx)
    for p in ('C0','CP','CD'):
        assert result['rows'][0]['policies'][p]['public_value_pct'] is None
        assert result['policies'][p]['units']['place']['numeric_accepted']
        assert result['policies'][p]['stop_new_sends']


def test_PC10_PC11_large_legal_discrepancy_is_warning_contract_change():
    req,raw,ctx=fixture(4);raw['answers']['u.value']['probabilities']={'0':1,'1':0,'2':0,'3':0,'4':0}
    result=compare(req,raw,ctx);r=result['rows'][0]
    assert result['policies']['C0']['stop_new_sends']
    assert r['policies']['CP']['public_value_pct']==100
    assert r['policies']['CD']['public_value_pct']==0
    for p in ('CP','CD'):
        assert not result['policies'][p]['stop_new_sends']
        assert r['policies'][p]['warnings']


def test_PC12_nonexclusive_scores_not_confidence_or_sum_to_100():
    req,raw,ctx=fixture(3);raw['answers']['u.value']['probabilities']={'0':0,'1':0,'2':0,'3':1,'4':0}
    req['questions']['u.other']=copy.deepcopy(req['questions']['u.value'])
    raw['answers']['u.other']=copy.deepcopy(raw['answers']['u.value']);raw['answers']['u.other']['confidence']=.99
    result=compare(req,raw,ctx)
    assert [r['policies']['CP']['public_value_pct'] for r in result['rows']]==[75,75]


def test_PC02_PC03_PC13_PC14_PC15_saved_corpus_evidence_and_case01():
    root=Path(__file__).resolve().parents[1];out=root/'artifacts/score-contract-offline/decision-prep-20260920'
    rows=[json.loads(s) for s in (out/'comparison.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows)==len({r['sample_id'] for r in rows})==55
    assert sum(r['evidence_stage']=='RAW_HTTP_BODY' for r in rows)==1
    assert sum(r['evidence_stage']=='APPLICATION_RESERIALIZED' for r in rows)==54
    raw=next(r for r in rows if r['evidence_stage']=='RAW_HTTP_BODY')
    assert raw['numeric']['provider_score']=='2.69' and D(raw['numeric']['mean_after'])==D('2.70')
    assert D(raw['numeric']['delta_after'])==D('-.01')
    assert D(raw['policies']['CP']['candidate_value_pct'])==D('67.25')
    assert D(raw['policies']['CD']['candidate_value_pct'])==D('67.50')
    assert raw['policies']['CP']['public_value_pct'] is None
    assert all(r['policies']['C1']['public_reason']=='NOT_EVALUATED_SPEC_MISSING' for r in rows)
    assert all(r['policies']['CP']['candidate_score'] is not None for r in rows)
    assert all(r['policies']['CP']['policy_id'] for r in rows)
    summary=json.loads((out/'collection-manifest.json').read_text(encoding='utf-8'))
    assert all(s['old_sample_identity_set_equal'] for s in summary['source_collection'])
    assert summary['excluded_evidence_stages']==['REPORT_EXCERPT','SYNTHETIC_FIXTURE']


def test_PC02_explicit_mock_model_never_passes_as_live():
    req,raw,ctx=fixture();raw['model']='MOCK-local-wiring-v1'
    assert compare(req,raw,ctx)['policies']['CP']['stop_new_sends']
    result=compare_response(req,json.dumps(raw),stage='APPLICATION_RESERIALIZED',contexts={'u':ctx},expected_returned_model='MOCK-local-wiring-v1')
    assert not result['policies']['C0']['stop_new_sends']


@pytest.mark.parametrize('status',[400,500])
def test_PC10_http_error_still_global(status):
    req,raw,ctx=fixture();result=compare_response(req,json.dumps(raw),stage='RAW_HTTP_BODY',http_status=status,contexts={'u':ctx})
    assert all(result['policies'][p]['stop_new_sends'] for p in ('C0','CP','CD'))
