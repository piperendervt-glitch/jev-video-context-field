"""Verify fixed inputs/budget read-only and inventory this task's exact changes."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.offline_guard import install, ROOT
install()
import difflib
import hashlib
import json
from datetime import datetime, timezone

ADDED=['context_fields/response_archive.py','scripts/offline_guard.py',
       'scripts/score_contract_offline.py','scripts/score_saved_fixtures.py',
       'scripts/check_score_offline_http.py','scripts/check_score_profiles.py',
       'scripts/finalize_score_offline.py','tests/test_score_offline.py',
       'docs/SCORE_CONTRACT_OFFLINE_REPORT_JA.md']


def sha(path,size=None):
    h=hashlib.sha256();remaining=path.stat().st_size if size is None else size
    with path.open('rb') as stream:
        while remaining:
            chunk=stream.read(min(1024*1024,remaining))
            if not chunk:raise ValueError('shortened_input')
            h.update(chunk);remaining-=len(chunk)
    return h.hexdigest()


def main():
    out=ROOT/'artifacts/score-contract-offline'/(ROOT/'artifacts/score-contract-offline/LATEST.txt').read_text().strip()
    before=json.loads((out/'manifest-before.json').read_text(encoding='utf-8'))
    result={'utc':datetime.now(timezone.utc).isoformat(),'fixed_inputs':{},'changes':{},'added_files':ADDED}
    patches=[]
    for rel,info in before['fixed_inputs'].items():
        path=ROOT/rel;actual=sha(path,info['size']);size=path.stat().st_size
        result['fixed_inputs'][rel]={'sha256':actual,'fixed_prefix_size':info['size'],
            'unchanged':actual==info['sha256'],'current_size':size,'appended_bytes':size-info['size']}
    for rel,info in before['source_backup'].items():
        backup=out/'backup'/rel
        assert sha(backup)==info['sha256'],rel
        result['changes'][rel]={'before_sha256':info['sha256'],'after_sha256':sha(ROOT/rel),'kind':'modified'}
        patches.extend(difflib.unified_diff(backup.read_text(encoding='utf-8').splitlines(True),
            (ROOT/rel).read_text(encoding='utf-8').splitlines(True),fromfile='before/'+rel,tofile='after/'+rel))
    for rel in ADDED:
        result['changes'][rel]={'after_sha256':sha(ROOT/rel),'kind':'added'}
        patches.extend(difflib.unified_diff([], (ROOT/rel).read_text(encoding='utf-8').splitlines(True),
            fromfile='/dev/null',tofile='after/'+rel))
    historical=json.loads((ROOT/'artifacts/live-status-m3a/final-runtime-manifest.json').read_text(encoding='utf-8'))
    result['previous_report_source_comparison']={}
    for rel,previous in historical['source_sha256'].items():
        actual=before['source_backup'][rel]['sha256'] if rel in before['source_backup'] else sha(ROOT/rel)
        result['previous_report_source_comparison'][rel]={'previous_report_sha256':previous,'task_start_sha256':actual,'matches':actual==previous}
    budget=json.loads((ROOT/'artifacts/local-private/jev-campaign-v02.json').read_text(encoding='utf-8'))
    start=json.loads((out/'budget-before.json').read_text(encoding='utf-8'))
    (out/'budget-after.json').write_text(json.dumps(budget,indent=2),encoding='utf-8')
    result['budget_delta']={key:budget[key]-value for key,value in start.items()}
    (out/'manifest-after.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    (out/'changes.diff').write_text(''.join(patches),encoding='utf-8')
    assert all(v['unchanged'] for v in result['fixed_inputs'].values()),'fixed_input_modified'
    print(json.dumps({'fixed_inputs_unchanged':len(result['fixed_inputs']),'budget_delta':result['budget_delta'],
        'modified_sources':len(before['source_backup']),'added_files':len(ADDED),
        'previous_source_matches':sum(v['matches'] for v in result['previous_report_source_comparison'].values())}))


if __name__=='__main__':main()
