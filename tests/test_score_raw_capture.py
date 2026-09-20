"""Fixed synthetic/fake HTTP tests. No actual credentials or external sockets."""
import copy
import hashlib
import json
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import http.client
import pytest
from context_fields.live import LiveCapability,CampaignBudget
from context_fields.response_archive import PrivateResponseArchive,allowed_response_headers
from context_fields.dispatcher import HttpTransport,Dispatcher
from context_fields.jev import DominanceRequest,mock_response
from context_fields.diagnostic_work import DiagnosticWork,DiagnosticBudget
from context_fields.score_diagnostics import analyze_body


def request():
    return DominanceRequest(json.dumps({'model':'jev-1.13.0','state':'synthetic only','questions':{
        'diagnostic.score':{'type':'score','criteria':['a','b','c','d','e']},
        'diagnostic.assessability':{'type':'choice','criteria':{'assessable':'yes','insufficient':'no','conflicting':'conflict'}}}}),('diagnostic',))


def body(score=2):
    req=request();a=mock_response(req)
    # Generic mock_response has no diagnostic score defaults; replace explicitly.
    a['model']='jev-1.13.0';a['answers']['diagnostic.score']={'type':'score','score':score,'confidence':.5,
        'legend':dict(zip(map(str,range(5)),['a','b','c','d','e'])),
        'probabilities':{'0':0.,'1':0.,'2':1.,'3':0.,'4':0.}}
    return json.dumps(a).encode()


class FakeResponse:
    def __init__(self,data,status=200,headers=None,failure=None):
        self.data=data;self.status=status;self.headers=Message();self.failure=failure;self.reads=0
        for k,v in (headers or [('content-length',str(len(data))),('x-typesafe-request-id','dummy-request-id')]):self.headers[k]=v
    def read1(self,n):
        self.reads+=1
        if self.failure and self.reads==2:raise self.failure
        chunk=self.data[:min(n,7)];self.data=self.data[len(chunk):];return chunk


class FakeConnection:
    def __init__(self,response):self.response=response;self.calls=0;self.closed=False
    def request(self,*a,**kw):self.calls+=1
    def getresponse(self):return self.response
    def close(self):self.closed=True


@pytest.mark.parametrize('kind',['valid','mismatch','invalid-json','duplicate-key','numeric-lexemes','http-400','http-500','partial','oversize','save-failure'])
def test_capture_before_validation_and_failure_stop(tmp_path,kind):
    data=body(2.01 if kind=='mismatch' else 2);status=200;failure=None
    if kind=='invalid-json':data=b'{invalid'
    if kind=='duplicate-key':data=data.replace(b'"score": 2',b'"score": 1, "score": 2')
    if kind=='numeric-lexemes':data=data.replace(b'"score": 2',b'"score": 2.0000').replace(b'"2": 1.0',b'"2": 1e0')
    if kind.startswith('http-'):status=int(kind[5:])
    if kind=='partial':failure=http.client.IncompleteRead(b'partial',99)
    if kind=='oversize':data=b'x'*(1024*1024+1)
    response=FakeResponse(data,status,failure=failure)
    if kind=='oversize':response.read1=lambda n:data # bounded to exactly sentinel limit in this fake
    connection=FakeConnection(response);archive=PrivateResponseArchive(tmp_path/'private')
    if kind=='save-failure':archive.capture=lambda *a,**k:(_ for _ in ()).throw(OSError('dummy storage failure'))
    transport=HttpTransport('DUMMY_SECRET',authorized=True,response_archive=archive,connection_factory=lambda *a,**k:connection)
    dispatcher=Dispatcher(transport);req=request();analysis=[]
    def callback(raw,a,b):
        result=analyze_body((archive.directory/transport.last_capture['body_ref']).read_bytes(),json.loads(req.payload_json));analysis.append(result)
        if result['classification']!='NO_MISMATCH_IN_BOUNDED_SAMPLE':raise ValueError('diagnostic_rejected')
    # Use real monotonic time for the capture deadline; fake connection never opens sockets.
    import time
    now=time.monotonic();dispatcher.submit(req,now,'test',callback);dispatcher.run_one(now,time.monotonic)
    assert connection.calls==1 and connection.closed and dispatcher.budget.attempts==1
    good=kind in ('valid','numeric-lexemes')
    assert dispatcher.stopped!=good
    if kind=='save-failure':assert not transport.last_capture;return
    meta=transport.last_capture;captured=(archive.directory/meta['body_ref']).read_bytes()
    assert meta['body_sha256']==hashlib.sha256(captured).hexdigest()
    assert meta['request_context']['attempt']==1 and meta['request_context']['unit_ids']==['diagnostic']
    assert meta['request_sha256']==hashlib.sha256(req.payload_json.encode()).hexdigest()
    assert meta['http_request_id']=='dummy-request-id' and 'DUMMY_SECRET' not in json.dumps(meta)
    if kind=='partial':assert captured==data[:7]+b'partial' and not meta['complete']
    else:assert captured==data
    if kind=='mismatch':assert analysis[0]['classification']=='RAW_NUMERIC_MISMATCH_OBSERVED'
    if kind=='numeric-lexemes':assert {'2.0000','1e0'}<=set(analysis[0]['numeric_lexemes'])
    if kind=='duplicate-key':assert analysis[0]['duplicate_keys']==['score']


