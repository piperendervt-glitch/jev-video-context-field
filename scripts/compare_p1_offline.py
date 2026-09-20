"""Same workload, actual P1 dispatcher, delayed in-process transport (no network)."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.offline_guard import install,COUNTS
install()
import json,time,math,threading,hashlib
from dataclasses import replace
from context_fields.parallel_dispatcher import ParallelDispatcher
from context_fields.dispatch_policy import P1,LEGACY
from context_fields.question_batch import WireRequest
from context_fields.dispatcher import MockTransport
from context_fields.config import json_text
OUT=ROOT/'artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1'

def percentile(values,p):
    values=sorted(values);return values[max(0,math.ceil(len(values)*p)-1)] if values else None

def workload():
    result=[]
    for i in range(12):
        state={'unit0':{'observation_id':'fixture-'+str(i),'quoted_data':'synthetic benchmark only','version':1}}
        role='evidence' if i%2==0 else 'dominance'
        result.append(WireRequest(json_text({'model':'jev-1.13.0','state':state if role=='evidence' else {'units':state},
            'questions':{f'unit0.q{q}':{'type':'noul','instructions':f'Evaluate state unit0 independent fixture criterion {q}'} for q in range(3)}}),
            ('fixture-'+str(i),) if role=='evidence' else ('unit0',),role))
    return result

def main():
    cases=[('legacy_split',replace(LEGACY,request_questions=1,batch=False)),
        ('concurrency_only',replace(LEGACY,id='offline-concurrency-only',concurrency=8,request_questions=1,batch=False)),
        ('batch_only',LEGACY),('combined',replace(P1,rps=32,burst=32))]
    cases += [('parallel_'+str(n),replace(P1,id='offline-'+str(n),concurrency=n,rps=64,burst=64,request_questions=1,batch=False)) for n in (1,2,4,8,16,32)]
    report=[]
    for name,policy in cases:
        active=0;peak=0;lock=threading.Lock();starts=[];finishes=[];completed=[]
        def fake(r):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active);starts.append(time.monotonic())
            state=json.loads(r.payload_json)['state'];unit=state['unit0'] if r.role=='evidence' else state['units']['unit0']
            i=int(unit['observation_id'].split('-')[-1]);time.sleep(.04+(i%4)*.02)
            with lock:active-=1;finishes.append(time.monotonic())
            return {'model':'jev-1.13.0','answers':{q:{'type':'noul','noul':.5} for q in json.loads(r.payload_json)['questions']}}
        import psutil
        cpu=time.process_time();rss=psutil.Process().memory_info().rss
        d=ParallelDispatcher(MockTransport(fake),policy=policy);start=time.monotonic();items=workload()
        for i,r in enumerate(items):d.submit(r,start,str(i),lambda *a,i=i:completed.append(i),context={'epoch':1,'cutoff':1,'dependencies':{'fixture':1},'authorization_scope':'offline-only'})
        try:
            while time.monotonic()-start<6:
                d.pump()
                if not d.workers and not any(d.queues.values()):d.drain();break
                time.sleep(.002)
            waits=[x['queue_wait_s'] for x in d.log];durations=[x['body_received_wall_s']-x['sent_wall_s'] for x in d.log]
            rate=max((sum(t<=s<t+1 for s in starts) for t in starts),default=0)
            row={'condition':name,'policy':policy.public(),'demand_units':12,'demand_questions':36,'candidate_count':12,
                'workload_sha256':hashlib.sha256(json_text([r.payload_json for r in items]).encode()).hexdigest(),
                'elapsed_s':time.monotonic()-start,'requests':d.budget.attempts,'charged_units':d.budget.units,'sent_questions':d.budget.questions,
                'logical_completed_before_deadline':len(completed),'current_display_eligible':None,'display_reason':'offline transport workload; publication gates tested separately',
                'actual_fake_io_peak':peak,'starts_max_rolling_second':rate,'samples':len(waits),'quantile_method':'nearest rank ceil(n*p), small simulated sample',
                'queue_p50':percentile(waits,.5),'queue_p95':percentile(waits,.95),'body_p50':percentile(durations,.5),'body_p95':percentile(durations,.95),
                'chars':d.budget.chars,'deadline_s':5,'retry':0,'events':d.events,'requests_log':d.log,'real_network':False,
                'completed_by_role':{role:sum(items[i].role==role for i in completed) for role in ('evidence','dominance')},
                'cpu_seconds':time.process_time()-cpu,'rss_start':rss,'rss_end':psutil.Process().memory_info().rss}
            report.append(row);print(name,'requests',row['requests'],'completed',len(completed),'peak',peak,'wait95',row['queue_p95'],flush=True)
        finally:d.close()
    (OUT/'offline-comparison.json').write_text(json.dumps({'cases':report,'guard':COUNTS},ensure_ascii=False,indent=2),encoding='utf8')

if __name__=='__main__':main()
