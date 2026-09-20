"""Explicit authorized installation only. No tokens, user data or inference API."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]

def main():
    preflight=json.loads((ROOT/'artifacts/install-preflight.json').read_text(encoding='utf-8'))
    report=[]
    for repo,folder in [('Systran/faster-whisper-small','whisper-small'),('Qwen/Qwen3-VL-2B-Instruct','qwen3-vl-2b')]:
        spec=preflight['models'][repo]
        if spec['gated'] or spec['license'] not in ('mit','apache-2.0'):raise ValueError('license_or_gate_changed')
        dest=ROOT/'models'/folder;dest.mkdir(parents=True,exist_ok=True)
        for entry in spec['files']:
            name=entry['name']
            if name=='.gitattributes':continue
            if '/' in name or '\\' in name:raise ValueError('nested_model_file')
            path=dest/name
            expected=(entry.get('lfs') or {}).get('sha256')
            if path.exists() and path.stat().st_size==entry['bytes']:
                digest=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
                if expected and digest!=expected:raise ValueError('existing_model_hash_mismatch')
            else:
                url=f"https://huggingface.co/{repo}/resolve/{spec['revision']}/{name}?download=true"
                print(f"Downloading {repo}/{name}: {entry['bytes']} bytes",flush=True)
                digest_obj=hashlib.sha256();partial=path.with_suffix(path.suffix+'.partial')
                with urllib.request.urlopen(url,timeout=90) as response,partial.open('wb') as f:
                    while chunk:=response.read(1024*1024):f.write(chunk);digest_obj.update(chunk)
                digest=digest_obj.hexdigest()
                if partial.stat().st_size!=entry['bytes'] or expected and digest!=expected:raise ValueError('download_integrity')
                partial.replace(path)
            report.append({'repo':repo,'revision':spec['revision'],'license':spec['license'],'file':str(path.relative_to(ROOT)),
                           'bytes':path.stat().st_size,'sha256':digest})
            (ROOT/'artifacts/model-download-manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(f"Complete: {repo}",flush=True)

if __name__=='__main__':main()
