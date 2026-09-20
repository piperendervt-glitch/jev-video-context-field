"""New real diagnostic and isolated synthetic fixture through non-LIVE HTTP."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.offline_guard import install,ALLOWED_PORTS,COUNTS
install()
import json,time,threading,urllib.request
from urllib.parse import urlencode
from context_fields.debug_server import make_debug_server
OUT=ROOT/'artifacts/jev-parallel-p1/jev-parallel-p1-20260920-v1'
server=make_debug_server(0);ALLOWED_PORTS.add(server.server_port)
t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
def call(path,body=None):
    req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/debug/'+path,
        data=None if body is None else json.dumps(body).encode(),headers={'X-Session-Token':server.app.token,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=60) as r:return json.load(r)
results=[]
try:
    for name in ('measurement-result','fixture'):
        run=json.loads((OUT/(name+'.json')).read_text(encoding='utf8'))['run_id']
        call('index',{'run':run})
        while True:
            status=call('status?run='+run)
            if status['status']!='indexing':break
            time.sleep(.05)
        assert status['status']=='ready' and status['displays']==0,status
        assert status['activity']['mode']=='elapsed' and status['activity']['diagnostic_kind']
        asset=call('managed-asset',{'run':run})
        assert call('match?'+urlencode({'run':run,'asset_id':asset['id']}))['media_match']=='hash_verified'
        source=ROOT/'artifacts/sessions'/(run+'.jsonl')
        events=[json.loads(x) for x in source.read_text(encoding='utf8').splitlines()]
        stop=next(e for e in events if e['kind']=='jev_lifecycle' and e['payload'].get('hard_error'))
        before=call('evaluation-lists?'+urlencode({'run':run,'position':stop['elapsed_s']-.000001}))
        final=call('evaluation-lists?'+urlencode({'run':run,'position':status['activity']['duration']}))
        assert not before['evaluation_progress'].get('hard_error') and final['evaluation_progress']['hard_error']
        assert len({r['row_id'] for r in final['evaluations']['items']})==final['evaluations']['total']
        assert all('evaluation_state' in r for r in final['evaluations']['items'])
        thumbs=[]
        for row in final['evaluations']['items']:
            detail=call('activity-row?'+urlencode({'run':run,'cursor':final['cursor'],'row_id':row['row_id']}))
            assert detail['original_fields']
            if thumbs:continue
            ref=next((x for x in row['source_refs'] if x['modality']=='video'),None)
            if ref:
                im=call('thumbnail?'+urlencode({'run':run,'cursor':final['cursor'],'row_id':row['row_id'],'revision':row['revision'],
                    'ref_id':ref['ref_id'],'asset_id':asset['id'],'generation':'p1'}))
                assert im['state']=='ready' and im['decoded_pts']==ref['pts'],im
                thumbs.append({k:v for k,v in im.items() if k!='jpeg'})
        assert thumbs
        sample_times=(1.7,2.4,4.2,5.2,7.2,8.2) if name=='fixture' else tuple(sorted({e['elapsed_s'] for e in events if e['kind']=='jev_lifecycle'}))
        states=[]
        for at in sample_times:
            data=call('evaluation-lists?'+urlencode({'run':run,'position':at}))
            states.append({'at':at,'rows':[{'id':r['row_id'],'state':r['evaluation_state'],'result_seq':r['result_seq']} for r in data['evaluations']['items']]})
        results.append({'run_id':run,'kind':name,'events':len(events),'seconds':status['activity']['duration'],
            'display_snapshots':0,'rows':final['evaluations']['total'],'states':states,'thumbnails':thumbs,
            'hard_error_only_at_correct_cursor':True,'final_page':final})
finally:server.shutdown();server.server_close();t.join(5)
(OUT/'diagnostic-http.json').write_text(json.dumps({'runs':results,'guard':COUNTS,'real_browser':False},ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'runs':[{k:r[k] for k in ('run_id','kind','rows','seconds')} for r in results],'guard':COUNTS}))
