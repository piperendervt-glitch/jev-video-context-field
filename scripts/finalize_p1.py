"""Record limited source diff and prove immutable historical artifacts."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.offline_guard import install
install()
import json,hashlib,difflib
from dataclasses import asdict
from context_fields.dispatch_policy import P1
OUT=ROOT/'artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,v):path.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
source=Path('D:/Users/pipe_render/Downloads/jev-video-context-field-po/JEV_VIDEO_CONTEXT_FIELDS_DESIGN_v0_3_JA.md')
text=source.read_text(encoding='utf8')
header='> 正本の参照用写しです。以下の正本文は変更せず、実装参照の索引だけを末尾に追加しました。提供された正本ファイルは不変です。\n\n'
footer='\n\n---\n\n## 実装参照索引（正本文外）\n\n正本の§7・8・12〜14、§19、§25・30に対応する並列実行・Viewer・受入れ差分は [P1追補](JEV_PARALLEL_EVALUATION_P1_ADDENDUM_JA.md)、結果は [P1報告](JEV_PARALLEL_EVALUATION_P1_REPORT_JA.md) を参照してください。数式・CD/C0・TTLの正本文は変更しません。\n'
(ROOT/'docs/JEV_VIDEO_CONTEXT_FIELDS_DESIGN_v0_3_JA.md').write_text(header+text+footer,encoding='utf8')
save(OUT/'design-source.json',{'path':str(source),'sha256':sha(source),'body_unchanged_in_reference_copy':True})
save(ROOT/'config/dispatch-p1-provisional.json',asdict(P1))
baseline=json.loads((OUT/'preservation-start.json').read_text(encoding='utf8'));checks=[];unexpected=[];changed=[];diffs=[]
for relative,old in baseline.items():
    p=ROOT/relative;new=sha(p) if p.is_file() else None
    backed=(OUT/'backup'/relative).is_file()
    checks.append({'path':relative,'start':old,'end':new,'same':old==new,'source_backup':backed})
    if old!=new and not backed:unexpected.append(relative)
for backup in sorted((OUT/'backup').rglob('*')):
    if not backup.is_file():continue
    relative=backup.relative_to(OUT/'backup');current=ROOT/relative
    if sha(backup)==sha(current):continue
    changed.append({'path':str(relative),'backup_sha256':sha(backup),'final_sha256':sha(current)})
    diffs.extend(difflib.unified_diff(backup.read_text(encoding='utf8').splitlines(True),current.read_text(encoding='utf8').splitlines(True),
        fromfile='before/'+relative.as_posix(),tofile='after/'+relative.as_posix()))
(OUT/'source.diff').write_text(''.join(diffs),encoding='utf8')
save(OUT/'restore-manifest.json',{'root':str(ROOT),'changed_sources':changed,'do_not_restore':['campaign','work-state','raw','sessions','human-review','media','models','.env']})
added=[ROOT/'context_fields'/n for n in ('parallel_dispatcher.py','question_batch.py','dispatch_policy.py')]
added += list((ROOT/'scripts').glob('*p1*')) + [ROOT/'tests/test_parallel_p1.py',ROOT/'config/dispatch-p1-provisional.json']
added += list((ROOT/'docs').glob('JEV_PARALLEL_EVALUATION_P1*'))+[ROOT/'docs/JEV_VIDEO_CONTEXT_FIELDS_DESIGN_v0_3_JA.md']
save(OUT/'added-files.json',[{'path':str(p.relative_to(ROOT)),'sha256':sha(p)} for p in sorted(set(added)) if p.is_file()])
save(OUT/'preservation-final.json',{'checks':checks,'unexpected_changes':unexpected,'changed_source_count':len(changed),
    'original_runs_and_human_records_unchanged':not unexpected,'translation_and_other_repos':'not edited'})
save(OUT/'budget-end.json',json.loads((ROOT/'artifacts/local-private/jev-campaign-v02.json').read_text()))
assert not unexpected,unexpected
print(json.dumps({'tracked':len(checks),'changed_sources':len(changed),'unexpected_changes':unexpected}))
