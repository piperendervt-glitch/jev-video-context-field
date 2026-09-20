"""Read-only operational diagnosis; never opens media, credentials or sends requests."""
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/live-status-m3a'

def diagnose(path):
    counts = Counter(); last = {}; rejects = []; models = defaultdict(list)
    records = {}; displays = []; requests = {}; failures = []
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            e = json.loads(line); p = e['payload']; counts[e['kind']] += 1
            last[e['kind']] = {k:e[k] for k in ('event_seq','created_monotonic_s','media_s')}
            if e['kind'] == 'evaluation_requested':
                req = json.loads(p['request']); requests[req.get('state',{}).get('input_snapshot_id')] = req
            if e['kind'] == 'local_model_completed':
                models[p['agent']].append({'accepted_to_completed_s':p['completed']-p['accepted'],
                    'queue_s':p['started']-p['accepted'],'expired':p['expired'],'event_seq':e['event_seq']})
            if e['kind'] in ('evaluation_rejected','readout_rejected','local_error','contribution_rejected'):
                rejects.append({'event_seq':e['event_seq'],**p})
            for r in ([p] if e['kind']=='record' else p.get('records',[]) if e['kind']=='atomic_replacement' else []):
                records[r['id']] = r
                last[r['kind']] = {'event_seq':e['event_seq'],'id':r['id'],'as_of_media_s':r.get('as_of_media_s'),
                    'citations':r.get('citations'),'status':r.get('status'),'reason':r.get('reason')}
                if r.get('status')=='error':
                    failures.append({'event_seq':e['event_seq'],'record':r,'request':requests.get(r.get('input_snapshot_id'))})
            if e['kind']=='display':
                item={'event_seq':e['event_seq'],'clock':p['clock'],'readouts':p['readouts'],
                      'queues':p['queues'],'evaluation_status':p.get('evaluation_status'),
                      'media':p.get('media'),'external':p['external']}
                # Keep transitions, not 10Hz duplicate frames.
                state=(p['clock']['state'],p.get('evaluation_status',{}).get('stopped'))
                if not displays or state!=displays[-1]['transition']:displays.append(dict(item,transition=state))
                final=item
    def stats(rows):
        times=sorted(r['accepted_to_completed_s'] for r in rows)
        return {'count':len(rows),'expired':sum(r['expired'] for r in rows),
                'p50_s':statistics.median(times),'p95_s':times[min(len(times)-1,int(.95*(len(times)-1)))],
                'last':rows[-1], 'pre_admission_slot_wait':'LEGACY_MISSING'}
    return {'run':path.stem,'events':dict(counts),'last_stages':last,'transitions':displays,'final':final,
            'rejections':rejects,'response_failures':failures,'models':{k:stats(v) for k,v in models.items()},
            'limitations':['No browser receive/render timestamps in v2 logs','No pre-admission slot timestamps in v2 logs']}

if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    for run in ('run-1789824695716131300','run-1789823740249298400','run-1789823427762700300'):
        result=diagnose(ROOT/'artifacts/sessions'/f'{run}.jsonl')
        (OUT/f'{run}-diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'run':run,'counts':result['events'],'models':result['models'],
            'failures':[(r['event_seq'],r['record']['reason']) for r in result['response_failures']],
            'final_clock':result['final']['clock'],'last':result['last_stages']},ensure_ascii=False))
