"""Read-only reconstruction helpers. New evaluations exist only in memory, in MOCK.

These are diagnostic scaffolds, not an implementation of strict historical Replay.
Strict Replay continues to use the saved display events without recomputation.
"""
import copy
import json
from pathlib import Path
from context_fields.ledger import Ledger
from context_fields.jev import DominanceReader
from context_fields.clock import MediaClock
from context_fields.fields import FieldSnapshot
from context_fields.config import json_text
from scripts.score_contract_offline import frozen_events, ROOT
import hashlib


def fixed_manifest():
    out=ROOT/'artifacts/score-contract-offline'/(ROOT/'artifacts/score-contract-offline/LATEST.txt').read_text().strip()
    return out,json.loads((out/'manifest-before.json').read_text(encoding='utf-8'))


def profile_frames():
    """First available saved frame for each registered profile, with no later data."""
    out,manifest=fixed_manifest(); found={}
    for rel,info in manifest['fixed_inputs'].items():
        records={};active=set();seen=set();event_count=0
        for event,_ in frozen_events(ROOT/rel,info['size']):
            event_count+=1;k,p=event['kind'],event['payload']
            if k=='epoch_reset':active.clear()
            if k=='record':records[p['id']]=p;active.add(p['id'])
            if k=='invalidation':active.difference_update(p['ids'])
            if k=='atomic_replacement':
                active.difference_update(p['invalidated'])
                for r in p['records']:records[r['id']]=r;active.add(r['id'])
            if k=='display':
                for profile in p.get('profiles',[]):
                    pid=profile['id']
                    if pid in seen:continue
                    seen.add(pid)
                    found[(Path(rel).stem,pid)]={'source_run':Path(rel).stem,'source_event':event['event_seq'],
                        'frame':copy.deepcopy(p),'records':copy.deepcopy(records),'active':set(active),
                        'event_count':event_count,'media_id':event['media_id']}
    return found


def scaffold(fixture):
    frame=fixture['frame'];snapshot=FieldSnapshot(json_text(frame['snapshot']))
    ledger=Ledger('OFFLINE-DIAGNOSTIC-MOCK',fixture['media_id'],frame['clock']['epoch'])
    ledger.records=copy.deepcopy(fixture['records']);ledger.active=set(fixture['active'])
    # Only event-count identity is needed for new local request cursor generation.
    ledger.events=[{'kind':'offline_prefix_placeholder'} for _ in range(fixture['event_count'])]
    reader=DominanceReader(ledger)
    reader.counter=max([int(r['id'].rsplit(':',1)[-1]) for r in ledger.records.values()
                        if r['kind']=='dominance_readout']+[0])
    reader.registry.profiles={p['id']:copy.deepcopy(p) for p in frame['profiles']}
    reader.registry.revision=max([p['registry_revision'] for p in frame['profiles']]+[0])
    clock=MediaClock(epoch=frame['clock']['epoch'],media_s=frame['clock']['media_s'],
                     state=frame['clock']['state'],released=copy.deepcopy(frame['clock']['released']))
    return ledger,reader,clock,snapshot


def saved_mixed_response():
    """First v3 mixed failed response with complete current request metadata."""
    _,manifest=fixed_manifest()
    for rel,info in manifest['fixed_inputs'].items():
        requests={};records={};profiles={}
        for event,_ in frozen_events(ROOT/rel,info['size']):
            k,p=event['kind'],event['payload']
            if k=='evaluation_requested':requests[hashlib.sha256(p['request'].encode()).hexdigest()]=p['request']
            if k=='profile_registered':profiles[p['id']]=p
            if k=='atomic_replacement':
                for r in p['records']:records[r['id']]=r
            if k!='record':continue
            if p['kind']=='dominance_evaluation' and p.get('status')=='error':
                request=requests.get(p['input_hash'])
                if request:
                    body=json.loads(request);units=body['state']['units']
                    if len(units)>1 and all('item_labels' in u for u in units.values()):
                        return {'source_run':Path(rel).stem,'evaluation_event':event['event_seq'],
                            'request':request,'evaluation':p,'records':records,'profiles':profiles,
                            'media_id':event['media_id']}
            records[p['id']]=p
    raise ValueError('complete_saved_mixed_response_not_found')
