"""Saved-event projection for H1. No inference; each row version has an arrival cursor."""
import copy
import hashlib
import json
import math
from .structured_results import enrich, present, field

PRODUCERS = {'SpeechASR': '音声認識', 'FrameInterpreter': '画像解析', 'FaceExpression': '表情解析',
             'TextContext': '会話解析', 'FaceTrack': '人物検出', 'MotionCut': '動き計測', 'AudioMeasure': '音声計測',
             'EvidenceEvaluator': 'Jev評価', 'DominanceReader': 'Jev評価', 'ContextReader': '追加確認'}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def result_text(payload):
    """Formatting only: preserve original strings, labels and numeric values."""
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False)
    parts = []
    for k in ('text', 'description', 'topic', 'register', 'quote', 'scene', 'time_of_day', 'expression',
              'status', 'reason', 'error', 'p_support', 'p_contradict', 'r', 'a'):
        if k in payload and payload[k] is not None:
            parts.append(str(payload[k]) if k in ('text', 'description') else f'{k}: {payload[k]}')
    if payload.get('measurement'):
        parts.append(json.dumps(payload['measurement'], ensure_ascii=False, separators=(',', ':')))
    if payload.get('parsed'):
        parts.append(json.dumps(payload['parsed'], ensure_ascii=False, separators=(',', ':')))
    return ' / '.join(parts) or json.dumps({k: v for k, v in payload.items() if k not in ('raw', 'model')}, ensure_ascii=False)


