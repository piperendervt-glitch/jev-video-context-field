"""Loopback-only HTTP integration, including origin/token/range rejection."""
import json
import os
import urllib.request
import urllib.error
import http.cookiejar

BASE = os.environ.get('JEV_TEST_BASE_URL', "http://127.0.0.1:8876")

def main():
    jar=http.cookiejar.CookieJar()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar))
    def call(path,body=None,token=None,headers=None):
        h={"Content-Type":"application/json",**(headers or {})}
        if token:h["X-Session-Token"]=token
        req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        try:
            with opener.open(req,timeout=5) as r:return r.status,r.read(),dict(r.headers)
        except urllib.error.HTTPError as e:return e.code,e.read(),dict(e.headers)
    assert call("/api/snapshot")[0]==403
    assert call("/api/bootstrap",headers={"Origin":"https://example.invalid"})[0]==403
    status,data,_=call("/api/bootstrap");assert status==200;token=json.loads(data)["token"]
    assert call("/media/../../.env")[0]==404
    status,data,headers=call("/media/fixture",headers={"Range":"bytes=0-31"})
    assert status==206 and len(data)==32 and headers["Content-Range"].startswith("bytes 0-31/")
    assert call("/media/fixture",headers={"Range":"bytes=99999999-"})[0]==416
    assert call("/api/control",{"epoch":1,"action":"stop"},"wrong")[0]==403
    status,data,_=call("/api/session",{"mode":"MOCK"},token);assert status==200
    epoch=json.loads(data)["clock"]["epoch"]
    for i in range(5):
        status,data,_=call("/api/clock",{"epoch":epoch,"seq":i,"media_s":i*.1,"video_presented_s":i*.1,"audio_presented_s":i*.1,"state":"playing"},token)
        assert status==200,(status,data)
    status,data,_=call("/api/clock",{"epoch":epoch,"seq":5,"media_s":.4,"video_presented_s":.4,"audio_presented_s":.4,"state":"paused"},token)
    assert status==200 and json.loads(data)["clock"]["state"]=="paused"
    status,data,_=call("/api/control",{"epoch":epoch,"action":"seek","target":50},token)
    assert status==200 and json.loads(data)["clock"]["released"]=={"audio":[],"video":[]}
    status,data,_=call("/api/replay",{"capture":True},token)
    assert status==200 and json.loads(data)["count"]>0
    assert call("/api/export",token=token)[0]==200
    call("/api/session",{"mode":"MOCK"},token)
    print("HTTP smoke: 14 checks passed; localhost only; new external sends in this test 0")

if __name__=="__main__":main()
