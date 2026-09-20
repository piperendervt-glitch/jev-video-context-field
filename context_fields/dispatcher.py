"""One atomic budget, role limits and zero-retry HTTP transport.

Live requires an explicit backend capability. Raw capture remains disabled unless
that capability's shared transport factory receives a private capture directory.
"""
import copy
import hashlib
import http.client
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from .config import CONFIG

class ResultDiscarded(Exception):
    """A valid request became obsolete locally; this is not a service failure."""

def unique_json_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate_json_key')
        result[key]=value
    return result

class Budget:
    def __init__(self, attempts=240, units=720, chars=2880000, request_limits=(3,12,12000)):
        self.limits = (attempts, units, chars)
        self.request_limits=request_limits
        self.attempts = self.units = self.questions = self.chars = self.bytes = 0
        self.lock = threading.RLock()
        self.reservations = []

    def reserve(self, request):
        payload = json.loads(request.payload_json)
        n, q, c = len(request.unit_ids), len(payload["questions"]), len(request.payload_json)
        if not 1 <= n <= self.request_limits[0] or not 1 <= q <= self.request_limits[1] or c > self.request_limits[2]:
            raise ValueError("request_limit")
        if request.role not in {"evidence", "dominance"}:
            raise ValueError("invalid_role")
        with self.lock:
            if self.attempts+1 > self.limits[0] or self.units+n > self.limits[1] or self.chars+c > self.limits[2]:
                raise ValueError("shared_budget_exhausted")
            self.attempts += 1
            self.units += n
            self.questions += q
            self.chars += c
            self.bytes += len(request.payload_json.encode("utf-8"))
            reservation = {"attempt": self.attempts, "role": request.role, "units": n, "questions": q, "chars": c}
            self.reservations.append(reservation)
            return reservation

    def summary(self):
        with self.lock:
            return {k: getattr(self,k) for k in ("attempts", "units", "questions", "chars", "bytes")}

class MockTransport:
    retries = 0
    external = False

    def __init__(self, responder):
        self.responder, self.calls = responder, 0

    def send(self, request, timeout):
        self.calls += 1
        return self.responder(request)

