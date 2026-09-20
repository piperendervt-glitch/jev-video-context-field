import copy
import json
import time
from .clock import MediaClock, root_key
from .ledger import Ledger
from .fields import FieldReducer, Hypothesis
from .jev import EvidenceEvaluator, DominanceReader, mock_response
from .dispatcher import Dispatcher, MockTransport, ResultDiscarded
from .transcript import TranscriptStore
from .triage import build_triage
from .inspection import ContextReader, LocalAdmission
from .observers import REGISTRY
from .score_policy import C0, policy_record
from .dispatch_policy import P1
from .parallel_dispatcher import ParallelDispatcher

class Session:
    def __init__(self, session_id="demo", mode="MOCK", path=None, adoption_policy_id=C0, dispatch_policy=P1):
        if mode not in {"MOCK", "LOCAL"}:
            raise ValueError("live_disabled")
        self.clock = MediaClock()
        self.dispatch_policy=dispatch_policy
        self.lifecycle_cursor=0
        self.adoption_policy_id = policy_record(adoption_policy_id)['adoption_policy_id']
        self.ledger = Ledger(session_id, "synthetic" if mode == "MOCK" else "selected-local", path=path)
        self.fields = FieldReducer(self.ledger)
        self.evidence = EvidenceEvaluator(self.ledger)
        self.dominance = DominanceReader(self.ledger, self.adoption_policy_id)
        self.dispatcher = Dispatcher(MockTransport(mock_response))
        self.admission = LocalAdmission()
        self.reader = ContextReader(self.ledger, self.admission)
        self.mode, self.selected_subject = mode, None
        self.fixture_seen = set()
        self.last_read_s, self.last_display_s = -2, -1
        self.evidence_cursor = 0
        self.history = []
        self.local_observations = {}
        self.reader_enabled = True
        self.stopped = False
        self.last_payload = None
        self.fixture_cut = False
        self.local_enabled = False
        self.local_pipeline = None
        self.media_info = None
        self.evaluation_mode = 'MOCK'
        self.remote_pool = None
        self.campaign_budget = None
        self.transcripts = TranscriptStore(self.ledger)
        self.candidate_links = {}
        self.dispatch_log_cursor=0

    def seek(self, target):
        self.dispatcher.stop()
        self.clock.seek(target)
        self.ledger.reset(self.clock.epoch)
        self.fields = FieldReducer(self.ledger)
        self.dominance = DominanceReader(self.ledger, self.adoption_policy_id)
        if isinstance(self.dispatcher,ParallelDispatcher):
            self.dispatcher.close()
            self.dispatcher=ParallelDispatcher(self.dispatcher.transport,self.dispatcher.budget,self.dispatch_policy)
            self.dispatcher.before_collect=self.flush_lifecycle
            self.lifecycle_cursor=0
        else:self.dispatcher = Dispatcher(self.dispatcher.transport, self.dispatcher.budget)
        self.reader = ContextReader(self.ledger, LocalAdmission())
        self.admission = self.reader.admission
        self.fixture_seen.clear()
        self.last_read_s = target-2
        self.fixture_cut = target >= 12
        self.stopped = False
        self.local_observations.clear()
        self.candidate_links.clear()
        self.dispatch_log_cursor=0

    def observation(self, stamp, space, value, text, *, obs_id=None, dependency=None):
        if self.mode != "MOCK":
            raise ValueError("synthetic_observation_forbidden_in_local")
        epoch, shot = self.clock.epoch, self.fields.shot
        modality = "audio" if space == "conversation" else "video"
        scope = "session" if space == "conversation" else shot
        subject = "conversation-stream01" if space == "conversation" else f"{shot}-face01" if space == "person" else f"scene-{shot}"
        facet = {"conversation": "conversation.register", "person": "person.face_expression", "place": "place.apparent_time_of_day"}[space]
        mode = "quoted_speech" if space == "conversation" else "depicted"
        labels = {"casual": "日常の話題", "business": "業務の話題", "smile": "笑顔に見える", "angry": "怒って見える", "day": "昼光の手掛かり", "night": "夜の照明の手掛かり"}
        h = Hypothesis(f"{epoch}:{scope}:{space}:{value}", space, scope, subject, facet, value, mode, labels[value])
        hid = self.fields.register(h)
        if hid is None:
            return None, None
        h = self.fields.hypotheses[hid]
        root = root_key(self.ledger.media_id, epoch, modality, "01", stamp)
        oid = obs_id or f"obs:{epoch}:{space}:{stamp}:{value}"
        obs = {"id": oid, "kind": "observation", "revision": 1, "epoch": epoch,
               "dependencies": dependency or {}, "scope": scope, "subject": subject,
               "reference_mode": mode, "facet": facet,
               "agent": {"person": "FaceTrack", "place": "FrameInterpreter", "conversation": "TextContext"}[space],
               "text": text, "basis": "synthetic_fixture", "model": "scripted-fixture-v1",
               "input_roots": [root], "evidence_roots": [root], "citations": {root: stamp},
               "input_cutoff_s": self.clock.media_s,
               "root_sources": {root: {"interval": [stamp, stamp], "modality": modality, "stream": "01",
                                       "pts": round(stamp*1000), "time_base": [1,1000], "pts_origin": "synthetic_media"}}}
        self.ledger.add(obs, self.clock)
        return obs, h

    def enqueue_evidence(self, obs, h, wall_s, relation=None, job=None):
        self.candidate_links.setdefault(obs['id'],[]).append(h.id)
        self.ledger.event('candidate_link',{'observation_id':obs['id'],'hypothesis_id':h.id})
        request = self.evidence.build([obs], [h])
        series = f"{obs['id']}:{h.id}"
        generation = self.evidence.generation(series)
        if relation:
            # Fixture transport records explicit alternative contradiction, no live branch.
            raw_factory = lambda req: mock_response(req, relation)
        else:
            raw_factory = mock_response
        def done(raw, accepted, completed):
            if obs['epoch']!=self.clock.epoch or obs['id'] not in self.ledger.active or self.clock.media_s-max(obs['citations'].values())>8:
                raise ResultDiscarded('superseded_or_stale_evidence')
            if relation:
                raw = raw_factory(request)
            evaluated = self.evidence.accept(request, raw, [h], generation, series, accepted, completed, self.clock.media_s, mode=self.evaluation_mode)
            if self.evaluation_mode=='LIVE-JEV' and any(ev is None for ev in evaluated):raise ValueError('live_evaluation_rejected')
            for ev in evaluated:
                if ev:
                    try:
                        contribution = self.fields.contribute(ev["id"], h.id, self.clock.media_s, completed)
                        self.evidence_cursor = len(self.ledger.events)
                        if job:
                            self.ledger.event("inspection_completed", {"job_id": job["id"], "observation_id": obs["id"],
                                                                      "evaluation_id": ev["id"], "contribution_id": contribution["id"],
                                                                      "new_roots": job.get('new_roots',0), "mode": self.evaluation_mode})
                    except ValueError as error:
                        self.ledger.event("contribution_rejected", {"reason": str(error), "evaluation_id": ev["id"]})
        if isinstance(self.dispatcher,ParallelDispatcher):
            self.dispatcher.submit(request,wall_s,series,self.guarded(done),context={'epoch':self.clock.epoch,'dependencies':{obs['id']:obs['revision']},
                'cutoff':obs['input_cutoff_s'],'authorization_scope':'selected-local-session','policy':self.adoption_policy_id})
            self.flush_lifecycle()
        else:self.dispatcher.submit(request, wall_s, series, self.guarded(done))
        self.ledger.event("evaluation_requested", {"role": "evidence", "request": request.payload_json, "unit_ids": list(request.unit_ids), "mode": self.evaluation_mode})

    def enable_live(self,capability,*,capture_directory=None,work_id=None,budget=None):
        if self.mode!='LOCAL' or not self.local_pipeline or self.clock.media_s!=self.local_pipeline.start or self.ledger.records:
            raise ValueError('live_requires_fresh_selected_local_session')
        transport=capability.transport(capture_directory=capture_directory,work_id=work_id) if capture_directory is not None else capability.transport()
        transport.strict_score_json=self.adoption_policy_id=='cd-distribution-display-v1'
        self.dispatcher.stop()
        self.dispatcher=ParallelDispatcher(transport,budget or capability.budget,self.dispatch_policy)
        self.dispatcher.before_collect=self.flush_lifecycle
        self.evaluation_mode='LIVE-JEV';self.media_info['evaluation_mode']='LIVE-JEV'
        self.remote_pool=None
        self.remote_jobs=[]
        self.ledger.event('adoption_policy',self.dominance.policy)
        self.ledger.event('dispatch_policy',self.dispatch_policy.public())

    def dispatch(self,wall):
        if isinstance(self.dispatcher,ParallelDispatcher):
            self.flush_lifecycle();self.dispatcher.pump();self.flush_lifecycle();return
        if not self.remote_pool:return self.dispatcher.run_one(wall)
        self.remote_jobs=[f for f in self.remote_jobs if not f.done()]
        if len(self.remote_jobs)<2:
            self.remote_jobs.append(self.remote_pool.submit(self.dispatcher.run_one,time.monotonic(),time.monotonic))

    def guarded(self,callback):
        def apply(*args):
            with self.ledger.lock:
                if self.stopped:raise ResultDiscarded('session_stopped')
                return callback(*args)
        return apply

    def fixture(self, wall_s):
        t = self.clock.media_s
        # Fixed synthetic story. Silence 20-32 s deliberately demonstrates stale + evaporation.
        schedule = [(1, "place", "day"), (2, "person", "smile"), (3, "conversation", "casual"),
                    (5, "conversation", "business"), (7, "place", "day"), (9, "person", "smile"),
                    (11, "conversation", "business"), (13, "place", "night"), (14, "person", "angry"),
                    (16, "conversation", "casual"), (18, "place", "night")]
        if t >= 12.4 and not self.fixture_cut and self.clock.epoch_start_media_s < 12:
            self.fields.cut(12, t)
            self.fixture_cut = True
        for stamp, space, value in schedule:
            if stamp in self.fixture_seen or stamp < self.clock.epoch_start_media_s or stamp > t:
                continue
            modality = "audio" if space == "conversation" else "video"
            if not self.clock.permits(stamp, stamp, modality, self.clock.epoch):
                continue
            self.fixture_seen.add(stamp)
            obs, h = self.observation(stamp, space, value, f"合成fixture: {stamp}秒の{space}/{value}。実モデルの観測結果ではありません。")
            if obs:
                self.admission.admit(wall_s)
                relation = {"support": .05, "contradict": .8, "insufficient": .15} if stamp == 7 else None
                self.enqueue_evidence(obs, h, wall_s, relation)

    def tick(self, message, wall_s=None):
        wall_s = time.monotonic() if wall_s is None else wall_s
        with self.ledger.lock:
            if self.stopped:
                return self.view(record=False)
            accepted = self.clock.notify(wall_s=wall_s, **message)
            if not accepted:
                return self.view(record=False)
            self.ledger.event('player_release',{'clock_seq':self.clock.seq,'state':self.clock.state,
                'released':self.clock.released,'received_wall_s':wall_s},self.clock.media_s)
            if self.mode == "MOCK" and self.clock.state == "playing":
                self.fixture(wall_s)
            if self.local_pipeline is not None:
                self.local_pipeline.pump(wall_s)
            self.dispatch(wall_s)
            snapshot = self.fields.snapshot(self.clock.media_s)
            if ((self.mode == "MOCK" and self.clock.media_s < 20) or self.local_enabled) and not self.stopped and not self.dispatcher.stopped and self.dominance.opportunity(self.clock.media_s, self.clock.state != "playing", self.evidence_cursor):
                parallel=isinstance(self.dispatcher,ParallelDispatcher)
                built = self.dominance.build(snapshot, self.selected_subject,self.dispatcher.busy_units(),dispatch_policy=self.dispatch_policy if parallel else None)
                requests=built if parallel else [built] if built else []
                for request in requests or []:
                    def done(raw, accepted, completed, snap=snapshot, req=request):
                        try:outputs=self.dominance.accept(req, raw, snap, self.clock, self.fields.scope_revision, accepted, completed,mode=self.evaluation_mode)
                        except ValueError as error:
                            if str(error) in {'old_epoch_or_scope','expired_readout','superseded_dependency'}:
                                self.ledger.event('readout_rejected',{'reason':str(error),'input_snapshot_id':snap.data()['snapshot_id']})
                                raise ResultDiscarded(str(error)) from error
                            raise
                        if self.evaluation_mode=='LIVE-JEV' and any(r['status']=='error' for r in outputs):raise ValueError('live_readout_schema')
                    try:
                        if parallel:
                            self.dispatcher.submit(request,wall_s,'facets:'+','.join(request.unit_ids),self.guarded(done),
                                context={'epoch':self.clock.epoch,'snapshot_id':snapshot.data()['snapshot_id'],'dependencies':snapshot.data()['dependency_versions'],
                                    'cutoff':self.clock.media_s,'authorization_scope':'selected-local-session','policy':self.adoption_policy_id})
                            self.flush_lifecycle()
                        else:self.dispatcher.submit(request, wall_s, "initial-facets", self.guarded(done))
                        self.ledger.event("evaluation_requested", {"role": "dominance", "request": request.payload_json, "mode": self.evaluation_mode,
                            'unit_ids':list(request.unit_ids),'policy_binding':json.loads(request.policy_json) if request.policy_json else None})
                    except ValueError as error:
                        self.ledger.event('readout_deferred',{'reason':str(error),'unit_ids':list(request.unit_ids)})
            if self.mode == "MOCK" and self.reader_enabled and self.clock.media_s < 20:
                job = self.reader.propose(snapshot, self.clock, wall_s)
                if job:
                    old = self.ledger.records[job["source_observation"]]
                    # A new interpretation keeps the exact same physical roots and times.
                    stamp = max(old["citations"].values())
                    value = "day" if stamp < 12 else "night"
                    obs, h = self.observation(stamp, "place", value, "合成の追加確認。元frameと同一rootを参照。", obs_id=f"obs:{job['id']}")
                    if obs:
                        self.enqueue_evidence(obs, h, wall_s, job=job)
            self.dispatch(wall_s)
            return self.view()

    def view(self, record=True):
        self.flush_lifecycle()
        for event in self.dispatcher.log[self.dispatch_log_cursor:]:self.ledger.event('dispatcher_result',event,self.clock.media_s)
        self.dispatch_log_cursor=len(self.dispatcher.log)
        snapshot = self.fields.snapshot(self.clock.media_s).data()
        snapshot['registry_revision']=self.dominance.registry.revision
        readouts = self.dominance.display(self.clock, self.fields.scope_revision, self.selected_subject)
        if self.mode == "LOCAL" and not self.local_enabled:
            for readout in readouts.values():
                readout.update(status="disabled", reason="semantic_models_and_live_jev_disabled")
        # Raw responses stay in the inspection record, not repeated in every 10 Hz frame.
        for readout in readouts.values():
            readout.pop("raw", None)
            readout.pop("parsed", None)
        summary = {"media_s": self.clock.media_s, "unknown": {s:v["UNKNOWN"] for s,v in snapshot["spaces"].items()}, "epoch": self.clock.epoch}
        if record and (not self.history or self.clock.media_s != self.history[-1]["media_s"] or self.clock.epoch != self.history[-1]["epoch"]):
            self.history.append(summary)
            self.history[:] = self.history[-1200:]
        payload = {"snapshot": snapshot, "readouts": readouts, "clock": {"epoch": self.clock.epoch, "media_s": self.clock.media_s,
                   "state": self.clock.state, "seq":self.clock.seq, "released": self.clock.released}, "mode": self.mode,
                   "external": ((self.campaign_budget or self.dispatcher.budget).summary()|{'usage':self.dispatcher.usage}) if self.evaluation_mode=='LIVE-JEV' or self.campaign_budget else {"attempts": 0, "units": 0, "usage": None}, "mock": self.dispatcher.budget.summary() if self.evaluation_mode=='MOCK' else {'attempts':0,'units':0},
                   "evaluation_status": {'mode':self.evaluation_mode,'stopped':self.dispatcher.stopped,'last_events':self.dispatcher.log[-3:]},
                   "queues": {k:len(v) for k,v in self.dispatcher.queues.items()}, "adapters": REGISTRY,
                   "local_observations": copy.deepcopy(self.local_observations), "history": self.history[-300:],
                   "events": [{k:e[k] for k in ("event_seq", "kind", "media_s", "payload")} for e in self.ledger.events[-12:] if e["kind"] not in {"display", "evaluation_requested"}],
                   "feedback": {"enabled": self.reader_enabled, "completed": len({e['payload']['job_id'] for e in self.ledger.events if e['kind']=='inspection_completed'}),
                       'accepted':sum(r['kind']=='inspection_job' and r['status']=='accepted' for r in self.ledger.records.values()),
                       'rejected':sum(r['kind']=='inspection_job' and r['status']=='rejected' for r in self.ledger.records.values())},
                   "status": "stopped" if self.stopped else "ready"}
        if self.media_info:
            payload["media"] = self.media_info
        pipe=self.local_pipeline
        pending=bool(pipe and ({'SpeechASR','TextContext'} & (pipe.pending.keys()|pipe.waiting.keys())))
        asr_errors=[e for e in (pipe.errors if pipe else []) if e['agent']=='SpeechASR']
        transcripts=self.transcripts.public(self.clock.epoch,has_audio=bool(not self.media_info or self.media_info.get('has_audio')),pending=pending,
                                            error=asr_errors[-1]['error'] if asr_errors else None)
        payload['transcripts']=transcripts
        payload['profiles']=self.dominance.registry.public()
        payload['unknown_triage']=build_triage(self,snapshot,readouts,transcripts,payload['profiles'])
        payload['display_event_cursor']=len(self.ledger.events)+1 if record else None
        if self.local_pipeline:
            payload["local_pipeline"] = self.local_pipeline.public()
            payload["evaluation_label"] = "実動画・実ローカル解析／"+self.evaluation_mode
            if self.evaluation_mode=='MOCK':payload['evaluation_label']='実動画・実ローカル解析／評価のみMOCK'
            payload["adapters"] = copy.deepcopy(REGISTRY)
            for agent in ("SpeechASR", "FrameInterpreter", "TextContext"):
                payload["adapters"][agent] = {"enabled": self.local_pipeline.models.status == "ready",
                    "reason": self.local_pipeline.models.status, "model": self.local_pipeline.models.public()["models"][agent]}
            payload["analysis_status"] = getattr(self, "analysis_status", "active")
        if record:
            self.ledger.event("display", payload, self.clock.media_s)
        self.last_payload = payload
        return payload

    def stop(self):
        self.stopped = True
        if self.local_pipeline:self.local_pipeline.cancel('session_stopped')
        self.dispatcher.stop()
        self.clock.state = "stopped"
        self.ledger.event("stop", {})
        if self.remote_pool:self.remote_pool.shutdown(wait=False,cancel_futures=True)
        if isinstance(self.dispatcher,ParallelDispatcher):self.dispatcher.pool.shutdown(wait=False,cancel_futures=False)

    def close_remote(self):
        if isinstance(self.dispatcher,ParallelDispatcher):
            self.dispatcher.close();self.flush_lifecycle()

    def flush_lifecycle(self):
        if isinstance(self.dispatcher,ParallelDispatcher):
            with self.dispatcher.lock:events=copy.deepcopy(self.dispatcher.events[self.lifecycle_cursor:]);self.lifecycle_cursor=len(self.dispatcher.events)
            for e in events:self.ledger.event('jev_lifecycle',e,self.clock.media_s)

    def begin_drain(self,reason):
        # Stop admissions, retain already accepted callbacks and their original deadlines.
        self.dispatcher.stop()
        if self.local_pipeline:self.local_pipeline.begin_drain(reason)
        self.clock.state='paused'
        self.ledger.event('evaluation_end',{'reason':reason},self.clock.media_s)
