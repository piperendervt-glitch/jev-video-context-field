"""One work allowance inside a locked campaign; no new credit or retry path."""
import hashlib
import json
import os
from pathlib import Path

LIMITS={'attempts':120,'units':360,'questions':1440,'chars':1440000}


class WorkBudget:
    def __init__(self,campaign,directory,work_id,manifest,*,limits=None):
        self.campaign=campaign
        self.limits=LIMITS if limits is None else dict(limits)
        self.path=Path(directory)/'work-state.json'
        if campaign.lock_file.closed:raise ValueError('campaign_lock_required')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.path.exists() or self.path.with_suffix('.tmp').exists():
            raise ValueError('work_exists_no_auto_resume_or_second_run')
        self.state={'work_id':work_id,'status':'prepared','limits':dict(self.limits),'baseline':campaign.summary(),
                    'manifest':manifest,'reservations':[],'run_id':None}
        self.save()

    def save(self):
        tmp=self.path.with_suffix('.tmp')
        with tmp.open('w',encoding='utf8') as f:
            json.dump(self.state,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,self.path)

    def start(self,run_id):
        with self.campaign.lock:
            if self.state['status']!='prepared' or self.state['run_id'] is not None:
                raise ValueError('one_run_only')
            self.state.update(status='running',run_id=run_id);self.save()

    def reserve(self,request):
        with self.campaign.lock:
            if self.campaign.lock_file.closed or self.state['status']!='running':raise ValueError('work_not_running')
            if any(x['status']=='intent' for x in self.state['reservations']):raise ValueError('ambiguous_intent_no_retry')
            p=json.loads(request.payload_json)
            allowed=self.state['manifest'].get('allowed_payload_hashes')
            if allowed is not None and hashlib.sha256(request.payload_json.encode()).hexdigest() not in allowed:raise ValueError('payload_not_in_fixed_manifest')
            increment={'attempts':1,'units':len(request.unit_ids),'questions':len(p['questions']),'chars':len(request.payload_json)}
            current=self.campaign.summary()
            if any(current[k]-self.state['baseline'][k]+increment[k]>self.limits[k] for k in self.limits):
                raise ValueError('work_budget_exhausted')
            intent={'status':'intent','request_sha256':hashlib.sha256(request.payload_json.encode()).hexdigest(),
                    'unit_ids':list(request.unit_ids),'question_ids':list(p['questions']),'global_before':current}
            self.state['reservations'].append(intent);self.save()
            try:
                value=self.campaign.reserve(request)
            except Exception:
                # Preserve ambiguous debit on storage failure; never refund or replay.
                self.state['status']='stopped';self.save();raise
            intent.update(status='reserved',reservation=value);self.save()
            return value

    def summary(self):return self.campaign.summary()

    def finish(self,reason,dispatch_log):
        with self.campaign.lock:
            self.state.update(status='finished',reason=reason,final=self.campaign.summary(),dispatcher=dispatch_log)
            self.save()
