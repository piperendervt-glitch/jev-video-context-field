"""Record only this dedicated environment's versions, licenses and footprint."""
import importlib.metadata as metadata
import json,platform,shutil,subprocess
from pathlib import Path
import psutil
ROOT=Path(__file__).resolve().parents[1]
packages=[]
for dist in metadata.distributions():
    m=dist.metadata
    packages.append({'name':m['Name'],'version':dist.version,'license_expression':m.get('License-Expression'),
        'license':m.get('License'),'home_page':m.get('Home-page'),'project_urls':m.get_all('Project-URL') or [],
        'license_files':[str(p) for p in dist.files or [] if 'license' in str(p).lower() or 'copying' in str(p).lower()]})
report={'python':platform.python_version(),'platform':platform.platform(),'cpu':platform.processor(),'logical_cpus':psutil.cpu_count(),
        'ram_bytes':psutil.virtual_memory().total,'free_disk_bytes':shutil.disk_usage(ROOT).free,
        'gpu':subprocess.check_output(['nvidia-smi','--query-gpu=name,driver_version,memory.total,memory.used','--format=csv,noheader'],text=True).strip(),
        'packages':sorted(packages,key=lambda p:p['name'].lower()),
        'directory_bytes':{name:sum(p.stat().st_size for p in (ROOT/name).rglob('*') if p.is_file()) for name in ('.venv','models','.cache')},
        'asr_runtime':'CUDA int8_float16, CTranslate2, shared single heavy lane',
        'vlm_text_runtime':'CUDA bfloat16, transformers SDPA, shared Qwen3-VL-2B',
        'download_records':['install-torch.json','install-local.json','model-download-manifest.json']}
(ROOT/'artifacts/installed-environment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='packages'},ensure_ascii=False,indent=2))
