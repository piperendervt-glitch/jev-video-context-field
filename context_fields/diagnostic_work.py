"""Single-writer, persistent work cap nested inside the existing campaign.

Write-ahead intent is conservative: interrupted or failed runs never auto-resume.
The owning CampaignBudget's exclusive process lock is required before reserve.
"""
import hashlib
import json
import os
from pathlib import Path
import threading
import copy
from datetime import datetime, timezone


class DiagnosticWork:
    def __init__(self,directory,work_id,payload_hashes,*,inspect_for_resume=False):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'work-state.json';self.lock=threading.RLock()
        self.lock_file=(self.directory/'work.lock').open('a+b')
        self.lock_file.seek(0,2)
        if self.lock_file.tell()==0:self.lock_file.write(b'0');self.lock_file.flush()
        self.lock_file.seek(0)
        import msvcrt
        try:msvcrt.locking(self.lock_file.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:self.lock_file.close();raise ValueError('diagnostic_work_already_open')
        if self.path.exists():
            self.state=json.loads(self.path.read_text(encoding='utf-8'))
            if self.state['work_id']!=work_id or self.state['payload_hashes']!=payload_hashes:
                self.close();raise ValueError('diagnostic_identity_changed')
            if (self.state['status']!='prepared' or self.state.get('resume_events')) and not inspect_for_resume:
                self.close();raise ValueError('diagnostic_terminal_or_interrupted_no_auto_resume')
        else:
            if inspect_for_resume:
                self.close();raise ValueError('diagnostic_resume_requires_existing_state')
            self.state={'work_id':work_id,'payload_hashes':payload_hashes,'status':'prepared','reservations':[],
                        'limits':{'attempts':3,'units':3,'questions':6,'chars':36000},'results':[]}
            self.save()

    def resume_blocked(self,campaign,*,expected_state_sha256,previous_execution,previous_budget):
        """One explicit CAS transition, only for a proven zero-debit pre-send stop.

        Holds both existing OS locks. Any ambiguous intent, changed campaign, raw
        artifact, prior resume or version conflict fails closed without changing state.
        """
        with self.lock,campaign.lock:
            if self.lock_file.closed or campaign.lock_file.closed:
                raise ValueError('resume_exclusive_ownership_required')
            original=self.path.read_bytes()
            if hashlib.sha256(original).hexdigest()!=expected_state_sha256 or json.loads(original)!=self.state:
                raise ValueError('resume_state_version_conflict')
            old=self.state
            if (old.get('status')!='stopped' or old.get('stop_reason')!='BLOCKED_BEFORE_SEND'
                    or old.get('reservations')!=[] or old.get('results')!=[]
                    or old.get('resume_events') or old.get('revision',0)!=0
                    or old.get('limits')!={'attempts':3,'units':3,'questions':6,'chars':36000}
                    or self.path.with_suffix('.tmp').exists()):
                raise ValueError('resume_not_proven_unsent_or_already_resumed')
            if set(old)!={'work_id','payload_hashes','status','reservations','limits','results','stop_reason'}:
                raise ValueError('resume_unknown_state_fields')
            response_dir=self.directory/'responses'
            if response_dir.exists() and any(response_dir.iterdir()):
                raise ValueError('resume_existing_raw_or_unknown_capture')
            execution_bytes=Path(previous_execution).read_bytes()
            execution=json.loads(execution_bytes)
            budget_bytes=Path(previous_budget).read_bytes()
            baseline=json.loads(budget_bytes)
            if (execution.get('work_id')!=old['work_id'] or execution.get('work_state')!=old
                    or execution.get('classification')!='BLOCKED_BEFORE_SEND'
                    or execution.get('samples')!=[] or execution.get('real_key_read') is not False
                    or execution.get('failure_code')!='campaign_already_open'
                    or execution.get('request_hashes')!=old['payload_hashes']):
                raise ValueError('resume_previous_execution_not_proven_unsent')
            # Campaign has durable cumulative counters, not a per-attempt journal.
            # Equality with the original post-stop baseline is deliberately strict.
            if baseline!=campaign.summary():raise ValueError('resume_campaign_changed_requires_review')
            next_state=copy.deepcopy(old)
            next_state.update(status='prepared',revision=1,resume_events=[{
                'event':'explicit_resume','utc':datetime.now(timezone.utc).isoformat(),
                'reason':'user_authorized_graceful_shutdown_and_same_work_resume',
                'previous_state':copy.deepcopy(old),'previous_state_sha256':expected_state_sha256,
                'previous_execution_sha256':hashlib.sha256(execution_bytes).hexdigest(),
                'previous_budget_sha256':hashlib.sha256(budget_bytes).hexdigest(),
                'campaign_before':campaign.summary(),'campaign_exclusive_lock':True,
                'raw_files_before':0,'from_revision':0,'to_revision':1}])
            if self.path.read_bytes()!=original:raise ValueError('resume_state_version_conflict')
            self.state=next_state
            try:self.save()
            except Exception:
                self.state=old
                raise
            return copy.deepcopy(next_state['resume_events'][-1])

    def save(self):
        tmp=self.path.with_suffix('.tmp')
        with tmp.open('w',encoding='utf-8') as f:
            json.dump(self.state,f,ensure_ascii=False,indent=2,default=str);f.flush();os.fsync(f.fileno())
        os.replace(tmp,self.path)

    def reserve(self,campaign,request):
        with self.lock,campaign.lock:
            if campaign.lock_file.closed:raise ValueError('campaign_lock_required')
            if self.state['status'] not in ('prepared','running'):raise ValueError('diagnostic_stopped')
            n=len(self.state['reservations'])
            if n>=3 or (n and self.state['reservations'][-1]['status']!='complete'):raise ValueError('diagnostic_cap_or_inflight')
            h=hashlib.sha256(request.payload_json.encode()).hexdigest();p=json.loads(request.payload_json)
            if h!=self.state['payload_hashes'][n] or request.role!='dominance' or len(request.unit_ids)!=1 or len(p['questions'])!=2:
                raise ValueError('diagnostic_fixed_payload')
            if len(request.payload_json)>12000 or p['model']!='jev-1.13.0':raise ValueError('diagnostic_request_limit')
            intent={'index':n,'request_sha256':h,'status':'intent','global_before':campaign.summary()}
            self.state['reservations'].append(intent);self.state['status']='running';self.save()
            # Exclusive campaign writer + persisted intent: crash at any boundary cannot reissue.
            reservation=campaign.reserve(request)
            intent.update(status='reserved',reservation=reservation);self.save()
            return reservation

    def result(self,value,continue_allowed):
        with self.lock:
            self.state['results'].append(value)
            if self.state['reservations']:self.state['reservations'][-1]['status']='complete'
            if not continue_allowed:self.state['status']='stopped';self.state['stop_reason']=value['classification']
            self.save()

    def stop(self,reason):
        self.state['status']='stopped';self.state['stop_reason']=reason;self.save()

    def close(self):
        if not self.lock_file.closed:
            import msvcrt
            self.lock_file.seek(0);msvcrt.locking(self.lock_file.fileno(),msvcrt.LK_UNLCK,1);self.lock_file.close()


class DiagnosticBudget:
    def __init__(self,campaign,work):self.campaign,self.work=campaign,work
    def reserve(self,request):return self.work.reserve(self.campaign,request)
    def summary(self):return self.campaign.summary()
