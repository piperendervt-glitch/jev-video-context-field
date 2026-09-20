"""Migration regression output stays separate from historical P1 artifacts."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.offline_guard import install,COUNTS
install()
OUT=ROOT/'artifacts/local-private/p1-checkpoint-rename-20260920-v1'
if sys.argv[1:] == ['pytest']:
    import pytest
    # The two opt-in local tests include a face detector; do not run inference.
    import tempfile
    temporary_root=Path(tempfile.mkdtemp(prefix='pytest-',dir=OUT)).resolve()
    assert temporary_root.parent==OUT.resolve()
    # A fresh, migration-owned directory avoids shared TEMP ownership conflicts.
    result=pytest.main(['-q','-m','not local','--basetemp',str(temporary_root/'cases'),
                       '-o','cache_dir='+str(temporary_root/'cache')])
    (OUT/'pytest-guard.json').write_text(json.dumps({'exit_code':int(result),'guard':COUNTS,'model_inference':'local marker excluded'},indent=2),encoding='utf8')
    raise SystemExit(result)
if sys.argv[1:] == ['saved-media']:
    source=ROOT/'scripts/check_p1_saved_media.py'
    code=source.read_text(encoding='utf8').replace('artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1','artifacts/local-private/p1-checkpoint-rename-20260920-v1/saved-media')
elif sys.argv[1:] == ['diagnostics']:
    source=ROOT/'scripts/check_p1_diagnostics.py'
    code=source.read_text(encoding='utf8').replace("(OUT/'diagnostic-http.json')","(ROOT/'artifacts/local-private/p1-checkpoint-rename-20260920-v1/diagnostic-http.json')")
else:raise SystemExit('pytest | saved-media | diagnostics')
exec(compile(code,str(source),'exec'),{'__name__':'__main__','__file__':str(source)})
