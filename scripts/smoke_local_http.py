"""Local asset API checks on diagnostic fixtures. No model inference or external API."""
import hashlib,http.cookiejar,json,urllib.request,urllib.error,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=os.environ.get('JEV_TEST_BASE_URL','http://127.0.0.1:8876')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
token=None;checks=0
def call(path,body=None,headers=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json',**({'X-Session-Token':token} if token else {}),**(headers or {})})
    try:
        with opener.open(req,timeout=15) as response:return response.status,response.read()
    except urllib.error.HTTPError as error:return error.code,error.read()
def check(condition):
    global checks
    assert condition;checks+=1
status,data=call('/api/bootstrap');token=json.loads(data)['token'];check(status==200)
assets=[]
for name in ('synthetic.webm','local-diagnostic.mp4'):
    path=ROOT/'fixtures'/name
    with path.open('rb') as stream:
        req=urllib.request.Request(BASE+'/api/assets',data=stream,headers={'X-Session-Token':token,'Content-Type':'application/octet-stream','Content-Length':str(path.stat().st_size),'X-File-Name':name})
        with opener.open(req,timeout=15) as response:asset=json.load(response)
    assets.append(asset);check(asset['sha256']==hashlib.sha256(path.read_bytes()).hexdigest())
    status,data=call('/media/'+asset['id'],headers={'Range':'bytes=100-199'})
    check(status==206 and data==path.read_bytes()[100:200])
check(assets[0]['id']!=assets[1]['id'])
for asset in assets:
    status,data=call('/api/session',{'mode':'LOCAL','asset_id':asset['id'],'start_s':8});state=json.loads(data)
    check(status==200 and state['clock']['media_s']==8 and state['clock']['released']=={'audio':[],'video':[]})
    check(call('/api/assets/browser-ready',{'asset_id':asset['id'],'duration_s':asset['duration_s']})[0]==200)
    check(call('/api/observe',{'epoch':state['clock']['epoch'],'media_s':8,'jpeg':''})[0]==400)
    check(call('/api/assets/browser-ready',{'asset_id':asset['id'],'duration_s':9999})[0]==400)
check(call('/api/session',{'mode':'LOCAL','asset_id':'../.env'})[0]==400)
call('/api/session',{'mode':'MOCK'})
for asset in assets:check(call('/api/assets/delete',{'asset_id':asset['id']})[0]==200)
print(f'Local asset HTTP: {checks} checks passed; fixture bytes only; no external inference')
