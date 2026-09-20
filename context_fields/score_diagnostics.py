"""Independent Decimal arithmetic over captured JSON tokens; no I/O or models."""
from decimal import Decimal, localcontext
import json


def analyze_body(body,request):
    tokens=[];duplicates=[]
    def number(token):tokens.append(token);return Decimal(token)
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:duplicates.append(k)
            result[k]=v
        return result
    def invalid(token):raise ValueError('non_json_numeric_constant')
    original=json.loads(body,parse_float=number,parse_int=number,parse_constant=invalid,object_pairs_hook=pairs)
    ordinary=json.loads(body)
    from .config import json_text
    resaved=json.loads(json_text(ordinary))
    from .jev import validate_answer
    errors=[];rows=[];checks={};transformed={}
    if duplicates:errors.append('duplicate_json_keys')
    if not isinstance(original,dict) or original.get('model')!=request['model']:errors.append('returned_model')
    answers=original.get('answers',{}) if isinstance(original,dict) else {}
    if not isinstance(answers,dict) or set(answers)!=set(request['questions']):errors.append('answer_keys')
    for qid,q in request['questions'].items():
        try:checks[qid]={'parsed':validate_answer(ordinary['answers'][qid],q),'status':'accepted'}
        except (ValueError,KeyError,TypeError,AttributeError) as e:checks[qid]={'status':'rejected','reason':str(e)}
        try:transformed[qid]={'parsed':validate_answer(resaved['answers'][qid],q),'status':'accepted'}
        except (ValueError,KeyError,TypeError,AttributeError) as e:transformed[qid]={'status':'rejected','reason':str(e)}
        if q['type']!='score':
            if checks[qid]['status']!='accepted':errors.append('choice_schema')
            continue
        row={'question_id':qid,'errors':[]}
        try:
            a=answers[qid];legend={str(i):v for i,v in enumerate(q['criteria'])}
            if a.get('type')!='score' or len(legend)!=5 or a.get('legend')!=legend:raise ValueError('score_legend_or_type')
            p=a['probabilities'];score=a['score'];confidence=a['confidence']
            if set(p)!=set(legend):raise ValueError('probability_keys')
            if any(not isinstance(v,Decimal) or not v.is_finite() for v in [score,confidence,*p.values()]):raise ValueError('numeric_type_or_nonfinite')
            if not 0<=score<=4 or not 0<=confidence<=1 or any(not 0<=v<=1 for v in p.values()):raise ValueError('numeric_range')
            with localcontext() as ctx:
                ctx.prec=80;total=sum(p.values());expected=sum(Decimal(k)*v for k,v in p.items())
                row.update(returned_score=score,probabilities=p,probability_sum=total,expected_raw=expected,
                    delta_raw=score-expected,abs_delta_raw=abs(score-expected),confidence=confidence)
                if abs(total-1)>Decimal('1e-6'):raise ValueError('probability_sum')
                norm=expected/total
                row.update(expected_normalized=norm,delta_normalized=score-norm,
                    current_contract_consistent=abs(score-norm)<=Decimal('1e-6'),normalization_applied=total!=1)
        except (ValueError,KeyError,TypeError,AttributeError) as e:row['errors'].append(str(e));errors.append('score_schema')
        rows.append(row)
    if errors:classification='SCHEMA_OR_JSON_ERROR'
    elif any(not r['current_contract_consistent'] for r in rows):classification='RAW_NUMERIC_MISMATCH_OBSERVED'
    elif any(v['status']=='rejected' for v in checks.values()) or checks!=transformed:classification='APP_TRANSFORM_MISMATCH'
    else:classification='NO_MISMATCH_IN_BOUNDED_SAMPLE'
    return {'classification':classification,'errors':errors,'numeric_lexemes':tokens,'duplicate_keys':duplicates,
        'scores':rows,'normal_json_validation':checks,'reserialized_validation':transformed,
        'normal_equals_reserialized':ordinary==resaved,'returned_model':ordinary.get('model') if isinstance(ordinary,dict) else None}