class ActivityBuilder:
    def __init__(self, db):
        self.db = db
        self.rows, self.jobs, self.completed, self.requests, self.identities = {}, {}, {}, [], {}
        self.valid_clock, self.previous_elapsed, self.last_seq = True, None, 0
        self.clock_count = 0
        db.executescript('DROP TABLE IF EXISTS activity; '
            'CREATE TABLE activity(row_id TEXT,seq INTEGER,birth INTEGER,body TEXT,PRIMARY KEY(row_id,seq));'
            'CREATE INDEX activity_seq ON activity(seq); CREATE INDEX activity_row ON activity(row_id,seq);')

    def new(self, event, producer, key, status='done'):
        rid = key
        self.rows[rid] = {'row_id': rid, 'birth_cursor': event['event_seq'], 'epoch': event.get('epoch'),
            'producer': PRODUCERS.get(producer, producer), 'agent': producer, 'status': status, 'text': '',
            'start_record_s': event.get('elapsed_s') if status == 'running' else None,
            'result_record_s': None, 'target_key': key, 'members': [], 'event_refs': [], 'outputs': []}
        return rid

    def save(self, rid, event, key, text=None, status=None, output_id=None):
        row = self.rows[rid]
        if key not in row['members']:
            row['members'].append(key)
        if event['event_seq'] not in row['event_refs']:
            row['event_refs'].append(event['event_seq'])
        row['available_cursor'] = event['event_seq']
        if status:
            row['status'] = status
        if text is not None:
            row['result_seq'] = event['event_seq']
            row['target_key'] = key
            row['result_record_s'] = event.get('elapsed_s')
            if output_id is None:
                row['text'] = text
            else:
                existing = next((x for x in row['outputs'] if x['id'] == output_id), None)
                if existing:
                    existing.update(text=text, key=key)
                else:
                    row['outputs'].append({'id': output_id, 'text': text, 'key': key})
                row['text'] = '\n'.join(x['text'] for x in row['outputs'])
            kind, payload = event['kind'], event['payload']
            if kind == 'local_model_completed':
                output = payload.get('output')
                payload = {**(output if isinstance(output, dict) else {}), 'input_cursor': payload.get('input_cursor')}
            elif kind == 'atomic_replacement':
                payload = payload['records'][int(key.rsplit(':', 1)[1])]
            if kind in ('record', 'atomic_replacement', 'transcript_version', 'local_model_completed'):
                enrich(row, event, key, payload, self.db)
            elif row['status'] == 'error':
                row['result_fields'] = [field('failure', '処理結果', payload.get('reason') or payload.get('error') or '失敗', 'reason', key)] + [f for f in row.get('result_fields', []) if f['id'] != 'failure']
                row['adoption_state'] = 'rejected'
        snapshot = json.dumps(row, ensure_ascii=False, allow_nan=False)
        self.db.execute('INSERT OR REPLACE INTO activity VALUES (?,?,?,?)', (rid, event['event_seq'], row['birth_cursor'], snapshot))

    def consume(self, event):
        seq, kind, p, epoch = event['event_seq'], event['kind'], event['payload'], event.get('epoch')
        stamp = event.get('elapsed_s')
        if not finite(stamp) or stamp < 0 or (self.previous_elapsed is not None and stamp < self.previous_elapsed):
            self.valid_clock = False
        if finite(stamp):
            self.previous_elapsed = stamp
        self.last_seq = seq
        if kind == 'display':
            self.clock_count += 1
        key = f'e:{seq}'
        agent = p.get('agent', '')
        job_key = (epoch, agent, p.get('input_cursor'), p.get('accepted_wall_s', p.get('accepted')))
        if kind == 'local_model_admitted':
            rid = self.new(event, agent, key, 'running')
            self.jobs[job_key] = rid
            self.save(rid, event, key)
        elif kind in ('local_model_completed', 'local_model_failed'):
            rid = self.jobs.pop(job_key, None) or self.new(event, agent, key)
            failed = kind == 'local_model_failed' or p.get('expired')
            text = ('失敗: ' + str(p.get('reason') or 'expired')) if failed else result_text(p.get('output', {}))
            self.save(rid, event, key, text, 'error' if failed else 'done', 'output')
            wall = event.get('created_monotonic_s')
            if wall is not None:
                self.completed[(epoch, agent, wall)] = rid
        elif kind == 'evaluation_requested':
            rid = self.new(event, 'DominanceReader' if p.get('role') == 'dominance' else 'EvidenceEvaluator', key, 'running')
            self.requests.append({'row': rid, 'epoch': epoch, 'role': p.get('role'), 'units': tuple(p.get('unit_ids', [])),
                                  'wall': event.get('created_monotonic_s'), 'closed': False})
            self.save(rid, event, key)
        elif kind == 'dispatcher_result':
            matches = [r for r in self.requests if not r['closed'] and r['epoch'] == epoch and r['units'] == tuple(p.get('unit_ids', []))]
            rid = matches[0]['row'] if len(matches) == 1 else self.new(event, 'EvidenceEvaluator', key)
            if len(matches) == 1:
                matches[0]['closed'] = True
            row = self.rows[rid]
            failed = p.get('status') not in ('accepted', 'completed')
            text = ('失敗: ' + str(p.get('reason') or p.get('status'))) if failed else row['text'] or str(p.get('status'))
            self.save(rid, event, key, text, 'error' if failed else 'done')
        elif kind in ('local_error', 'local_result_discarded', 'local_slot_discarded'):
            rid = self.new(event, agent or '処理', key)
            self.save(rid, event, key, '失敗: ' + str(p.get('error') or p.get('reason')), 'error')
        elif kind == 'cut':
            rid = self.new(event, '場面検出', key)
            self.save(rid, event, key, f"boundary_s: {p.get('boundary_s')} / {p.get('old_scope')} → {p.get('new_scope')}")
        elif kind in ('record', 'atomic_replacement', 'transcript_version'):
            records = p.get('records', []) if kind == 'atomic_replacement' else [p]
            for i, record in enumerate(records):
                self.record(event, record, f'r:{seq}:{i}', kind)

    def record(self, event, record, key, kind):
        rk, agent = record.get('kind'), record.get('agent', '')
        if kind != 'transcript_version' and rk not in ('observation', 'evidence_evaluation', 'dominance_evaluation', 'dominance_readout'):
            return  # field ticks are inspected in the collapsed field/event views
        epoch, stamp = event.get('epoch'), event.get('elapsed_s')
        identity = (record.get('id') or record.get('segment_id'), record.get('revision'),
                    hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest())
        repeated = self.identities.get(identity)
        if repeated:
            self.save(repeated, event, key)  # same record re-published: retain the event ref, not a new row
            return
        if kind == 'transcript_version':
            agent = 'SpeechASR'
        is_jev = rk in ('evidence_evaluation', 'dominance_evaluation', 'dominance_readout')
        if is_jev:
            agent = 'DominanceReader' if rk in ('dominance_evaluation', 'dominance_readout') else 'EvidenceEvaluator'
            role = record.get('evaluation_kind', 'dominance' if rk == 'dominance_readout' else None)
            accepted = record.get('accepted_wall_s')
            matches = [r for r in self.requests if r['epoch'] == epoch and r['role'] == role and not r['closed']
                       and accepted is not None and r['wall'] == accepted]
            if len(matches) > 1 and record.get('observation_id'):
                matches = [r for r in matches if record['observation_id'] in r['units']]
            rid = matches[0]['row'] if len(matches) == 1 else None
        else:
            wall = event.get('created_monotonic_s')
            rid = self.completed.get((epoch, agent, wall)) if wall is not None else None
        # Routine measurements share a one-second display bucket, with every original ref retained.
        m = record.get('measurement') or {}
        abnormal = m.get('cut') or m.get('discontinuity') or (m.get('clip_fraction') or 0) > 0
        bucket = f'cpu:{epoch}:{agent}:{math.floor(stamp)}' if agent in ('MotionCut', 'AudioMeasure') and finite(stamp) and not abnormal else None
        rid = rid or (bucket if bucket in self.rows else None)
        if not rid:
            rid = self.new(event, agent or '処理', bucket or key)
        self.identities[identity] = rid
        status = 'error' if record.get('status') == 'error' else 'done'
        text = result_text(record)
        output_id = (record.get('profile_id') or record.get('hypothesis_id') or record.get('id')) if is_jev else (
            record.get('segment_id') if kind == 'transcript_version' else 'measurement' if bucket else
            record.get('facet') or record.get('id'))
        self.save(rid, event, key, text, status, output_id)

    def finish(self):
        return {'mode': 'elapsed' if self.valid_clock and self.clock_count else 'order',
                'duration': self.previous_elapsed if self.valid_clock and self.clock_count else self.last_seq,
                'time_label': '結果記録', 'rows': len(self.rows), 'projection': 'h11-structured-v1'}


