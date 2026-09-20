"""Explicit migration bookkeeping and path-only edits; no inference or Jev."""
from pathlib import Path
import sys,json,sqlite3,subprocess,shutil,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.rename_checkpoint import OUT,event,save,digest
OLD=Path('C:/dev/jev-video-context-field-po');NEW=Path('C:/dev/jev-video-context-field')
def checkpoint():
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    save('checkpoint.json',{'sha':sha,'branch':'main'});event('checkpoint',sha=sha)
def protected():
    result=[]
    for folder in ('artifacts/sessions','artifacts/media','human-review','artifacts/local-private'):
        for p in (ROOT/folder).rglob('*'):
            if not p.is_file() or p.is_relative_to(OUT) or p.name.endswith(('-wal','-shm','.lock')):continue
            result.append({'path':str(p.relative_to(ROOT)),'bytes':p.stat().st_size,'sha256':digest(p)})
    return result
def stopped():
    with sqlite3.connect(f'file:{(ROOT/"human-review/reviews.sqlite").as_posix()}?mode=ro',uri=True) as source:
        assert source.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        with sqlite3.connect(OUT/'human-review-stopped.sqlite') as dest:source.backup(dest)
    save('protected-stopped.json',protected());event('stopped',sqlite_backup='official SQLite backup API')
def paths():
    assert ROOT==NEW and not OLD.exists()
    for name in ('start_p1_debug.ps1','start_cd_debug.ps1','start_h12_debug.ps1'):
        p=ROOT/'scripts'/name;before=p.read_text(encoding='utf8')
        needle="$taskRoot = 'C:\\dev\\jev-video-context-field-po'"
        if needle not in before:raise ValueError('unexpected_launcher:'+name)
        p.write_text(before.replace(needle,'$taskRoot = Split-Path -Parent $PSScriptRoot'),encoding='utf8')
    p=ROOT/'README.md';p.write_text(p.read_text(encoding='utf8').replace(str(OLD),str(NEW)),encoding='utf8')
    save('path-binding.json',{'migration_id':OUT.name,'old_root':str(OLD),'new_root':str(NEW),
        'assets':[x for x in protected() if x['path'].startswith('artifacts\\media\\')],
        'resolver':'existing root-relative AssetStore with opaque ID, hash, size verification; no broad prefix rewrite',
        'campaign':'same relative artifacts/local-private/jev-campaign-v02.json, unchanged bytes',
        'work_state':'same relative private work files; finished status retained',
        'historical_logs_and_grants':'unchanged; only private root relocation binding added'})
    event('paths_ready')
def check_stopped():
    assert ROOT==OLD and not NEW.exists()
    expected=json.loads((OUT/'protected-stopped.json').read_text(encoding='utf8'))
    mismatches=[x['path'] for x in expected if not (ROOT/x['path']).is_file() or digest(ROOT/x['path'])!=x['sha256']]
    assert not mismatches,mismatches
    assert (OUT/'human-review-stopped.sqlite').is_file()
    event('stopped_assets_rechecked',files=len(expected));print('stopped assets unchanged',len(expected))
def verify():
    expected=json.loads((OUT/'protected-stopped.json').read_text(encoding='utf8'))
    mismatches=[x['path'] for x in expected if not (ROOT/x['path']).is_file() or digest(ROOT/x['path'])!=x['sha256']]
    save('preservation-after.json',{'files':len(expected),'mismatches':mismatches,'budget':json.loads((ROOT/'artifacts/local-private/jev-campaign-v02.json').read_text())})
    assert not mismatches,mismatches
    import importlib.metadata as md
    installed={d.metadata['Name'].lower().replace('_','-').replace('.','-'):d.version for d in md.distributions()}
    before=json.loads((OUT/'environment-before.json').read_text(encoding='utf8'))
    assert installed==before['packages'],(set(installed.items())^set(before['packages'].items()))
    assert sys.base_prefix==before['base'] and sys.version==before['python'] and Path(sys.prefix)==ROOT/'.venv'
    save('environment-after.json',{'executable':sys.executable,'base':sys.base_prefix,'version':sys.version,'packages':installed,'all_versions_identical':True})
    event('verified_preservation_and_environment',files=len(expected));print('preserved',len(expected),'files; packages identical')
if __name__=='__main__':
    {'checkpoint':checkpoint,'stopped':stopped,'check-stopped':check_stopped,'paths':paths,'verify':verify}[sys.argv[1]]()
