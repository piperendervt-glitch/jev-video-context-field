"""Process-local fence: no production credentials/budget writes or remote sockets.

Install before importing application/test modules. Does not read environment variables.
Only a specifically selected ephemeral HTTP test port may be added by the harness.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ALLOWED_PORTS = set()
COUNTS = {'remote_connections': 0, 'credential_reads': 0, 'campaign_writes': 0,
          'loopback_connections': 0}
_installed = False


def install():
    global _installed
    if _installed:
        return
    _installed = True
    real_env = os.path.normcase(str(ROOT / '.env'))
    campaign = os.path.normcase(str(ROOT / 'artifacts/local-private/jev-campaign-v02'))

    def audit(event, args):
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))
            if path == real_env:
                COUNTS['credential_reads'] += 1
                raise PermissionError('offline_guard: production credentials forbidden')
            mode, flags = args[1:3]
            writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or bool(
                (flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if path.startswith(campaign) and writing:
                COUNTS['campaign_writes'] += 1
                raise PermissionError('offline_guard: production campaign writes forbidden')
        if event in ('os.rename', 'os.remove', 'os.rmdir'):
            for arg in args[:2] if event == 'os.rename' else args[:1]:
                if isinstance(arg, (str, bytes, os.PathLike)):
                    path = os.path.normcase(os.path.abspath(os.fsdecode(arg)))
                    if path == real_env or path.startswith(campaign):
                        raise PermissionError('offline_guard: protected path mutation')
        if event == 'socket.connect':
            address = args[1]
            if not (isinstance(address, tuple) and address[0] == '127.0.0.1'
                    and address[1] in ALLOWED_PORTS and address[1] != 8876):
                COUNTS['remote_connections'] += 1
                raise PermissionError('offline_guard: connection forbidden')
            COUNTS['loopback_connections'] += 1
    sys.addaudithook(audit)


if __name__ == '__main__':
    install()
    import pytest
    code=pytest.main(sys.argv[1:])
    directory=os.environ.get('JEV_TEST_ARTIFACT_DIR')
    if directory:
        import json
        target=Path(directory).resolve()
        if not target.is_relative_to(ROOT/'artifacts/score-contract-offline'):
            raise ValueError('offline_test_artifact_scope')
        (target/'pytest-isolation.json').write_text(json.dumps({'exit_code':int(code),'guard':COUNTS},indent=2),encoding='utf-8')
    raise SystemExit(code)
