"""Opt-in private HTTP body capture, before JSON parsing; never served by Viewer.

No network or credentials loader. The caller must explicitly provide a private
directory. No request headers, request bodies, cookies or keys are accepted here.
Historical application JSON must never be imported as a captured HTTP body.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid


HEADER_NAMES=('x-typesafe-request-id','content-type','content-encoding','content-length')


def allowed_response_headers(response):
    """Read only four named headers; preserve neither invalid values nor secrets."""
    result={};reasons={}
    headers=getattr(response,'headers',None)
    for name in HEADER_NAMES:
        values=headers.get_all(name,[]) if headers is not None else []
        if not values:result[name]=None;reasons[name]='missing'
        elif len(values)!=1:result[name]=None;reasons[name]='duplicate'
        elif not isinstance(values[0],str) or len(values[0])>256 or any(ord(c)<32 or ord(c)==127 for c in values[0]):
            result[name]=None;reasons[name]='invalid_length_or_control'
        else:result[name]=values[0];reasons[name]=None
    return result,reasons


class PrivateResponseArchive:
    def __init__(self, directory, work_id=None):
        self.directory = Path(directory)
        self.work_id=work_id

    def preflight(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        if self.directory.is_symlink():raise ValueError('archive_symlink')
        free=shutil.disk_usage(self.directory).free
        if free<8*1024*1024:raise ValueError('archive_disk_space')
        probe=self.directory/(uuid.uuid4().hex+'.probe')
        try:
            with probe.open('xb') as stream:
                stream.write(b'private-capture-write-read-check');stream.flush();os.fsync(stream.fileno())
            if probe.read_bytes()!=b'private-capture-write-read-check':raise ValueError('archive_readback')
        finally:
            if probe.exists():probe.unlink()
        return {'directory':str(self.directory.resolve()),'free_bytes':free,'write_read_fsync':True}

    def capture(self, body, *, status, request_hash, truncated=False, complete=True,
                headers=None, header_reasons=None, context=None, timings=None, failure=None):
        save_started=time.monotonic()
        if not isinstance(body, bytes):
            raise TypeError('body_bytes_required')
        if len(body) > 1024*1024+1:
            raise ValueError('archive_body_size')
        self.directory.mkdir(parents=True, exist_ok=True)
        capture_id = uuid.uuid4().hex
        body_name = capture_id + '.body'
        complete=bool(complete and not truncated and failure is None)
        metadata = {'capture_id':capture_id,'stage':'http_body_before_json_decode',
            'body_definition':'HTTPResponse.read/read1 body bytes; not TLS or transfer framing',
            'body_ref':body_name,'body_sha256':hashlib.sha256(body).hexdigest(),
            'hash_scope':'complete_body' if complete else 'captured_prefix',
            'body_bytes':len(body),'http_status':int(status) if status is not None else None,
            'request_sha256':request_hash,'captured_wall_ns':time.time_ns(),
            'truncated':bool(truncated),'complete':complete,'capture_failure':failure,
            'http_request_id':(headers or {}).get('x-typesafe-request-id'),
            'request_id_reason':(header_reasons or {}).get('x-typesafe-request-id','not_collected'),
            'response_headers':{k:(headers or {}).get(k) for k in HEADER_NAMES},
            'header_reasons':{k:(header_reasons or {}).get(k) for k in HEADER_NAMES},
            'privacy':'private','work_id':self.work_id,
            'request_context':{k:v for k,v in (context or {}).items() if k in (
                'attempt','unit_ids','question_ids','requested_model','accepted_wall_s','sent_wall_s')},
            'timings':timings or {}}
        metadata['timings']['save_started_wall_s']=save_started
        # Exclusive creation prevents overwriting a capture, including retries.
        with (self.directory/body_name).open('xb') as stream:
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        if hashlib.sha256((self.directory/body_name).read_bytes()).hexdigest()!=metadata['body_sha256']:
            raise OSError('archive_readback_hash')
        metadata['timings']['saved_wall_s']=time.monotonic()
        with (self.directory/(capture_id+'.json')).open('x',encoding='utf-8') as stream:
            json.dump(metadata,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        saved=json.loads((self.directory/(capture_id+'.json')).read_text(encoding='utf-8'))
        if saved!=metadata:raise OSError('archive_metadata_readback')
        return metadata
