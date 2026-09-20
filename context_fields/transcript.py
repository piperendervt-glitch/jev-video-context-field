"""Append-only ASR originals. Interpretation and evidence remain in the ledger."""
import copy


class TranscriptStore:
    def __init__(self, ledger):
        self.ledger = ledger
        self.versions = []
        self.current = {}
        self.serial = 0

    def ingest(self, output, audio, observation_id=None, audio_sample_refs=None):
        epoch = self.ledger.epoch
        a, b = audio['start_s'], audio['end_s']
        segments = output.get('segments', [])
        if not output.get('text', '').strip():
            segments = [dict(start_s=a, end_s=b, text='', status='silence' if output.get('reason')=='digital_silence' else 'no_speech_recognized',timing_basis='audio_window')]
        elif not segments:
            segments = [dict(start_s=a, end_s=b, text=output['text'], timing_basis='audio_window')]
        previous = [r for r in self.current.values() if r['epoch']==epoch
                    and r['media_start_s'] < b and r['media_end_s'] > a
                    and r['status'] != 'cancelled']
        # One-to-one correction keeps the ID. Splits/merges explicitly link all
        # affected versions, without pretending to know word alignment.
        replacements = []
        for part in segments:
            start, end = part['start_s'], part['end_s']
            if not a <= start <= end <= b + 1e-6:
                raise ValueError('transcript_outside_released_audio')
            overlaps = [r for r in previous if r['media_start_s'] < end and r['media_end_s'] > start]
            unique = len(overlaps)==1 and sum(p['start_s'] < overlaps[0]['media_end_s'] and p['end_s'] > overlaps[0]['media_start_s'] for p in segments)==1
            self.serial += 1
            sid = overlaps[0]['segment_id'] if unique else f'transcript:{epoch}:{self.serial}'
            revision = overlaps[0]['revision']+1 if unique else 1
            record = {'segment_id':sid,'revision':revision,'supersedes':[{'segment_id':r['segment_id'],'revision':r['revision']} for r in overlaps],
                'epoch':epoch,'media_start_s':start,'media_end_s':end,'text':part['text'],
                'status':part.get('status','provisional'),'observation_id':observation_id,
                'audio_sample_refs':copy.deepcopy(audio_sample_refs or []),
                'timing_basis':part.get('timing_basis','asr_segment'),
                'available_event_cursor':len(self.ledger.events)+1}
            if 'words' in part:record['words']=copy.deepcopy(part['words'])
            replacements.append(record)
        superseded = {r['segment_id'] for r in previous}
        for sid in superseded:self.current.pop(sid,None)
        for old in previous:
            if not any(old['segment_id']==ref['segment_id'] for r in replacements for ref in r['supersedes']):
                replacements.append({**copy.deepcopy(old),'revision':old['revision']+1,'status':'cancelled',
                    'supersedes':[{'segment_id':old['segment_id'],'revision':old['revision']}],
                    'available_event_cursor':len(self.ledger.events)+1,'cancellation_reason':'asr_window_revision'})
        for r in replacements:
            # Each event is its own availability point, including split outputs.
            r['available_event_cursor']=len(self.ledger.events)+1
            self.ledger.event('transcript_version',r)
            self.versions.append(copy.deepcopy(r)); self.current[r['segment_id']]=r
        return replacements

    def public(self, epoch, has_audio=True, pending=False, error=None):
        rows=sorted((copy.deepcopy(r) for r in self.current.values() if r['epoch']==epoch),key=lambda r:(r['media_start_s'],r['segment_id']))
        for r in rows:
            r['source_active']=r['observation_id'] in self.ledger.active
        state='no_audio' if not has_audio else 'recognition_error' if error else 'processing' if pending else 'available' if rows else 'awaiting_released_audio'
        return {'schema_version':'3','state':state,'reason':error,'current':rows,
                'history':[copy.deepcopy(r) for r in self.versions if r['epoch']==epoch]}
