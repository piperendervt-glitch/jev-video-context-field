"""evidence-mass-v1.1. Maximum BEFORE decay, independently for each sign."""
import copy
from dataclasses import asdict, dataclass
from .clock import finite
from .config import CONFIG, CONFIG_HASH, json_text

@dataclass(frozen=True)
class Hypothesis:
    id: str
    space: str
    scope: str
    subject: str
    facet: str
    value: str
    reference_mode: str
    label: str

@dataclass(frozen=True)
class FieldSnapshot:
    """Serialized canonical JSON ensures deep immutability, including nested values."""
    encoded: str

    def data(self):
        import json
        return json.loads(self.encoded)

class FieldReducer:
    def __init__(self, ledger):
        self.ledger = ledger
        self.hypotheses = {}
        self.aliases = {}
        self.scope_revision = 1
        self.shot = "shot01"
        self.shot_boundaries = [(0.0, self.shot)]
        self.counter = 0

    def register(self, h):
        if h.space not in CONFIG["half_life"]:
            raise ValueError("unknown_space")
        key = (h.space, h.scope, h.subject, h.facet, h.value, h.reference_mode)
        for existing in self.hypotheses.values():
            if (existing.space, existing.scope, existing.subject, existing.facet, existing.value, existing.reference_mode) == key:
                self.aliases[h.id] = existing.id
                return existing.id
        if sum(x.space == h.space and (x.scope == self.shot or x.scope == "session") for x in self.hypotheses.values()) >= 128:
            self.ledger.event("candidate_deferred", {"id": h.id, "reason": "hypothesis_limit"})
            return None
        self.hypotheses[h.id] = h
        self.ledger.event("hypothesis", asdict(h))
        return h.id

    def contribute(self, evaluation_id, h_id, current_media_s, completed_wall_s=None):
        with self.ledger.lock:
            ev = self.ledger.records[evaluation_id]
            h_id = self.aliases.get(h_id, h_id)
            h = self.hypotheses[h_id]
            if ev["kind"] != "evidence_evaluation" or evaluation_id not in self.ledger.active:
                raise ValueError("invalid_evaluation")
            obs = self.ledger.records[ev["observation_id"]]
            if h_id != ev["hypothesis_id"] or obs["scope"] != h.scope:
                raise ValueError("target_scope_mismatch")
            if h.reference_mode != obs["reference_mode"] or h.subject != obs["subject"]:
                raise ValueError("illegal_routing")
            if h.scope != "session" and h.scope != self.shot:
                raise ValueError("old_shot")
            if h.scope != "session":
                for root in obs["evidence_roots"]:
                    source = obs["root_sources"][root]
                    if source["modality"] == "video" and any(self.scope_at(t) != h.scope for t in source["interval"]):
                        raise ValueError("source_shot_mismatch")
            if current_media_s - max(obs["citations"].values()) > CONFIG["evidence_ttl_s"]:
                raise ValueError("evidence_ttl")
            if completed_wall_s is not None and completed_wall_s > ev["accepted_wall_s"] + 5:
                raise ValueError("request_deadline")
            c = {"id": f"contribution:{evaluation_id}", "kind": "contribution", "revision": 1,
                 "epoch": ev["epoch"], "dependencies": {evaluation_id: ev["revision"]},
                 "evaluation_id": evaluation_id, "hypothesis_id": h_id}
            self.ledger.add(c)
            return c

    def scope_at(self, media_s):
        return next(scope for boundary, scope in reversed(self.shot_boundaries) if boundary <= media_s)

    def cut(self, boundary_s, discovered_s):
        if boundary_s > discovered_s or boundary_s <= self.shot_boundaries[-1][0]:
            raise ValueError("future_cut")
        with self.ledger.lock:
            old = self.shot
            self.scope_revision += 1
            self.shot = f"shot{self.scope_revision:02d}"
            self.shot_boundaries.append((boundary_s, self.shot))
            invalid = [i for i in self.ledger.active if self.ledger.records[i]["kind"] == "observation"
                       and self.ledger.records[i].get("scope") != "session"]
            # Re-analysis is the conservative correction for post-boundary observations.
            # Old anonymous tracks are never carried into the new shot.
            corrected = [i for i in invalid if max(self.ledger.records[i]["citations"].values()) >= boundary_s]
            self.ledger.invalidate(invalid, "cut_scope_reanalysis")
            self.ledger.event("cut", {"boundary_s": boundary_s, "discovered_s": discovered_s,
                                      "old_scope": old, "new_scope": self.shot, "reanalysis": corrected})

    def snapshot(self, t):
        t = finite(t)
        with self.ledger.lock:
            expired = [i for i in self.ledger.active if self.ledger.records[i]["kind"] == "observation"
                       and t - max(self.ledger.records[i]["citations"].values()) > 180]
            if expired:
                self.ledger.invalidate(expired, "retention_expired")
            selected, dependencies = {}, {}
            for cid in sorted(self.ledger.active):
                c = self.ledger.records[cid]
                if c["kind"] != "contribution":
                    continue
                ev = self.ledger.records[c["evaluation_id"]]
                obs = self.ledger.records[ev["observation_id"]]
                h = self.hypotheses[c["hypothesis_id"]]
                if h.scope not in {"session", self.shot}:
                    continue
                for i in [cid, ev["id"], obs["id"], *self.ledger.ancestry(obs)]:
                    dependencies[i] = self.ledger.records[i]["revision"]
                roots = obs["evidence_roots"]
                for g in roots:
                    tau = obs["citations"][g]
                    if tau > t:
                        raise ValueError("future_contribution")
                    for sign, p in (("support", ev["p_support"]), ("contradict", ev["p_contradict"])):
                        vals = [finite(v) for v in [ev["q"], ev["r"], ev["a"], p]]
                        if any(v < 0 or v > 1 for v in vals) or ev["q"] not in (0, 1):
                            raise ValueError("invalid_weight")
                        u = vals[0]*vals[1]*vals[2]*vals[3]/len(roots)
                        key = (h.space, g, h.id, sign)
                        item = {"u": u, "tau": tau, "evaluation_id": ev["id"], "observation_id": obs["id"], "root": g}
                        old = selected.get(key)
                        if old is None or (-u, -tau, ev["id"]) < (-old["u"], -old["tau"], old["evaluation_id"]):
                            selected[key] = item
            totals = {}
            for (space, g, _, _), item in selected.items():
                totals[space, g] = totals.get((space, g), 0.0) + item["u"]
            spaces = {}
            for space, half in CONFIG["half_life"].items():
                rows = []
                for h in self.hypotheses.values():
                    if h.space != space or h.scope not in {"session", self.shot}:
                        continue
                    signs = {"support": [], "contradict": []}
                    for (s, g, hid, sign), item in selected.items():
                        if s == space and hid == h.id:
                            d = item["u"] / max(1.0, totals[s, g])
                            signs[sign].append(dict(item, d=d, decayed=d*2**(-(t-item["tau"])/half)))
                    support = sum(i["decayed"] for i in signs["support"])
                    contra = sum(i["decayed"] for i in signs["contradict"])
                    rows.append(dict(asdict(h), S=support, R=contra, E=max(0.0, support-contra),
                                     sources=signs, conflict=support > 0 and contra > 0))
                den = 1.0 + sum(row["E"] for row in rows)
                for row in rows:
                    row["C"] = row["E"]/den
                spaces[space] = {"hypotheses": rows, "UNKNOWN": 1.0/den, "denominator": den,
                                 "value_kind": "field_concentration_pct"}
            self.counter += 1
            data = {"schema_version": "3", "kind": "field_snapshot", "snapshot_id": f"{self.ledger.epoch}:snapshot:{self.counter}",
                    "session": self.ledger.session, "media_id": self.ledger.media_id, "epoch": self.ledger.epoch,
                    "as_of_media_s": t, "input_cursor": len(self.ledger.events), "scope_revision": self.scope_revision,
                    "shot": self.shot, "dependency_versions": dependencies, "spaces": spaces,
                    "field_rule_id": CONFIG["field_rule_id"], "config_hash": CONFIG_HASH,
                    "root_allocations": [{"space": s, "root": g, "L": v, "allocated": v/max(1, v)} for (s,g),v in totals.items()]}
            return FieldSnapshot(json_text(data))
