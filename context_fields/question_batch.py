"""Exact immutable-state packing. Never merges roles, versions or causal inputs."""
import hashlib,json
from dataclasses import dataclass
from .config import json_text

@dataclass(frozen=True)
class WireRequest:
    payload_json:str
    unit_ids:tuple
    role:str
    policy_json:str|None=None

def payload_units(request,payload,questions):
    if request.role=='evidence':
        names=list(payload['state'])
        mapping=dict(zip(names,request.unit_ids,strict=True))
    else:mapping={u:u for u in request.unit_ids}
    return tuple(dict.fromkeys(mapping[q.split('.')[0]] for q in questions))

def split(request,policy):
    p=json.loads(request.payload_json);parts=[];group={}
    if len(p['questions'])<=policy.request_questions and len(request.unit_ids)<=policy.request_units and len(request.payload_json)<=policy.request_chars:
        return [WireRequest(request.payload_json,request.unit_ids,request.role,getattr(request,'policy_json',None))]
    for qid,q in p['questions'].items():
        trial={**group,qid:q};units=payload_units(request,p,trial)
        text=json_text({**p,'questions':trial})
        if group and (len(trial)>policy.request_questions or len(units)>policy.request_units or len(text)>policy.request_chars):
            parts.append(WireRequest(json_text({**p,'questions':group}),payload_units(request,p,group),request.role,getattr(request,'policy_json',None)));group={qid:q}
        else:group=trial
        text=json_text({**p,'questions':group})
        if len(text)>policy.request_chars:raise ValueError('single_question_request_chars')
    if group:parts.append(WireRequest(json_text({**p,'questions':group}),payload_units(request,p,group),request.role,getattr(request,'policy_json',None)))
    return parts

def compatibility(request,context):
    p=json.loads(request.payload_json)
    return json_text({'state':p['state'],'model':p['model'],'role':request.role,'policy':getattr(request,'policy_json',None),'context':context})

def merge(requests,policy):
    first=requests[0];p=json.loads(first.payload_json);questions={};units=[]
    for r in requests:
        q=json.loads(r.payload_json)
        if (q['state'],q['model'],r.role,getattr(r,'policy_json',None))!=(p['state'],p['model'],first.role,getattr(first,'policy_json',None)):raise ValueError('batch_input_mismatch')
        for key,value in q['questions'].items():
            if key in questions and questions[key]!=value:raise ValueError('batch_question_collision')
            questions[key]=value
        units.extend(u for u in r.unit_ids if u not in units)
    text=json_text({**p,'questions':questions})
    if len(units)>policy.request_units or len(questions)>policy.request_questions or len(text)>policy.request_chars:raise ValueError('batch_size')
    return WireRequest(text,tuple(units),first.role,getattr(first,'policy_json',None))
