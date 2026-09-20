"""Prepare exact installed wheels before moving; never read credentials."""
from pathlib import Path
import sys,json,hashlib,zipfile,email,shutil,importlib.metadata as md
from packaging.tags import sys_tags
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/local-private/p1-checkpoint-rename-20260920-v1'
OUT.mkdir(parents=True,exist_ok=True)
def norm(s):return s.lower().replace('_','-').replace('.','-')
installed={norm(d.metadata['Name']):d.version for d in md.distributions()}
report={'python':sys.version,'base':sys.base_prefix,'executable':sys.executable,'packages':installed}
(OUT/'environment-before.json').write_text(json.dumps(report,indent=2),encoding='utf8')
expected={}
for name in ('install-torch.json','install-local.json'):
    for item in json.loads((ROOT/'artifacts'/name).read_text(encoding='utf8')).get('install',[]):
        info=item['download_info'];key=norm(item['metadata']['name'])
        expected[(key,item['metadata']['version'])]=info['archive_info']['hashes']['sha256']
wheels=OUT/'wheels';wheels.mkdir(exist_ok=True);found={}
caches=[wheels,ROOT/'.cache/pip',Path.home()/'AppData/Local/pip/Cache']
supported={str(t) for t in sys_tags()}
for cache in caches:
    for p in cache.rglob('*'):
        if not p.is_file() or not (p.name.endswith('.body') or p.suffix=='.whl'):continue
        with p.open('rb') as f:
            if f.read(4)!=b'PK\x03\x04':continue
        try:
            with zipfile.ZipFile(p) as z:
                metadata=next((x for x in z.namelist() if x.endswith('.dist-info/METADATA')),None)
                if not metadata:continue
                m=email.message_from_bytes(z.read(metadata));name=norm(m['Name']);version=m['Version']
                if installed.get(name)!=version or name in found:continue
                w=email.message_from_bytes(z.read(metadata.rsplit('/',1)[0]+'/WHEEL'))
                tag=next(t for t in w.get_all('Tag') if t in supported)
                filename=m['Name'].replace('-','_')+'-'+version+'-'+tag+'.whl'
            h=hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
            if (name,version) in expected and h!=expected[(name,version)]:continue
            target=wheels/filename
            if not target.exists():shutil.copyfile(p,target)
            found[name]={'version':version,'wheel':filename,'sha256':h,'bytes':p.stat().st_size,
                'matches_prior_install_hash':(name,version) in expected,'source':'existing pip cache'}
        except (zipfile.BadZipFile,KeyError,StopIteration):continue
missing={n:v for n,v in installed.items() if n not in found}
# pip is bootstrapped by this exact base Python, verified after recreation.
report.update(wheels=found,missing=missing)
(OUT/'environment-preparation.json').write_text(json.dumps(report,indent=2),encoding='utf8')
(OUT/'requirements-exact.txt').write_text('\n'.join(n+'=='+v for n,v in sorted(installed.items()) if n!='pip')+'\n',encoding='utf8')
print(json.dumps({'installed':len(installed),'cached_wheels':len(found),'missing':missing,'wheel_bytes':sum(x['bytes'] for x in found.values())}))