def page(index, run, position, offset=0, limit=60):
    status = index.status(run)
    meta = status.get('activity')
    if not meta:
        raise ValueError('activity_index_missing_reopen_run')
    position = float(position)
    if not math.isfinite(position) or position < 0:
        raise ValueError('record_position')
    limit, offset = max(1, min(int(limit), 60)), max(0, int(offset))
    with index.connect(run) as db:
        if meta['mode'] == 'elapsed':
            cursor = db.execute('SELECT max(seq) FROM events WHERE elapsed<=?', (position,)).fetchone()[0] or 0
        else:
            cursor = min(int(position), status['events'])
        count = db.execute('SELECT count(DISTINCT row_id) FROM activity WHERE seq<=?', (cursor,)).fetchone()[0]
        rows = db.execute('SELECT body FROM activity a WHERE seq=(SELECT max(seq) FROM activity b WHERE b.row_id=a.row_id AND seq<=?) '
                          'ORDER BY birth DESC LIMIT ? OFFSET ?', (cursor, limit, offset)).fetchall()
        anchor = db.execute("SELECT seq,epoch,media,elapsed,state FROM events WHERE kind='display' AND seq<=? ORDER BY seq DESC LIMIT 1", (cursor,)).fetchone()
        next_anchor = db.execute("SELECT seq,epoch,media,elapsed,state FROM events WHERE kind='display' AND seq>? ORDER BY seq LIMIT 1", (cursor,)).fetchone()
        next_update = db.execute('SELECT min(seq) FROM activity WHERE seq>?', (cursor,)).fetchone()[0]
    values = [present(json.loads(r[0]), run) for r in reversed(rows)]
    # Pages intentionally omit full result text and members; selection fetches them at its pinned cursor.
    items = [{k: v for k, v in r.items() if k not in ('members', 'event_refs', 'outputs', 'text')} |
             {'text': r['text'][:360], 'truncated': len(r['text']) > 360} for r in values]
    for item in items:
        item['field_count'] = len(item['result_fields'])
        item['result_fields'] = [{**f, 'value': f['value'][:240] if isinstance(f['value'], str) else f['value']} for f in item['result_fields'][:4]]
        item['source_count'] = len(item['source_refs'])
        item['source_refs'] = item['source_refs'][:2]
        if 'aggregation' in item:
            item['aggregation'] = {k: v for k, v in item['aggregation'].items() if k != 'event_refs'}
    return {'meta': meta, 'cursor': cursor, 'total': count, 'offset': offset, 'items': items,
            'clock': dict(anchor) if anchor else None, 'next_clock': dict(next_anchor) if next_anchor else None,
            'next_cursor': next_update}


def row_at(index, run, cursor, row_id):
    if row_id.startswith('jev:'):
        from .evaluation_lists import evaluation_at
        return evaluation_at(index, run, cursor, row_id)
    with index.connect(run) as db:
        row = db.execute('SELECT body FROM activity WHERE row_id=? AND seq<=? ORDER BY seq DESC LIMIT 1', (row_id, int(cursor))).fetchone()
    if not row:
        raise ValueError('activity_not_available_at_cursor')
    value = present(json.loads(row[0]), run)
    from .evaluation_lists import analysis_details
    return analysis_details(index, run, cursor, value)
