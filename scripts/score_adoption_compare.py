"""OFFLINE_COMPARISON only. No runtime registration, I/O, clocks or network.

Decimal/Fraction arithmetic is independent of the production validator. Its
ordinary-float result is retained separately and defines C0's numeric decision.
"""
import json
import math
from decimal import Decimal, localcontext
from fractions import Fraction

D=Decimal
TOL=D('0.000001')
POLICIES={'C0':'strict-v03-comparison-v1','C1':'documented-provider-precision-pending',
          'CP':'provider-primary-display-proposal-v1','CD':'distribution-derived-display-proposal-v1'}
STAGES={'RAW_HTTP_BODY','APPLICATION_RESERIALIZED','REPORT_EXCERPT','SYNTHETIC_FIXTURE'}


def decode(data):
    duplicates=[];lexemes=[]
    def number(s):lexemes.append(s);return D(s)
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:duplicates.append(k)
            result[k]=v
        return result
    def constant(s):raise ValueError('nonfinite_json_constant')
    value=json.loads(data,parse_float=number,parse_int=number,parse_constant=constant,object_pairs_hook=pairs)
    return value,duplicates,lexemes


def number(v):
    if isinstance(v,bool) or not isinstance(v,(int,float,D)):raise ValueError('numeric_type')
    n=D(str(v))
    if not n.is_finite():raise ValueError('nonfinite_number')
    return n


def check_answer(a,q):
    result={'structural_errors':[],'relation_status':'NOT_APPLICABLE'}
    try:
        if not isinstance(a,dict) or a.get('type')!=q['type']:raise ValueError('primitive_type')
        confidence=number(a['confidence'])
        if not 0<=confidence<=1:raise ValueError('confidence_range')
        keys={str(i) for i in range(5)} if q['type']=='score' else set(q['criteria'])
        p=a['probabilities']
        if not isinstance(p,dict) or set(p)!=keys:raise ValueError('probability_keys')
        values={k:number(v) for k,v in p.items()}
        if any(not 0<=v<=1 for v in values.values()):raise ValueError('probability_range')
        with localcontext() as ctx:
            ctx.prec=80
            total=sum(values.values(),D(0));result.update(probability_sum=total,probabilities=values,confidence=confidence)
            if total<=0 or abs(total-1)>TOL:raise ValueError('probability_sum')
            result.update(normalized=total!=1,normalization_factor=1/total)
            if q['type']=='choice':
                if a['choice'] not in p or values[a['choice']]!=max(values.values()):raise ValueError('choice_argmax')
                result['normalized_probabilities']={k:v/total for k,v in values.items()}
            elif q['type']=='score':
                if len(q['criteria'])!=5 or a['legend']!={str(i):v for i,v in enumerate(q['criteria'])}:raise ValueError('score_legend')
                score=number(a['score'])
                if not 0<=score<=4:raise ValueError('score_range')
                mean=sum(D(k)*v for k,v in values.items());normalized=mean/total
                exact=sum(Fraction(int(k))*Fraction(v) for k,v in values.items())/sum(map(Fraction,values.values()))
                delta=score-normalized
                result.update(provider_score=score,mean_before=mean,mean_after=normalized,
                    exact_mean_fraction=str(exact),delta_before=score-mean,delta_after=delta,
                    exact_delta_fraction=str(Fraction(score)-exact),
                    relation_status='CONSISTENT' if abs(delta)<=TOL else 'NUMERIC_DISCREPANCY')
            else:raise ValueError('unsupported_primitive')
    except (ValueError,KeyError,TypeError,AttributeError,ArithmeticError) as error:
        result['structural_errors'].append(str(error))
    # This comparison path is not called independent arithmetic.
    from context_fields.jev import validate_answer
    try:
        ordinary=json.loads(json.dumps(a,default=float,allow_nan=True))
        result['production']={'status':'accepted','value':validate_answer(ordinary,q)}
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as error:
        result['production']={'status':'rejected','reason':str(error)}
    return result


def qualification(assess,context):
    """Three-valued eligibility at an explicitly saved display cursor, never NOW."""
    failed=[];missing=[]
    if assess is None:missing.append('assessability_missing')
    elif assess.get('structural_errors'):failed.append('assessability_invalid')
    else:
        p=assess.get('normalized_probabilities',{})
        if set(p)!={'assessable','insufficient','conflicting'}:missing.append('assessability_schema_missing')
        elif not p['assessable']>max(p['insufficient'],p['conflicting']):failed.append('assessable_not_strict_winner')
    if context.get('diagnostic_only'):failed.append('DIAGNOSTIC_NOT_FIELD_SNAPSHOT')
    for key in ('snapshot_match','epoch_match','scope_match','profile_match','dependencies_valid','ttl_valid','deadline_valid','readout_active'):
        value=context.get(key)
        if value is False:failed.append(key)
        elif value is not True:missing.append(key)
    return {'status':'INELIGIBLE' if failed else 'NOT_EVALUATED' if missing else 'ELIGIBLE',
            'failed':failed,'missing':missing,'context':context,
            'assessability':None if assess is None else assess.get('normalized_probabilities')}


