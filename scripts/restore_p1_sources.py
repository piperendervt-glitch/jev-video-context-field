"""Manual code rollback only, no deletion and no budget/data restoration."""
from pathlib import Path
import sys,json,hashlib,socket,shutil
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1'
if sys.argv[1:]!=['--restore']:raise SystemExit('Stop the debug Viewer normally, then use --restore. No data or budget rollback.')
with socket.socket() as sock:
    sock.settimeout(1)
    if sock.connect_ex(('127.0.0.1',8876))==0:raise SystemExit('Stop the Viewer normally first; no process is stopped by this script.')
manifest=json.loads((OUT/'restore-manifest.json').read_text(encoding='utf8'))
targets=[]
for item in manifest['changed_sources']:
    target=(ROOT/item['path']).resolve();backup=(OUT/'backup'/item['path']).resolve()
    if not target.is_relative_to(ROOT) or not backup.is_relative_to(OUT/'backup'):raise ValueError('path_outside_workspace')
    if hashlib.sha256(target.read_bytes()).hexdigest()!=item['final_sha256']:raise ValueError('later_source_changes:'+item['path'])
    if hashlib.sha256(backup.read_bytes()).hexdigest()!=item['backup_sha256']:raise ValueError('backup_changed:'+item['path'])
    targets.append((backup,target))
for backup,target in targets:shutil.copyfile(backup,target)
print('Restored',len(targets),'source files. Budget, runs, raw captures and human records were not changed.')
