"""Versioned application controls, not provider capacity guarantees."""
from dataclasses import dataclass,asdict
import hashlib,json,math

@dataclass(frozen=True)
class DispatchPolicy:
    id:str='p1-provisional-v1'
    concurrency:int=4
    rps:float=4
    burst:int=4
    evidence_rps:float|None=None
    dominance_rps:float|None=None
    queue_requests:int=64
    queue_bytes:int=768000
    request_units:int=3
    request_questions:int=12
    request_chars:int=12000
    batch:bool=True
    dynamic_units:int|None=None
    def __post_init__(self):
        for k in ('concurrency','burst','queue_requests','queue_bytes','request_units','request_questions','request_chars'):
            if type(getattr(self,k)) is not int or getattr(self,k)<1:raise ValueError('invalid_dispatch_policy:'+k)
        for v in (self.rps,self.evidence_rps,self.dominance_rps):
            if v is not None and (type(v) not in (int,float) or not math.isfinite(v) or v<=0):raise ValueError('invalid_rate')
        if self.dynamic_units is not None and (type(self.dynamic_units) is not int or self.dynamic_units<1):raise ValueError('invalid_dynamic_capacity')
    def public(self):
        d=asdict(self);d['sha256']=hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest()
        return d

LEGACY=DispatchPolicy(id='legacy-v03-comparison',concurrency=2,rps=2,burst=2,evidence_rps=1,dominance_rps=.5,queue_requests=16,dynamic_units=2)
P1=DispatchPolicy()

def load_policy(path):
    from pathlib import Path
    return DispatchPolicy(**json.loads(Path(path).read_text(encoding='utf8')))