class HttpTransport:
    retries = 0
    external = True

    def __init__(self, api_key, *, authorized=False, connection_factory=http.client.HTTPSConnection,
                 response_archive=None):
        if not authorized:
            raise PermissionError("live_not_authorized")
        self._key, self._factory = api_key, connection_factory
        self.response_archive = response_archive
        self._capture_state=threading.local()

    def bind_capture(self,request,reservation,accepted,sent):
        payload=json.loads(request.payload_json)
        self._capture_state.context={'attempt':reservation['attempt'],'unit_ids':list(request.unit_ids),
            'question_ids':list(payload['questions']),'requested_model':payload['model'],
            'accepted_wall_s':accepted,'sent_wall_s':sent}

    @property
    def last_capture(self):return getattr(self._capture_state,'last_capture',None)

    def _captured_body(self,response,request,conn,timeout):
        from .response_archive import allowed_response_headers
        context=getattr(self._capture_state,'context',{})
        deadline=min(time.monotonic()+timeout,context.get('accepted_wall_s',time.monotonic())+5)
        headers,reasons=allowed_response_headers(response)
        started=time.monotonic();chunks=[];size=0;failure=None;complete=False
        try:
            if hasattr(response,'read1'):
                while size<=1024*1024:
                    left=deadline-time.monotonic()
                    if left<=0:raise TimeoutError('request_deadline')
                    sock=getattr(conn,'sock',None)
                    if sock is not None:sock.settimeout(left)
                    chunk=response.read1(min(65536,1024*1024+1-size))
                    if not chunk:complete=True;break
                    chunks.append(chunk);size+=len(chunk)
            else:
                chunk=response.read(1024*1024+1);chunks.append(chunk);size=len(chunk);complete=True
        except http.client.IncompleteRead as error:
            chunks.append(error.partial[:max(0,1024*1024+1-size)]);failure='IncompleteRead'
        except Exception as error:failure=type(error).__name__
        data=b''.join(chunks);truncated=len(data)>1024*1024
        length=headers['content-length']
        if length is not None and (not length.isdecimal() or int(length)!=len(data)):
            complete=False;failure=failure or 'content_length_mismatch'
        if any(v not in (None,'missing') for v in reasons.values()):failure=failure or 'invalid_response_header'
        received=time.monotonic()
        meta=self.response_archive.capture(data,status=response.status,
            request_hash=hashlib.sha256(request.payload_json.encode('utf-8')).hexdigest(),
            truncated=truncated,complete=complete,headers=headers,header_reasons=reasons,context=context,
            timings={'read_started_wall_s':started,'received_wall_s':received},failure=failure)
        self._capture_state.last_capture=meta
        self._capture_state.archive_return_wall_s=time.monotonic()
        if truncated:raise ValueError('response_size')
        if not meta['complete']:raise ValueError('incomplete_capture')
        if time.monotonic()>deadline:raise TimeoutError('request_deadline')
        if headers['content-encoding'] not in (None,'identity'):raise ValueError('unsupported_content_encoding')
        return data

    def send(self, request, timeout):
        # Exactly one request; http.client has no SDK retry/backoff/redirect layer.
        self._capture_state.last_capture=None
        conn = self._factory("api.typesafe.ai", timeout=timeout)
        try:
            conn.request("POST", "/v1/systemone", body=request.payload_json.encode("utf-8"),
                         headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
            response = conn.getresponse()
            data = self._captured_body(response,request,conn,timeout) if self.response_archive is not None else response.read(1024*1024+1)
            if response.status != 200:
                raise ValueError(f"http_{response.status}")
            if len(data) > 1024*1024:
                raise ValueError("response_size")
            return json.loads(data,object_pairs_hook=unique_json_object) if getattr(self,'strict_score_json',False) else json.loads(data)
        finally:
            conn.close()

@dataclass
class Pending:
    request: object
    accepted: float
    series: str
    callback: object

class Dispatcher:
    def __init__(self, transport, budget=None):
        if transport.retries != 0:
            raise ValueError("transport_retry_not_zero")
        self.transport, self.budget = transport, budget or Budget()
        self.queues = {"evidence": deque(), "dominance": deque()}
        self.lock = threading.RLock()
        self.active = 0
        self.last_role = {"evidence": -float("inf"), "dominance": -float("inf")}
        self.tokens, self.token_time = 2.0, None
        self.turn = "evidence"
        self.stopped = False
        self.log = []
        self.usage = None
        self.inflight = {}

    def busy_units(self):
        with self.lock:
            return {u for req in self.inflight.values() for u in req.unit_ids} | {u for q in self.queues.values() for p in q for u in p.request.unit_ids}

    def submit(self, request, now, series, callback):
        with self.lock:
            if self.stopped:
                raise ValueError("dispatcher_stopped")
            queue = self.queues[request.role]
            for old in list(queue):
                if old.series == series:
                    queue.remove(old)
                    self.log.append({"status": "coalesced", "series": series,"accepted_wall_s":old.accepted,"completed_wall_s":now})
            if request.role == "evidence" and len(queue) >= 12:
                raise ValueError("evidence_queue_full")
            if request.role == "dominance" and sum(len(p.request.unit_ids) for p in queue)+len(request.unit_ids) > 4:
                raise ValueError("dominance_queue_full")
            if sum(len(q) for q in self.queues.values()) >= 16:
                raise ValueError("shared_queue_full")
            queue.append(Pending(request, now, series, callback))

    def run_one(self, now, finish_clock=None):
        with self.lock:
            if self.stopped or self.active >= 2:
                return False
            if self.token_time is None:
                self.token_time = now
            self.tokens = min(2, self.tokens+max(0, now-self.token_time)*2)
            self.token_time = now
            selected = None
            roles = [self.turn, "dominance" if self.turn == "evidence" else "evidence"]
            for role in roles:
                queue = self.queues[role]
                while queue and now >= queue[0].accepted + 5:
                    expired = queue.popleft()
                    self.log.append({"status": "expired_before_send", "series": expired.series,"accepted_wall_s":expired.accepted,"completed_wall_s":now})
                interval = 1 if role == "evidence" else 2
                if queue and now-self.last_role[role] >= interval and self.tokens >= 1:
                    selected = queue.popleft()
                    break
            if selected is None:
                return False
            try:
                reservation = self.budget.reserve(selected.request)
            except Exception as error:
                self.stopped = True
                self.log.append({"status": "budget_stop", "reason": str(error) if isinstance(error,ValueError) else type(error).__name__})
                return False
            self.tokens -= 1
            self.last_role[role] = now
            self.active += 1
            self.inflight[reservation['attempt']]=selected.request
            self.turn = "dominance" if role == "evidence" else "evidence"
        entry = {"reservation": reservation, "status": "sent", "external": self.transport.external,
                 "series":selected.series,"unit_ids":list(selected.request.unit_ids),
                 "accepted_wall_s":selected.accepted,"sent_wall_s":now,"queue_wait_s":now-selected.accepted}
        try:
            if isinstance(self.transport,HttpTransport) and self.transport.response_archive is not None:
                self.transport.bind_capture(selected.request,reservation,selected.accepted,now)
            raw = self.transport.send(selected.request, timeout=selected.accepted+5-now)
            if self.transport.external and isinstance(raw.get('usage'),dict):
                values={k:v for k,v in raw['usage'].items() if type(v) is int and v>=0}
                self.usage={k:(self.usage or {}).get(k,0)+v for k,v in values.items()}
            completed = finish_clock() if finish_clock else now
            if completed > selected.accepted + 5:
                raise TimeoutError("request_deadline")
            selected.callback(raw, selected.accepted, completed)
            entry["status"] = "accepted"
        except ResultDiscarded as error:
            entry['status'],entry['reason']='discarded',str(error)
        except Exception as error:
            entry["status"], entry["reason"] = "error", type(error).__name__ + ":" + str(error)
            if self.transport.external or isinstance(error, (ValueError, KeyError, TypeError)):
                self.stopped = True  # Live errors stop all new sends; no retry.
        finally:
            with self.lock:
                self.active -= 1
                self.inflight.pop(reservation['attempt'],None)
                entry['completed_wall_s']=finish_clock() if finish_clock else now
                self.log.append(entry)
        return True

    def stop(self):
        with self.lock:
            self.stopped = True
            self.queues = {"evidence": deque(), "dominance": deque()}
