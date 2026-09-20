"""Migration checkpoint inventory; never reads .env or credentials."""
from pathlib import Path
import sys,json,hashlib,shutil,time,re,ast
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/local-private/p1-checkpoint-rename-20260920-v1'
OUT.mkdir(parents=True,exist_ok=True)
def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(name,v):(OUT/name).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
def event(stage,**kw):
    with (OUT/'migration.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps({'stage':stage,'at':time.time(),**kw},ensure_ascii=False)+'\n')
def candidates():
    paths=[ROOT/p for p in ('README.md','.gitignore','pyproject.toml','requirements-local.txt','requirements-local.lock.txt')]
    for folder,glob in [('context_fields','*.py'),('web','*'),('tests','*.py'),('config','*.json'),('scripts','*.py'),('scripts','*.cjs'),('scripts','*.ps1')]:
        paths += [p for p in (ROOT/folder).glob(glob) if p.is_file()]
    paths += [ROOT/'docs'/n for n in ('P1_PUBLIC_SUMMARY_JA.md','MIGRATION_PUBLIC_JA.md','JEV_VIDEO_CONTEXT_FIELDS_DESIGN_v0_3_JA.md','SCORE_CD_DISPLAY_ONLY_CONTRACT_v1_JA.md','HUMAN_DEBUG_VIEWER_ADDENDUM_v0_3_H0_JA.md') if (ROOT/'docs'/n).exists()]
    return sorted(p for p in set(paths) if p.relative_to(ROOT).as_posix()!='scripts/check_local_models.py')
def audit(files):
    findings=[];texts={p:p.read_text(encoding='utf8') for p in files}
    for p,text in texts.items():
        for pattern in (r'(?:gh[pousr]_|github_pat_|sk-(?:proj-)?)[A-Za-z0-9_-]{20,}',r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'https?://[^\s/@:]+:[^\s/@]+@'):
            if re.search(pattern,text):findings.append({'path':str(p.relative_to(ROOT)),'reason':'credential_pattern'})
    # Compare long actual ASR/observation strings without printing their contents.
    actual=set()
    for p in (ROOT/'artifacts/sessions').glob('*.jsonl'):
        with p.open(encoding='utf8') as f:
            for line in f:
                e=json.loads(line);payload=e.get('payload',{})
                records=payload.get('records',[]) if e['kind']=='atomic_replacement' else [payload] if e['kind'] in ('record','transcript_version') else []
                for r in records:
                    if r.get('agent')=='SpeechASR' or e['kind']=='transcript_version':
                        s=r.get('text','')
                        if len(s)>=24 and not any(x in s.lower() for x in ('fixture','synthetic','合成')):actual.add(s)
    for p,text in texts.items():
        if any(s in text for s in actual):findings.append({'path':str(p.relative_to(ROOT)),'reason':'matches_private_asr'})
    save('public-audit.json',{'files':len(files),'private_asr_strings_compared':len(actual),'findings':findings})
    if findings:raise ValueError('public_audit_findings; inspect private report')
def prepare():
    if (OUT/'preflight.json').exists():raise ValueError('preflight_already_saved')
    files=candidates();backup=OUT/'source-backup'
    for p in files:
        dest=backup/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    # Immutable assets only. All derived indexes and caches are intentionally separate.
    protected=[]
    for folder in ('artifacts/sessions','artifacts/media','human-review','artifacts/local-private'):
        for p in (ROOT/folder).rglob('*'):
            if not p.is_file() or p.is_relative_to(OUT) or p.name.endswith(('-wal','-shm','.lock')):continue
            protected.append({'path':str(p.relative_to(ROOT)),'bytes':p.stat().st_size,'sha256':digest(p)})
    save('protected-before.json',protected)
    save('preflight.json',{'migration_id':OUT.name,'old_root':str(ROOT),'new_root':'C:\\dev\\jev-video-context-field',
        'remote':'https://github.com/piperendervt-glitch/jev-video-context-field.git','remote_public':True,'remote_empty_confirmed':True,
        'git_before':'not a repository; no parent git','base_python':sys.base_prefix,'source_hashes':{str(p.relative_to(ROOT)):digest(p) for p in files},
        'budget':json.loads((ROOT/'artifacts/local-private/jev-campaign-v02.json').read_text()),
        'user_acceptance':'P1動作確認完了 reported by user; does not imply unmeasured Jev performance'} )
    event('preflight',protected_files=len(protected),source_files=len(files))
    print('preflight saved',len(protected),'protected files')
if __name__=='__main__':
    if sys.argv[1:]==['--prepare']:prepare()
    elif sys.argv[1:]==['--audit']:
        files=candidates();audit(files);save('public-files.json',[p.relative_to(ROOT).as_posix() for p in files]);print('audited',len(files))