@pytest.mark.parametrize('values,reason',[(['normal-id'],None),([], 'missing'),(['one','two'],'duplicate'),(['x'*257],'invalid_length_or_control'),(['bad\nvalue'],'invalid_length_or_control')])
def test_request_id_allowlist(values,reason):
    h=Message()
    for v in values:h['x-typesafe-request-id']=v
    h['Authorization']='DUMMY_SECRET';h['Set-Cookie']='DUMMY_COOKIE'
    selected,reasons=allowed_response_headers(SimpleNamespace(headers=h))
    assert reasons['x-typesafe-request-id']==reason
    assert 'DUMMY' not in json.dumps(selected)
    assert selected['x-typesafe-request-id']==('normal-id' if reason is None else None)


def test_production_factory_default_off_and_explicit_on(tmp_path):
    key=tmp_path/'dummy.env';key.write_text('JEV_API_KEY=DUMMY_SECRET\n')
    cap=LiveCapability(approved=True,phase='demo',key_file=key,campaign_file=tmp_path/'campaign.json')
    try:
        assert cap.transport().response_archive is None
        conn=FakeConnection(FakeResponse(body()))
        t=cap.transport(capture_directory=tmp_path/'responses',work_id='test',connection_factory=lambda *a,**k:conn)
        t.send(request(),5)
        assert t.last_capture['work_id']=='test' and t.last_capture['complete']
        with pytest.raises(ValueError,match='private'):cap.transport(capture_directory=tmp_path.parent/'not-private')
    finally:cap.close()


def test_nested_reservation_single_writer_concurrency_and_restart(tmp_path):
    req=request();h=hashlib.sha256(req.payload_json.encode()).hexdigest()
    campaign=CampaignBudget(tmp_path/'campaign.json','demo');work=DiagnosticWork(tmp_path/'work','test',[h,h,h])
    try:
        with pytest.raises(ValueError,match='already_open'):CampaignBudget(tmp_path/'campaign.json','demo')
        with pytest.raises(ValueError,match='already_open'):DiagnosticWork(tmp_path/'work','test',[h,h,h])
        def reserve():
            try:return work.reserve(campaign,req)
            except ValueError:return None
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:reserve(),range(2)))
        assert sum(r is not None for r in results)==1 and campaign.attempts==1
        work.result({'classification':'NO_MISMATCH_IN_BOUNDED_SAMPLE'},True)
        for _ in range(2):work.reserve(campaign,req);work.result({'classification':'NO_MISMATCH_IN_BOUNDED_SAMPLE'},True)
        with pytest.raises(ValueError,match='cap'):work.reserve(campaign,req)
        assert campaign.attempts==campaign.units==3
        work.stop('max_three')
    finally:work.close();campaign.close()
    with pytest.raises(ValueError,match='no_auto_resume'):DiagnosticWork(tmp_path/'work','test',[h,h,h])
    reopened=CampaignBudget(tmp_path/'campaign.json','demo')
    try:assert reopened.attempts==3
    finally:reopened.close()


