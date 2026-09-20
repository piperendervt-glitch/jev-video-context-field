import copy
import hashlib
import json
import shutil
from email.message import Message

import pytest

from context_fields.diagnostic_work import DiagnosticWork
from context_fields.live import CampaignBudget, LiveCapability
from context_fields.jev import DominanceRequest, mock_response
from scripts.capture_score_raw import ROOT, WORK_ID, HASHES, run


def prepared_stop(tmp_path):
    directory=tmp_path/'private';directory.mkdir()
    for i in range(1,4):
        shutil.copyfile(ROOT/'artifacts/local-private'/WORK_ID/f'case_{i:02}.request.json',directory/f'case_{i:02}.request.json')
    campaign=CampaignBudget(tmp_path/'campaign.json','demo')
    baseline=campaign.summary();campaign.close()
    def locked():raise ValueError('campaign_already_open')
    previous=tmp_path/'previous'
    run(directory,previous,locked)
    (previous/'budget-after.json').write_text(json.dumps(baseline))
    state=(directory/'work-state.json').read_bytes()
    resume={'expected_state_sha256':hashlib.sha256(state).hexdigest(),
            'previous_execution':previous/'execution.json','previous_budget':previous/'budget-after.json'}
    return directory,resume,state


@pytest.mark.parametrize('mismatch',[False,True])
def test_explicit_resume_same_work_factory_caps_and_immutable_history(tmp_path,mismatch):
    directory,resume,old_bytes=prepared_stop(tmp_path)
    original_execution=resume['previous_execution'].read_bytes()
    key=tmp_path/'dummy.env';key.write_text('JEV_API_KEY=DUMMY_SECRET')
    sent=[]
    class Connection:
        sock=None
        def __init__(self,*args,**kwargs):pass
        def request(self,method,url,body,headers):sent.append(body);self.payload=json.loads(body)
        def getresponse(self):
            value=mock_response(DominanceRequest(json.dumps(self.payload),('diagnostic',)));value['model']='jev-1.13.0'
            if mismatch:next(a for a in value['answers'].values() if a['type']=='score')['score']+=.01
            self.data=json.dumps(value).encode();self.status=200;self.headers=Message()
            self.headers['Content-Length']=str(len(self.data));self.headers['x-typesafe-request-id']='dummy-id'
            return self
        def read1(self,n):chunk=self.data[:n];self.data=self.data[n:];return chunk
        def close(self):pass
    class Capability(LiveCapability):
        def transport(self,**kwargs):return super().transport(connection_factory=Connection,**kwargs)
    def factory():return Capability(approved=True,phase='demo',key_file=key,campaign_file=tmp_path/'campaign.json')
    report=run(directory,tmp_path/'resumed',factory,resume=resume)
    count=1 if mismatch else 3
    assert len(sent)==len(report['work_state']['reservations'])==count
    assert report['budget_after']['attempts']==report['budget_after']['units']==count
    assert report['budget_after']['questions']==2*count
    assert report['resume_event']['previous_state']==json.loads(old_bytes)
    assert len(report['work_state']['resume_events'])==1
    assert report['work_state']['limits']==json.loads(old_bytes)['limits']
    assert original_execution==resume['previous_execution'].read_bytes()
    for i,data in enumerate(sent,1):assert data==(directory/f'case_{i:02}.request.json').read_bytes()
    assert report['classification']==('RAW_NUMERIC_MISMATCH_OBSERVED' if mismatch else 'NO_MISMATCH_IN_BOUNDED_SAMPLE')
    state_after=(directory/'work-state.json').read_bytes()
    refused=run(directory,tmp_path/'duplicate',factory,resume=resume)
    assert refused['classification']=='RESUME_REJECTED_BEFORE_SEND'
    assert (directory/'work-state.json').read_bytes()==state_after and len(sent)==count
    assert not refused['credential_read_attempted']
    with pytest.raises(ValueError,match='execution_record_already_exists'):
        run(directory,tmp_path/'resumed',factory,resume=resume)


@pytest.mark.parametrize('case',['intent','consumed','raw','other-stop','mismatch-stop','version',
    'prior-execution','prior-budget','campaign-changed','interrupted-resume','unknown-field','pending-tmp'])
def test_resume_rejects_ambiguity_before_key_and_preserves_state(tmp_path,case):
    directory,resume,old_bytes=prepared_stop(tmp_path)
    state=json.loads(old_bytes)
    if case=='intent':state['reservations']=[{'status':'intent'}]
    if case=='consumed':state['results']=[{'classification':'NO_MISMATCH_IN_BOUNDED_SAMPLE'}]
    if case=='other-stop':state['stop_reason']='INCOMPLETE_CAPTURE_OR_TRANSPORT_FAILURE'
    if case=='mismatch-stop':state['stop_reason']='RAW_NUMERIC_MISMATCH_OBSERVED'
    if case=='interrupted-resume':state.update(status='prepared',resume_events=[{}],revision=1)
    if case=='unknown-field':state['unknown_intent']=True
    if state!=json.loads(old_bytes):(directory/'work-state.json').write_text(json.dumps(state))
    resume['expected_state_sha256']=hashlib.sha256((directory/'work-state.json').read_bytes()).hexdigest()
    if case=='version':resume['expected_state_sha256']='0'*64
    if case=='raw':(directory/'responses'/'unknown.body').write_bytes(b'{}')
    if case=='pending-tmp':(directory/'work-state.tmp').write_text('{}')
    if case=='prior-execution':resume['previous_execution'].write_text('{}')
    if case=='prior-budget':resume['previous_budget'].write_text('{}')
    if case=='campaign-changed':
        c=CampaignBudget(tmp_path/'campaign.json','demo')
        c.reserve(DominanceRequest((directory/'case_01.request.json').read_text(),('diagnostic',)));c.close()
    before=(directory/'work-state.json').read_bytes()
    def factory():return LiveCapability(approved=True,phase='demo',key_file=tmp_path/'MUST_NOT_READ',campaign_file=tmp_path/'campaign.json')
    report=run(directory,tmp_path/'rejected',factory,resume=resume)
    assert report['classification']=='RESUME_REJECTED_BEFORE_SEND'
    assert not report['credential_read_attempted'] and report['samples']==[]
    assert (directory/'work-state.json').read_bytes()==before


def test_resume_cas_unique_and_crash_does_not_allow_implicit_continuation(tmp_path):
    directory,resume,_=prepared_stop(tmp_path)
    campaign=CampaignBudget(tmp_path/'campaign.json','demo')
    work=DiagnosticWork(directory,WORK_ID,HASHES,inspect_for_resume=True)
    try:
        with pytest.raises(ValueError,match='already_open'):
            DiagnosticWork(directory,WORK_ID,HASHES,inspect_for_resume=True)
        work.resume_blocked(campaign,**resume)
        state=copy.deepcopy(work.state)
        with pytest.raises(ValueError,match='version_conflict'):work.resume_blocked(campaign,**resume)
        assert work.state==state and campaign.attempts==0
    finally:work.close();campaign.close()
    with pytest.raises(ValueError,match='no_auto_resume'):DiagnosticWork(directory,WORK_ID,HASHES)


def test_resume_does_not_create_missing_state(tmp_path):
    with pytest.raises(ValueError,match='requires_existing_state'):
        DiagnosticWork(tmp_path/'empty',WORK_ID,HASHES,inspect_for_resume=True)
    assert not (tmp_path/'empty/work-state.json').exists()
