"""Concurrent I/O, atomic starts, completion-order collection, one state writer.

Only start admission/budget debit holds the gate. No HTTP wait holds it. Workers
never call application callbacks or mutate fields; pump/drain is the sole writer.
"""
import copy,hashlib,json,threading,time,uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue,Empty
from .dispatcher import Budget,HttpTransport,ResultDiscarded
from .dispatch_policy import P1
from .question_batch import split,merge,compatibility
from .score_policy import C0,validate_dominance_answer
from .jev import validate_answer

class BudgetBlocked(ResultDiscarded):pass

class ParallelDispatcher:
    def __init__(self,transport,budget=None,policy=P1,clock=time.monotonic):
        if transport.retries:raise ValueError('transport_retry_not_zero')
        self.transport,self.budget,self.policy,self.clock=transport,budget or Budget(request_limits=(policy.request_units,policy.request_questions,policy.request_chars)),policy,clock
        self.lock=threading.RLock();self.writer_lock=threading.RLock()
        self.pool=ThreadPoolExecutor(max_workers=policy.concurrency,thread_name_prefix='jev-io')
        self.queues={'evidence':deque(),'dominance':deque()};self.turn='evidence'
        self.owners={};self.events=[];self.log=[];self.completed=SimpleQueue();self.inflight={}
        self.active=0;self.workers=0;self.max_active=0;self.stopped=False;self.hard_stopped=False;self.stop_reason=None;self.usage=None
        self.tokens=float(policy.burst);self.token_time=clock();self.last_role={'evidence':-float('inf'),'dominance':-float('inf')}

    def event(self,stage,owner=None,**extra):
        with self.lock:
            p={'stage':stage,'wall_s':self.clock(),'lifecycle_seq':len(self.events)+1,**extra}
            if owner:p.update(logical_id=owner['id'],role=owner['request'].role,unit_ids=list(owner['request'].unit_ids),
                request_hash=owner['hash'],requested_wall_s=owner['accepted'],deadline_wall_s=owner['accepted']+5,context=owner['context'])
            self.events.append(p)

    def submit(self,request,now,series,callback,*,context=None,logical_id=None):
        context=copy.deepcopy(context or {})
        parts=split(request,self.policy);logical_id=logical_id or 'req-'+uuid.uuid4().hex
        with self.lock:
            if logical_id in self.owners:return logical_id
            if self.stopped:raise ValueError('dispatcher_stopped')
            # Coalescing cancels only wholly-unsent same-series input; preserves the
            # earliest deadline. It never merges different immutable states.
            old=[o for o in self.owners.values() if o['series']==series and o['status']=='queued' and o['request'].role==request.role]
            for o in old:
                now=min(now,o['accepted']);self._cancel(o,'coalesced','new_input_version')
            size=sum(len(x['wire'].payload_json.encode()) for q in self.queues.values() for x in q)
            count=sum(map(len,self.queues.values()))
            owner={'id':logical_id,'request':request,'hash':hashlib.sha256(request.payload_json.encode()).hexdigest(),
                'accepted':now,'series':series,'callback':callback,'context':context,'answers':{},'status':'queued','last_received':None}
            self.owners[logical_id]=owner
            self.event('candidate_available',owner,available_wall_s=context.get('candidate_available_wall_s',now))
            self.event('requested',owner,request=request.payload_json,policy=self.policy.public())
            if count+len(parts)>self.policy.queue_requests or size+sum(len(r.payload_json.encode()) for r in parts)>self.policy.queue_bytes:
                self._cancel(owner,'unsent','queue_capacity');return logical_id
            self.event('queued',owner)
            for wire in parts:self.queues[request.role].append({'owner':owner,'wire':wire,'compatibility':compatibility(wire,context)})
            return logical_id

    def _cancel(self,owner,stage,reason):
        if owner['status'] in ('settled','error','cancelled'):return
        owner['status']='cancelled'
        for role,q in self.queues.items():self.queues[role]=deque(x for x in q if x['owner'] is not owner)
        self.event(stage,owner,reason=reason)

    def _stop_locked(self,reason,hard=False):
        if not self.stopped or hard and not self.hard_stopped:
            self.stopped=True;self.stop_reason=reason
            self.hard_stopped=self.hard_stopped or hard
            self.event('global_stop',reason=reason,hard_error=hard)
        remaining={x['owner']['id'] for q in self.queues.values() for x in q}
        for o in list(self.owners.values()):
            if o['status']=='queued' or o['id'] in remaining:
                self._cancel(o,'unsent' if hard or 'budget' in reason else 'cancelled',reason)
        self.queues={'evidence':deque(),'dominance':deque()}

    def stop(self,reason='manual_stop'):
        with self.lock:self._stop_locked(reason)

    def busy_units(self):
        with self.lock:return {u for o in self.owners.values() if o['status'] in ('queued','sent') for u in o['request'].unit_ids}

    def pump(self,now=None):
        self.drain()
        now=self.clock() if now is None else now
        with self.lock:
            self.tokens=min(self.policy.burst,self.tokens+max(0,now-self.token_time)*self.policy.rps);self.token_time=now
            waiting={x['owner']['id'] for q in self.queues.values() for x in q}
            for o in list(self.owners.values()):
                if o['id'] in waiting and now>=o['accepted']+5:self._cancel(o,'expired','deadline_before_send')
            while not self.stopped and self.workers<self.policy.concurrency and self.tokens>=1:
                role=None
                for candidate in (self.turn,'dominance' if self.turn=='evidence' else 'evidence'):
                    rate=getattr(self.policy,candidate+'_rps')
                    if self.queues[candidate] and (rate is None or now-self.last_role[candidate]>=1/rate):role=candidate;break
                if role is None:break
                first=self.queues[role].popleft();group=[first];wire=first['wire']
                if self.policy.batch:
                    for item in list(self.queues[role]):
                        if item['compatibility']!=first['compatibility']:continue
                        try:merged=merge([wire,item['wire']],self.policy)
                        except ValueError:continue
                        group.append(item);wire=merged;self.queues[role].remove(item)
                wire_id='http-'+uuid.uuid4().hex
                for item in group:self.event('batch_finalized',item['owner'],wire_id=wire_id,wire_hash=hashlib.sha256(wire.payload_json.encode()).hexdigest(),question_ids=list(json.loads(item['wire'].payload_json)['questions']))
                self.tokens-=1;self.last_role[role]=now;self.turn='dominance' if role=='evidence' else 'evidence'
                self.workers+=1
                self.pool.submit(self._io,wire_id,wire,group)

    def _io(self,wire_id,wire,group):
        raw=None;error=None;capture=None;entry=None;sent_call=False
        accepted=min(x['owner']['accepted'] for x in group)
        try:
            with self.lock:
                if self.stopped:raise ResultDiscarded('stopped_before_start')
                if self.clock()>=accepted+5:raise ResultDiscarded('deadline_before_start')
                # Atomic debit + send admission linearization. The HTTP call below
                # belongs to this already-started request even if stop follows it.
                try:reservation=self.budget.reserve(wire)
                except ValueError as exc:
                    if str(exc) in ('shared_budget_exhausted','work_budget_exhausted','initial_connection_cap_exhausted'):raise BudgetBlocked(str(exc)) from exc
                    raise
                sent=self.clock();self.active+=1;self.max_active=max(self.max_active,self.active);self.inflight[wire_id]=wire
                entry={'wire_id':wire_id,'reservation':reservation,'unit_ids':list(wire.unit_ids),'role':wire.role,'status':'sent','external':self.transport.external,
                    'accepted_wall_s':accepted,'sent_wall_s':sent,'queue_wait_s':sent-accepted,'request_hash':hashlib.sha256(wire.payload_json.encode()).hexdigest(),
                    'request':wire.payload_json,'logical_ids':list(dict.fromkeys(x['owner']['id'] for x in group)),'active_at_start':self.active}
                for item in group:
                    item['owner']['status']='sent';self.event('reserved',item['owner'],wire_id=wire_id,reservation=reservation)
                    self.event('sent',item['owner'],wire_id=wire_id,wire_hash=entry['request_hash'],active_http=self.active)
            if isinstance(self.transport,HttpTransport):self.transport.bind_capture(wire,reservation,accepted,sent)
            sent_call=True;raw=self.transport.send(wire,timeout=max(.000001,accepted+5-self.clock()))
            capture=self.transport.last_capture if isinstance(self.transport,HttpTransport) else None
            received=self.clock()
            if received>accepted+5:raise TimeoutError('request_deadline')
        except Exception as exc:
            error=exc;received=self.clock()
            if sent_call and isinstance(self.transport,HttpTransport):capture=self.transport.last_capture
            with self.lock:
                if isinstance(exc,BudgetBlocked):self._stop_locked(str(exc),hard=False)
                if not isinstance(exc,ResultDiscarded):self._stop_locked(type(exc).__name__+':'+str(exc),hard=not isinstance(exc,ValueError) or str(exc) not in ('shared_budget_exhausted','work_budget_exhausted','initial_connection_cap_exhausted'))
        with self.lock:
            if entry:
                self.active-=1;self.inflight.pop(wire_id,None)
                entry.update(body_received_wall_s=received,raw_capture=capture,received_after_stop=self.stopped)
            for item in group:self.event('body_received' if error is None else 'io_failed',item['owner'],wire_id=wire_id,body_received_wall_s=received,capture=capture,reason=str(error) if error else None)
            self.workers-=1
            self.completed.put((wire,group,raw,error,entry,received))

    def drain(self):
        # Reentrant so production's ledger guard and this gate form one writer.
        with self.writer_lock:
            while True:
                try:wire,group,raw,error,entry,received=self.completed.get_nowait()
                except Empty:break
                owners={x['owner']['id']:x['owner'] for x in group}
                hook=getattr(self,'before_collect',None)
                if hook:hook()
                start=self.clock()
                try:
                    if error:raise error
                    p=json.loads(wire.payload_json)
                    if not isinstance(raw,dict) or raw.get('model')!=p['model'] and self.transport.external or not isinstance(raw.get('answers'),dict):raise ValueError('response_model_or_envelope')
                    if set(raw['answers'])!=set(p['questions']):raise ValueError('response_question_ids')
                    policy=json.loads(wire.policy_json)['adoption_policy_id'] if wire.policy_json else C0
                    for qid,q in p['questions'].items():
                        if wire.role=='dominance':validate_dominance_answer(raw['answers'][qid],q,policy)
                        else:validate_answer(raw['answers'][qid],q)
                    for item in group:
                        o=item['owner']
                        if o['status'] in ('cancelled','error','settled'):continue
                        for q in json.loads(item['wire'].payload_json)['questions']:o['answers'][q]=raw['answers'][q]
                        o['last_received']=received
                    for o in owners.values():
                        if o['status'] in ('cancelled','error','settled'):continue
                        expected=json.loads(o['request'].payload_json)['questions']
                        if set(o['answers'])!=set(expected):continue
                        if self.clock()>o['accepted']+5:raise TimeoutError('request_deadline_at_writer')
                        self.event('validated',o,wire_id=entry['wire_id'] if entry else None)
                        o['callback']({'model':raw['model'],'answers':copy.deepcopy(o['answers']),'usage':raw.get('usage')},o['accepted'],received)
                        o['status']='settled';self.event('settled',o,wire_id=entry['wire_id'] if entry else None)
                    if entry:entry['status']='accepted'
                except ResultDiscarded as exc:
                    for o in owners.values():self._cancel(o,'unsent' if isinstance(exc,BudgetBlocked) else 'discarded',str(exc))
                    if entry:entry.update(status='discarded',reason=str(exc))
                except Exception as exc:
                    reason=type(exc).__name__+':'+str(exc)
                    with self.lock:
                        self._stop_locked(reason,hard=True)
                        for o in owners.values():
                            if o['status']!='settled':o['status']='error';self.event('error',o,reason=reason,wire_id=entry['wire_id'] if entry else None)
                    if entry:entry.update(status='error',reason=reason)
                finally:
                    if entry:
                        entry.update(validation_started_wall_s=start,completed_wall_s=self.clock());self.log.append(entry)
                        if isinstance((raw or {}).get('usage'),dict):
                            self.usage={k:(self.usage or {}).get(k,0)+v for k,v in raw['usage'].items() if type(v) is int and v>=0}

    def close(self):
        self.stop();self.pool.shutdown(wait=True,cancel_futures=False);self.drain()
