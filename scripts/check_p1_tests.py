"""Full regression with a single shared offline guard module instance."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.offline_guard import install, COUNTS, ROOT
install()
import json
import pytest
code = pytest.main(sys.argv[1:] or ['-q'])
out = ROOT / 'artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1'
out.mkdir(parents=True, exist_ok=True)
(out / 'pytest-isolation.json').write_text(json.dumps({'exit_code': int(code), 'guard': COUNTS}, indent=2), encoding='utf8')
raise SystemExit(code)
