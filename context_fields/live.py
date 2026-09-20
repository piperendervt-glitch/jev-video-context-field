"""Explicit backend capability and durable, shared campaign budget. No auto enable."""
import json
import os
from pathlib import Path
from .dispatcher import Budget, HttpTransport

ROOT=Path(__file__).resolve().parents[1]
KEY_FILE=ROOT/'.env'
CAMPAIGN_FILE=ROOT/'artifacts/local-private/jev-campaign-v02.json'

class CampaignBudget(Budget):
    def __init__(self,path=CAMPAIGN_FILE,phase='initial'):
        super().__init__()
        if phase not in {'initial','demo'}:raise ValueError('invalid_campaign_phase')
        self.path=Path(path);self.phase=phase
        self.path.parent.mkdir(parents=True,exist_ok=True)
        # Exclusive process lock prevents two servers independently spending a cap.
        self.lock_file=self.path.with_suffix('.lock').open('a+b')
        self.lock_file.seek(0,2)
        if self.lock_file.tell()==0:self.lock_file.write(b'0');self.lock_file.flush()
        self.lock_file.seek(0)
        import msvcrt
        try:msvcrt.locking(self.lock_file.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:self.lock_file.close();raise ValueError('campaign_already_open')
        if self.path.exists():
            data=json.loads(self.path.read_text(encoding='utf-8'))
            for key in ('attempts','units','questions','chars','bytes'):setattr(self,key,data[key])
        else:self._save()

    def _save(self):
        tmp=self.path.with_suffix('.tmp')
        with tmp.open('w',encoding='utf-8') as file:
            json.dump(self.summary(),file);file.flush();os.fsync(file.fileno())
        os.replace(tmp,self.path)

    def reserve(self,request):
        with self.lock:
            if self.phase=='initial' and (self.attempts+1>20 or self.units+len(request.unit_ids)>20):
                raise ValueError('initial_connection_cap_exhausted')
            reservation=super().reserve(request)
            self._save() # Durable debit BEFORE transmission; never refund a failure.
            return reservation

    def close(self):
        if not self.lock_file.closed:
            import msvcrt
            self.lock_file.seek(0);msvcrt.locking(self.lock_file.fileno(),msvcrt.LK_UNLCK,1);self.lock_file.close()

class LiveCapability:
    def __init__(self,*,approved=False,phase='initial',key_file=KEY_FILE,campaign_file=CAMPAIGN_FILE):
        if not approved:raise PermissionError('live_not_authorized')
        self.key_file=Path(key_file)
        self.key_read_attempted=False;self.key_file_read=False
        self.budget=CampaignBudget(campaign_file,phase)

    def transport(self,*,capture_directory=None,work_id=None,connection_factory=None):
        archive=None
        if capture_directory is not None:
            from .response_archive import PrivateResponseArchive
            directory=Path(capture_directory).resolve()
            if not directory.is_relative_to(self.budget.path.parent.resolve()):
                raise ValueError('capture_must_be_campaign_private_directory')
            archive=PrivateResponseArchive(directory,work_id=work_id)
            archive.preflight() # Fail before credentials are read.
        # The one explicitly approved file only. No environment scan or shell eval.
        values={}
        self.key_read_attempted=True
        lines=self.key_file.read_text(encoding='utf-8-sig').splitlines()
        self.key_file_read=True
        for line in lines:
            key,sep,value=line.strip().partition('=')
            if sep and key in {'JEV_API_KEY','TYPESAFE_API_KEY'}:
                value=value.strip().strip('"').strip("'")
                if value:values[key]=value
        if len(set(values.values()))!=1:raise ValueError('key_missing_or_ambiguous')
        kwargs={'response_archive':archive}
        if connection_factory is not None:kwargs['connection_factory']=connection_factory
        return HttpTransport(next(iter(values.values())),authorized=True,**kwargs)

    def close(self):self.budget.close()
