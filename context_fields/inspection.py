"""Read-only ContextReader and local-model admission accounting."""
import math
from collections import deque

class LocalAdmission:
    def __init__(self):
        self.accepted = deque()
        self.seen = set()

    def admit(self, now, *, reader=False, identity=None):
        while self.accepted and self.accepted[0][0] <= now-10:
            self.accepted.popleft()
        if reader:
            if identity in self.seen:
                return False, "duplicate_root_question_version"
            reader_count = sum(r for _,r in self.accepted)
            if reader_count+1 > math.floor(.2*(len(self.accepted)+1)):
                return False, "basic_observation_reserved"
            self.seen.add(identity)
        self.accepted.append((now, reader))
        return True, None

    def reader_capacity(self,now):
        recent=[r for stamp,r in self.accepted if stamp>now-10]
        return sum(recent)+1<=math.floor(.2*(len(recent)+1))

class ContextReader:
    def __init__(self, ledger, admission):
        self.ledger, self.admission = ledger, admission
        self.last_s = -5
        self.count = 0

    def propose(self, snapshot, clock, wall_s, target_frame=None):
        snap = snapshot.data()
        if clock.state != "playing" or clock.media_s-self.last_s < 5:
            return None
        self.last_s = clock.media_s
        # It reads field conflicts/UNKNOWN, never a DominanceReadout.
        candidates = []
        for oid in sorted(self.ledger.active):
            obs = self.ledger.records[oid]
            if obs["kind"] == "observation" and obs.get("agent") == "FrameInterpreter" and obs["scope"] == snap["shot"]:
                candidates.append(obs)
        if not candidates:
            return None
        obs = max(candidates, key=lambda o: max(o["citations"].values()))
        for root in obs["evidence_roots"]:
            src = obs["root_sources"][root]
            if not clock.permits(*src["interval"], src["modality"], obs["epoch"]):
                return None
        sources=obs['root_sources']
        if target_frame is not None:
            if not clock.permits(target_frame['media_s'],target_frame['media_s'],'video',clock.epoch):return None
            from .clock import root_key
            root=root_key(self.ledger.media_id,clock.epoch,'video',target_frame['stream'],target_frame['media_s'])
            sources={root:{k:target_frame[k] for k in ('frame_id','pts','time_base','media_origin_s','stream')}}
            sources[root].update(interval=[target_frame['media_s'],target_frame['media_s']],modality='video')
        question = "公開済みframeの明るさと光源の手掛かりだけを記述してください。"
        identity = (tuple(sorted(sources)), question, obs["revision"])
        admitted, reason = self.admission.admit(wall_s, reader=True, identity=identity)
        self.count += 1
        job = {"id": f"inspection:{clock.epoch}:{self.count}", "kind": "inspection_job", "revision": 1,
               "epoch": clock.epoch, "dependencies": {}, "input_cursor": snap["input_cursor"],
               "snapshot_id": snap["snapshot_id"], "source_observation": obs["id"],
               "source_roots": sorted(sources), "question": question, "roi": None,
               "job_type": 'latest_published_lighting' if target_frame else 'reinspect_original',
               "target_sources":sources,"target_media_s":target_frame['media_s'] if target_frame else max(obs['citations'].values()),
               "new_roots":len(set(sources)-set(obs['evidence_roots'])),
               "deadline_wall_s": wall_s+5, "status": "accepted" if admitted else "rejected", "reason": reason,
               "trigger": {"unknown": snap["spaces"]["place"]["UNKNOWN"],
                           "conflict": any(r["conflict"] for r in snap["spaces"]["place"]["hypotheses"])},
               "no_conclusion_input": True, "budget_lane": "reader"}
        self.ledger.add(job)
        return job if admitted else None