def compare_response(request,body,*,stage,contexts=None,unit_ids=None,http_status=None,expected_returned_model=None):
    assert stage in STAGES
    contexts=contexts or {};envelope=[];duplicates=[];lexemes=[]
    try:raw,duplicates,lexemes=decode(body)
    except (ValueError,TypeError,UnicodeError):raw={};envelope.append('json_syntax_or_nonfinite')
    if duplicates:envelope.append('duplicate_json_keys')
    if http_status is not None and http_status!=200:envelope.append('http_status')
    if not isinstance(raw,dict):raw={};envelope.append('response_envelope')
    if raw.get('model')!=(expected_returned_model or request.get('model')):envelope.append('model_mismatch')
    answers=raw.get('answers',{})
    if not isinstance(answers,dict):answers={};envelope.append('answers_type')
    questions=request['questions']
    if set(answers)!=set(questions):envelope.append('answer_keys')
    units=unit_ids if unit_ids is not None else sorted({k.rsplit('.',1)[0] for k in questions})
    checks={qid:check_answer(answers.get(qid),q) for qid,q in questions.items()}
    policies={};rows=[]
    for policy in POLICIES:
        unit_results={}
        for uid in units:
            qids=[q for q in questions if q.startswith(uid+'.')]
            hard=list(envelope);warnings=[]
            for qid in qids:
                c=checks[qid];hard.extend(f'{qid}:{e}' for e in c['structural_errors'])
                prod=c['production']
                if prod['status']=='rejected' and (policy=='C0' or prod['reason']!='score_expectation'):
                    hard.append(f'{qid}:production:{prod["reason"]}')
                if c['relation_status']=='NUMERIC_DISCREPANCY' and policy in ('CP','CD'):
                    warnings.append(f'{qid}:NUMERIC_DISCREPANCY')
                elif not c['structural_errors'] and prod.get('reason')=='score_expectation' and policy in ('CP','CD'):
                    warnings.append(f'{qid}:PRODUCTION_FLOAT_RELATION_REJECTION')
            eligible=qualification(checks.get(uid+'.assessability'),contexts.get(uid,{}))
            unit_results[uid]={'numeric_accepted':not hard,'hard_errors':hard,'warnings':warnings,'eligibility':eligible}
        stop=any(v['hard_errors'] for v in unit_results.values())
        policies[policy]={'policy_id':POLICIES[policy], 'status':'NOT_EVALUATED_SPEC_MISSING' if policy=='C1' else 'EVALUATED',
            'units':unit_results if policy!='C1' else {},'stop_new_sends':None if policy=='C1' else stop,
            'stop_scope':'existing_global_on_hard_error','history_mode':'STATIC_SAME_SAVED_RESPONSE_NOT_LIVE_REPLAY'}
    for qid,q in questions.items():
        if q['type']!='score':continue
        uid=qid.rsplit('.',1)[0];c=checks[qid];values={}
        for policy in POLICIES:
            v={'policy_id':POLICIES[policy],'selected_source':{'C0':'provider_score','C1':None,'CP':'provider_score','CD':'returned_distribution_mean'}[policy],
               'display_name':{'C0':'現契約の返却score','C1':'精度仕様未確認','CP':'提供元score由来','CD':'返却分布から算出'}[policy],
               'candidate_score':None,'candidate_value_pct':None,'public_value_pct':None,'public_reason':None}
            if policy=='C1':v['public_reason']='NOT_EVALUATED_SPEC_MISSING';values[policy]=v;continue
            unit=policies[policy]['units'][uid]
            own_ok=not envelope and not c['structural_errors'] and (c['production']['status']=='accepted' or
                policy!='C0' and c['production'].get('reason')=='score_expectation')
            if own_ok:
                chosen=c['mean_after'] if policy=='CD' else c['provider_score']
                v.update(candidate_score=chosen,candidate_value_pct=chosen*25)
            if not unit['numeric_accepted']:reason='UNIT_HARD_ERROR'
            elif unit['eligibility']['status']!='ELIGIBLE':reason=unit['eligibility']['status']
            else:reason=None;v['public_value_pct']=v['candidate_value_pct']
            v.update(public_reason=reason,unit_numeric_accepted=unit['numeric_accepted'],hard_errors=unit['hard_errors'],
                     warnings=unit['warnings'],stop_new_sends=policies[policy]['stop_new_sends'])
            values[policy]=v
        rows.append({'question_id':qid,'unit_id':uid,'evidence_stage':stage,'numeric':c,
            'eligibility':policies['C0']['units'][uid]['eligibility'],'policies':values})
    return {'rows':rows,'policies':policies,'envelope_errors':envelope,'duplicate_keys':duplicates,
            'original_duplicate_keys':duplicates if stage in ('RAW_HTTP_BODY','SYNTHETIC_FIXTURE') else 'UNDETERMINABLE_AFTER_PARSE',
            'numeric_lexemes':lexemes,'lexeme_origin':stage,'question_checks':checks}
