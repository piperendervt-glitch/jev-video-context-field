"""Check index and every reachable blob against explicitly reviewed source files."""
from pathlib import Path
import sys,subprocess,json,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.rename_checkpoint import OUT,audit,candidates,save
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
files=candidates();audit(files);allowed={p.relative_to(ROOT).as_posix():p for p in files}
rows=[] if '--history-only' in sys.argv else git('ls-files','-s','-z').decode().strip('\0').split('\0');current={}
for row in rows:
    header,name=row.split('\t',1);mode,oid,stage=header.split()
    if name not in allowed or mode!='100644' or stage!='0':raise ValueError('unapproved_index_path_or_mode:'+name)
    blob=git('cat-file','blob',oid);working=allowed[name].read_bytes()
    if blob.replace(b'\r\n',b'\n')!=working.replace(b'\r\n',b'\n'):raise ValueError('index_differs_from_reviewed_file:'+name)
    current[oid]={'path':name,'sha256':hashlib.sha256(blob).hexdigest()}
registry=OUT/'approved-git-blobs.json'
approved=json.loads(registry.read_text(encoding='utf8')) if registry.exists() else {}
approved.update(current);save('approved-git-blobs.json',approved)
objects=git('rev-list','--objects','--all').decode().splitlines()
blobs=[]
for row in objects:
    oid=row.split(' ',1)[0]
    if git('cat-file','-t',oid).strip()!=b'blob':continue
    if oid not in approved:raise ValueError('unreviewed_reachable_blob:'+oid)
    blobs.append(oid)
save('git-audit-latest.json',{'index_files':len(current),'reachable_objects':len(objects),'reachable_blobs':len(blobs),'all_reachable_blobs_previously_reviewed':True,
    'excluded':['credentials','media','ASR text','HTTP bodies','real runs','human DB','budgets','models','venv','cache','backups'],
    'known_whitespace':'original Markdown hard-break spaces and original test trailing newline preserved'})
print(json.dumps({'index_files':len(current),'reachable_blobs':len(blobs),'unreviewed':0}))
