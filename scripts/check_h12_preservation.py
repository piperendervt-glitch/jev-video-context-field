from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.offline_guard import install, ROOT, COUNTS
install()
import json
import hashlib
import difflib
import sqlite3
from collections import Counter
from context_fields.evaluation_lists import classify

out=ROOT/'artifacts/human-debug-h12/20260920'
def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
baseline=json.loads((out/'preservation-start.json').read_text(encoding='utf8'))
allowed={'context_fields/activity.py','context_fields/review_index.py','context_fields/debug_review.py','context_fields/debug_server.py',
         'web/activity.js','web/activity-view.js','web/activity.css'}
changed=[rel.replace('\\','/') for rel,expected in baseline.items() if digest(ROOT/rel)!=expected]
assert set(changed)<=allowed,changed
budget=ROOT/'artifacts/local-private/jev-campaign-v02.json'
assert budget.read_bytes()==(out/'budget-start.json').read_bytes()
(out/'budget-end.json').write_bytes(budget.read_bytes())
with sqlite3.connect(f'file:{(ROOT/"human-review/reviews.sqlite").as_posix()}?mode=ro',uri=True) as db:
    counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['pins','annotations','issues']}
assert counts=={'pins':4,'annotations':0,'issues':0},counts
patch=[]
for p in (out/'backup').rglob('*'):
    if not p.is_file():continue
    rel=p.relative_to(out/'backup')
    patch.extend(difflib.unified_diff(p.read_text(encoding='utf8').splitlines(True),(ROOT/rel).read_text(encoding='utf8').splitlines(True),fromfile='before/'+str(rel),tofile='after/'+str(rel)))
(out/'existing-source.diff').write_text(''.join(patch),encoding='utf8')
prepared=json.loads((out/'prepared.json').read_text(encoding='utf8'))
media=[]
for run in prepared['runs']:
    with sqlite3.connect(f'file:{(ROOT/"artifacts/human-review-index"/(run["run"]+".sqlite")).as_posix()}?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;units={};links={}
        for r in db.execute('SELECT * FROM h12_units ORDER BY seq'):
            units.setdefault((r['epoch'],r['oid'],r['rev'],r['hid']),[]).append(dict(r))
        for r in db.execute('SELECT * FROM h12_links'):
            links.setdefault((r['epoch'],r['oid'],r['rev']),set()).add(r['hid'])
        states=Counter();moved=[];partial=[]
        for r in db.execute('SELECT body FROM activity a WHERE seq=(SELECT max(seq) FROM activity b WHERE b.row_id=a.row_id)'):
            row=json.loads(r[0])
            if row['agent'] in ['EvidenceEvaluator','DominanceReader']:continue
            c=classify(db,row,run['events'],units,links,set());states[c['state']]+=1
            data={'row_id':row['row_id'],'agent':row['agent'],'evaluation':c}
            if c['complete']:moved.append(data)
            elif any(t['state'] in ('done','error') for t in c['targets']):partial.append(data)
        (out/(run['run']+'-classification.json')).write_text(json.dumps({'states_before_stop_label':dict(states),'moved':moved,'partial_or_error':partial},ensure_ascii=False,indent=2),encoding='utf8')
        offset,size=db.execute("SELECT offset,size FROM events WHERE kind='local_asset' LIMIT 1").fetchone()
    with (ROOT/'artifacts/sessions'/(run['run']+'.jsonl')).open('rb') as f:
        f.seek(offset);asset=json.loads(f.read(size))['payload']
    candidates=[p for p in (ROOT/'artifacts/media').glob(asset['id']+'.*') if p.suffix.lower() in ('.mp4','.mkv','.mov','.webm','.avi','.m4v')]
    assert len(candidates)==1
    actual=digest(candidates[0]);assert actual==asset['sha256']
    media.append({'run':run['run'],'asset':asset['id'],'sha256':actual,'matches_saved_run':True})
report={'baseline_files':len(baseline),'intentional_changed':changed,'unexpected_changed':[],'preserved':len(baseline)-len(changed),
    'human':counts,'budget_start':json.loads((out/'budget-start.json').read_text()),'budget_end':json.loads(budget.read_text()),
    'budget_increment':dict.fromkeys(['attempts','units','questions','chars','bytes'],0),'media':media,'guard':COUNTS,
    'browser':{'available':False,'operation':False,'discovery':'No browser is available; []'},'restoration_executed':False}
(out/'preservation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'preserved':report['preserved'],'unexpected_changes':0,'human':counts,'budget_increment':report['budget_increment'],'media_hashes_match':True}))
