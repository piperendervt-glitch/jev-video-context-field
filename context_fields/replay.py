"""Strict replay consumes recorded display frames in event arrival order."""
import copy
import json
from .config import CONFIG_HASH

def read_events(path):
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if len(line) > 4_000_000:
                raise ValueError("event_size")
            event = json.loads(line)
            if event["schema_version"] not in {"2","3"} or event["event_seq"] != len(events)+1:
                raise ValueError("replay_version_or_order")
            events.append(event)
    return events

class Replay:
    def __init__(self, events):
        self.events = copy.deepcopy(events)
        self.frames = [e for e in self.events if e["kind"] == "display"]
        for e in self.frames:
            if e["payload"]["snapshot"]["config_hash"] != CONFIG_HASH:
                raise ValueError("replay_config_mismatch")

    def at(self, elapsed_s=None, event_cursor=None):
        available = [e for e in self.frames if (elapsed_s is None or e["elapsed_s"] <= elapsed_s)
                     and (event_cursor is None or e["event_seq"] <= event_cursor)]
        return copy.deepcopy(available[-1]["payload"]) if available else None

    def as_of(self, media_cutoff, event_cursor):
        available = [e for e in self.frames if e["event_seq"] <= event_cursor
                     and e["payload"]["snapshot"]["as_of_media_s"] <= media_cutoff]
        return copy.deepcopy(available[-1]["payload"]) if available else None

    def record_at(self, record_id, event_cursor):
        for event in self.events:
            if event['event_seq']>event_cursor:break
            records=[event['payload']] if event['kind']=='record' else event['payload'].get('records',[]) if event['kind']=='atomic_replacement' else []
            for record in records:
                if record['id']==record_id:return copy.deepcopy(record)
        return None
