"""Single-writer append-only journal and atomic dependency invalidation."""
import copy
import time
from pathlib import Path
from threading import RLock
from .clock import finite, pts_seconds, root_key
from .config import json_text

FORBIDDEN = {"dominance_readout", "dominance_evaluation", "digest", "inspection_job", "field_snapshot", "viewer_value",
             "unknown_triage", "transcript_view", "jev_profile"}

class Ledger:
    def __init__(self, session="demo", media_id="synthetic", epoch=1, path=None):
        self.session, self.media_id, self.epoch = session, media_id, epoch
        self.records, self.active, self.events = {}, set(), []
        self.lock = RLock()
        self.start = time.monotonic()
        self.path = Path(path) if path else None

    def event(self, kind, payload, media_s=None):
        with self.lock:
            now = time.monotonic()
            seq = len(self.events) + 1
            e = {"schema_version": "3", "event_id": f"{self.session}:event:{seq}",
                 "event_seq": seq, "session": self.session, "media_id": self.media_id,
                 "epoch": self.epoch, "kind": kind, "created_monotonic_s": now,
                 "elapsed_s": now-self.start, "media_s": media_s,
                 "payload": copy.deepcopy(payload)}
            line = json_text(e)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            self.events.append(e)
            return e

    def valid_dependencies(self, deps):
        return all(i in self.active and self.records[i]["revision"] == rev for i, rev in deps.items())

    def ancestry(self, record):
        seen, todo = set(), list(record.get("dependencies", {}))
        while todo:
            i = todo.pop()
            if i in seen:
                continue
            seen.add(i)
            parent = self.records.get(i)
            if parent is None:
                raise ValueError("missing_dependency")
            if parent["kind"] in FORBIDDEN:
                raise ValueError("self_reinforcement")
            todo.extend(parent.get("dependencies", {}))
        return seen

    def validate(self, r, clock=None):
        required = {"id", "kind", "revision", "epoch", "dependencies"}
        if not required <= r.keys() or r["epoch"] != self.epoch or type(r["revision"]) is not int or r["revision"] < 1:
            raise ValueError("record_envelope")
        json_text(r)
        if r["id"] in self.records:
            raise ValueError("id_reused")
        if not self.valid_dependencies(r["dependencies"]):
            raise ValueError("superseded_dependency")
        kind = r["kind"]
        if kind not in FORBIDDEN:
            self.ancestry(r)
        if kind == "observation":
            ins, evs = set(r["input_roots"]), set(r["evidence_roots"])
            if not evs or not evs <= ins or not ins <= set(r["root_sources"]):
                raise ValueError("root_lineage")
            if set(r["citations"]) != evs:
                raise ValueError("citation_roots")
            for root in ins:
                src = r["root_sources"][root]
                a, b = map(finite, src["interval"])
                if not clock or not clock.permits(a, b, src["modality"], r["epoch"]):
                    raise ValueError("unreleased_media")
                if root != root_key(self.media_id, self.epoch, src["modality"], src["stream"], a):
                    raise ValueError("nonphysical_root")
                if int(a // 2) != int(b // 2):
                    raise ValueError("root_crosses_bin")
                stamp = pts_seconds(src["pts"], src["time_base"])
                stamp -= finite(src.get("media_origin_s", 0))
                if "sample_offset" in src:
                    if type(src["sample_offset"]) is not int or src["sample_offset"] < 0 or src.get("sample_rate",0) <= 0:
                        raise ValueError("invalid_sample_offset")
                    stamp += src["sample_offset"] / src["sample_rate"]
                if abs(stamp - b) > 1e-6:
                    raise ValueError("pts_mismatch")
                if root in evs and not a <= finite(r["citations"][root]) <= b:
                    raise ValueError("citation_outside_source")
            if finite(r["input_cutoff_s"]) > clock.media_s:
                raise ValueError("future_cutoff")
            for parent_id in r["dependencies"]:
                parent = self.records[parent_id]
                if parent["kind"] != "observation" or not set(parent["input_roots"]) <= ins:
                    raise ValueError("derived_lineage_lost")
        elif kind == "evidence_evaluation":
            if r.get("evaluation_kind") != "evidence":
                raise ValueError("evaluation_kind")
            obs = self.records.get(r.get("observation_id"))
            if not obs or obs["kind"] != "observation" or obs["id"] not in r["dependencies"]:
                raise ValueError("evaluation_observation")
        elif kind == "contribution":
            ev = self.records.get(r.get("evaluation_id"))
            if not ev or ev["kind"] != "evidence_evaluation" or ev["id"] not in r["dependencies"]:
                raise ValueError("contribution_evaluation")
        elif kind not in FORBIDDEN:
            raise ValueError("unknown_kind")

    def add(self, r, clock=None):
        with self.lock:
            self.validate(r, clock)
            self.records[r["id"]] = copy.deepcopy(r)
            self.active.add(r["id"])
            self.event("record", r)

    def descendants(self, ids):
        invalid = set(ids)
        while True:
            more = {i for i in self.active if set(self.records[i].get("dependencies", {})) & invalid}
            if more <= invalid:
                return invalid
            invalid |= more

    def invalidate(self, ids, reason):
        with self.lock:
            invalid = self.descendants(ids) & self.active
            self.active -= invalid
            self.event("invalidation", {"ids": sorted(invalid), "reason": reason})
            return invalid

    def replace(self, old_ids, new_records, clock):
        with self.lock:
            if not set(old_ids) <= self.active or len({r["id"] for r in new_records}) != len(new_records):
                raise ValueError("invalid_supersedes")
            invalid = self.descendants(old_ids)
            for r in new_records:
                if set(r.get("supersedes", [])) != set(old_ids) or set(r["dependencies"]) & invalid:
                    raise ValueError("invalid_supersedes")
                self.validate(r, clock)
            self.active -= invalid
            for r in new_records:
                self.records[r["id"]] = copy.deepcopy(r)
                self.active.add(r["id"])
            self.event("atomic_replacement", {"invalidated": sorted(invalid), "records": new_records})

    def reset(self, epoch):
        with self.lock:
            self.active.clear()
            self.epoch = epoch
            self.event("epoch_reset", {"epoch": epoch})
