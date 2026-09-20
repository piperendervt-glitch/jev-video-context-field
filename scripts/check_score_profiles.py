"""Compile saved existing profiles into fresh MOCK-only diagnostic readouts."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.offline_guard import install
install()
import hashlib
import json
from scripts.score_saved_fixtures import fixed_manifest, profile_frames, scaffold
from context_fields.jev import mock_response


def main():
    out,_=fixed_manifest();results=[]
    for (run,pid),fixture in profile_frames().items():
        ledger,reader,clock,snapshot=scaffold(fixture)
        profile=reader.registry.profiles[pid];before=snapshot.data();req=None
        for _ in range(6):
            candidate=reader.build(snapshot)
            if candidate and pid in candidate.unit_ids:req=candidate;break
        assert req is not None
        payload=json.loads(req.payload_json);raw=mock_response(req)
        outputs=reader.accept(req,raw,snapshot,clock,before['scope_revision'],0,1,mode='MOCK')
        result=next(r for r in outputs if r['profile_id']==pid)
        assert result['status']=='valid' and result['items']=={'topic':50.0}
        assert snapshot.data()==before and not reader.registry.profiles[pid]['runtime_verified']
        raw['answers'][pid+'.assessability'].update(choice='insufficient',probabilities={'assessable':0.,'insufficient':1.,'conflicting':0.})
        null=next(r for r in reader.accept(req,raw,snapshot,clock,before['scope_revision'],0,1) if r['profile_id']==pid)
        assert null['items']=={'topic':None} and null['status']=='insufficient'
        results.append({'source_run':run,'source_event':fixture['source_event'],'profile_id':pid,
            'revision':profile['revision'],'rubric_version':profile['rubric_version'],
            'category_label':profile['category_label'],'mode':'OFFLINE-MOCK',
            'request_hash':hashlib.sha256(req.payload_json.encode()).hexdigest(),
            'questions':{k:q for k,q in payload['questions'].items() if k.startswith(pid+'.')},
            'unit':payload['state']['units'][pid],'valid_items':result['items'],
            'insufficient_items':null['items'],'runtime_verified':False,'field_snapshot_unchanged':True,
            'source_data_unchanged':True,'live_acceptance':'NOT_PERFORMED'})
    (out/'profiles-offline.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{'profile':x['profile_id'],'version':x['revision'],'mode':x['mode'],'runtime_verified':x['runtime_verified']} for x in results]))


if __name__=='__main__':main()