def test_first_failure_stops_and_preflight_precedes_key(tmp_path):
    req=request();h=hashlib.sha256(req.payload_json.encode()).hexdigest()
    campaign=CampaignBudget(tmp_path/'campaign.json','demo');work=DiagnosticWork(tmp_path/'work','test',[h]*3)
    try:
        work.reserve(campaign,req);work.result({'classification':'RAW_NUMERIC_MISMATCH_OBSERVED'},False)
        with pytest.raises(ValueError,match='stopped'):work.reserve(campaign,req)
        assert campaign.attempts==1
    finally:work.close();campaign.close()
    cap=LiveCapability(approved=True,phase='demo',key_file=tmp_path/'MUST_NOT_BE_READ',campaign_file=tmp_path/'other.json')
    try:
        with pytest.raises(ValueError,match='private'):cap.transport(capture_directory=tmp_path.parent/'outside')
    finally:cap.close()


@pytest.mark.parametrize('scenario',['all-match','first-mismatch','campaign-locked'])
def test_runner_fixed_payload_order_and_stop_persist(tmp_path,scenario):
    import shutil
    from scripts.capture_score_raw import run,ROOT,WORK_ID
    directory=tmp_path/'private';directory.mkdir()
    for i in range(1,4):
        shutil.copyfile(ROOT/'artifacts/local-private'/WORK_ID/f'case_{i:02}.request.json',directory/f'case_{i:02}.request.json')
    key=tmp_path/'dummy.env';key.write_text('JEV_API_KEY=DUMMY_SECRET')
    sent=[]
    class Connection:
        sock=None
        def __init__(self,*a,**k):pass
        def request(self,method,url,body,headers):
            sent.append(body);self.payload=json.loads(body)
        def getresponse(self):
            req=DominanceRequest(json.dumps(self.payload),('diagnostic',));raw=mock_response(req);raw['model']='jev-1.13.0'
            if scenario=='first-mismatch':
                score=next(v for v in raw['answers'].values() if v['type']=='score');score['score']+=.01
            return FakeResponse(json.dumps(raw).encode())
        def close(self):pass
    class Capability(LiveCapability):
        def transport(self,**kwargs):return super().transport(connection_factory=Connection,**kwargs)
    def factory():
        if scenario=='campaign-locked':raise ValueError('campaign_already_open')
        return Capability(approved=True,phase='demo',key_file=key,campaign_file=tmp_path/'campaign.json')
    r=run(directory,tmp_path/'report',factory)
    expected={'all-match':3,'first-mismatch':1,'campaign-locked':0}[scenario]
    assert len(sent)==len(r['work_state']['reservations'])==expected
    for i,data in enumerate(sent,1):assert data==(directory/f'case_{i:02}.request.json').read_bytes()
    assert r['classification']=={'all-match':'NO_MISMATCH_IN_BOUNDED_SAMPLE','first-mismatch':'RAW_NUMERIC_MISMATCH_OBSERVED','campaign-locked':'BLOCKED_BEFORE_SEND'}[scenario]
    assert r['work_state']['status']=='stopped'
    if expected==3:
        starts=[s['dispatcher']['sent_wall_s'] for s in r['samples']]
        assert all(b-a>=2 for a,b in zip(starts,starts[1:]))
    with pytest.raises(ValueError,match='no_auto_resume'):run(directory,tmp_path/'report2',factory)
    assert len(sent)==expected


def test_real_http_parser_duplicate_headers_without_socket(tmp_path):
    import io
    # Exercise stdlib HTTP parsing without network access or a second client path.
    data=body();wire=(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(data)).encode()+
        b'\r\nx-typesafe-request-id: one\r\nx-typesafe-request-id: two\r\nSet-Cookie: DUMMY_COOKIE\r\n\r\n'+data)
    response=http.client.HTTPResponse(SimpleNamespace(makefile=lambda *a,**k:io.BytesIO(wire)));response.begin()
    conn=FakeConnection(response);archive=PrivateResponseArchive(tmp_path)
    transport=HttpTransport('DUMMY_KEY',authorized=True,response_archive=archive,connection_factory=lambda *a,**k:conn)
    with pytest.raises(ValueError,match='incomplete_capture'):transport.send(request(),5)
    meta=transport.last_capture
    assert (tmp_path/meta['body_ref']).read_bytes()==data and meta['request_id_reason']=='duplicate'
    assert 'DUMMY_COOKIE' not in json.dumps(meta)
