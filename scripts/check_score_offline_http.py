"""Existing HTTP/Replay suites against an ephemeral, keyless, model-free server."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.offline_guard import install, ALLOWED_PORTS, COUNTS, ROOT
install()
import json
import os
import runpy
import shutil
import tempfile
import threading
from unittest.mock import patch
from context_fields import server
from context_fields.media import AssetStore


class NoInference:
    status='ready'
    def public(self):
        return {'status':'ready','models':{k:'OFFLINE-STUB-NO-INFERENCE' for k in ('SpeechASR','FrameInterpreter','TextContext')}}
    def submit(self,*args,**kwargs):
        raise AssertionError('Model inference forbidden in offline HTTP tests')


def main():
    task=(ROOT/'artifacts/score-contract-offline/LATEST.txt').read_text().strip()
    out=Path(os.environ['JEV_TEST_ARTIFACT_DIR']).resolve() if 'JEV_TEST_ARTIFACT_DIR' in os.environ else ROOT/'artifacts/score-contract-offline'/task
    if not out.is_relative_to(ROOT/'artifacts'):raise ValueError('test_artifact_scope')
    out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='offline-http-',dir=out) as td:
        temp=Path(td); logs=temp/'sessions';logs.mkdir()
        for run in ('run-1789827258430987000','run-1789824695716131300'):
            # Test fixture copies, outside the source-only backup, removed on completion.
            shutil.copyfile(ROOT/'artifacts/sessions'/(run+'.jsonl'),logs/(run+'.jsonl'))
        with patch.object(server,'AssetStore',lambda unused:AssetStore(temp/'assets')):
            srv=server.make_server(0,log_dir=logs,load_models=False,live_capability=None)
        srv.app.models=NoInference()
        assert srv.server_port != 8876 and srv.app.live_capability is None
        ALLOWED_PORTS.add(srv.server_port)
        worker=threading.Thread(target=srv.serve_forever,daemon=True);worker.start()
        env={'JEV_TEST_BASE_URL':f'http://127.0.0.1:{srv.server_port}',
             'JEV_TEST_ARTIFACT_DIR':str(out),'JEV_TEST_LOG_DIR':str(logs)}
        try:
            with patch.dict(os.environ,env):
                for name in ('smoke_http.py','smoke_local_http.py','smoke_m3a_http.py','check_m3a_saved_replay.py'):
                    runpy.run_path(str(ROOT/'scripts'/name),run_name='__main__')
            report={'result':'passed','server_port':srv.server_port,'live_capability':False,
                    'real_models_loaded':False,'real_key_read':False,'real_budget_reservations':0,
                    'existing_8876_mutations':0,'guard':COUNTS,'browser_operation':False}
            (out/'http-isolation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        finally:
            srv.shutdown();worker.join(5);srv.app.session.stop();srv.app.pool.close()
            if srv.app.session.local_pipeline:srv.app.session.local_pipeline.close()
            srv.server_close();ALLOWED_PORTS.discard(srv.server_port)
    print('Isolated HTTP/Replay complete; temporary test sessions/assets removed')


if __name__=='__main__':main()
