"""Read-only, bounded, cursor-addressed access to saved journals.

The SQLite files are disposable indexes, never replacements for the source log.
No Session, observer, transport or budget is constructed by this module.
"""
import hashlib
import json
import re
import sqlite3
import threading
from .activity import ActivityBuilder
from .evaluation_lists import EvaluationBuilder
from contextlib import contextmanager
from pathlib import Path


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class ReviewIndex:
    def __init__(self, runs, cache):
        self.runs, self.cache = Path(runs).resolve(), Path(cache).resolve()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.jobs = {}
        self.lock = threading.RLock()

    def path(self, run):
        if not re.fullmatch(r'run-[0-9]+', run):
            raise ValueError('invalid_run_id')
        path = self.runs / (run + '.jsonl')
        if path.resolve().parent != self.runs or not path.is_file():
            raise ValueError('run_not_allowed')
        return path

    def catalog(self):
        return [{'id': p.stem, 'bytes': p.stat().st_size} for p in sorted(
            self.runs.glob('run-*.jsonl'), reverse=True)
            if re.fullmatch(r'run-[0-9]+', p.stem) and p.resolve().parent == self.runs]

    def start(self, run):
        path = self.path(run)
        with self.lock:
            current = self.jobs.get(run, {})
            if current.get('status') == 'indexing':
                return self.status(run)
            if (current.get('status') == 'ready' and path.stat().st_size == current['total']
                    and path.stat().st_mtime_ns == current['mtime_ns']):
                return self.status(run)
            self.jobs[run] = dict(status='indexing', bytes=0, total=path.stat().st_size,
                                  cancel=False, events=0, missing=[])
            threading.Thread(target=self._build, args=(run,), daemon=True).start()
        return self.status(run)

    def status(self, run):
        with self.lock:
            if run not in self.jobs:
                raise ValueError('index_not_started')
            return {k: v for k, v in self.jobs[run].items() if k != 'cancel'}

    def cancel(self, run):
        with self.lock:
            self.jobs[run]['cancel'] = True
        return self.status(run)

    def _build(self, run):
        job = self.jobs[run]
        path = self.path(run)
        dbpath = self.cache / (run + '.sqlite')
        db = sqlite3.connect(dbpath)
        try:
            db.executescript('DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS targets; '
                'CREATE TABLE events(seq INTEGER PRIMARY KEY,kind TEXT,epoch INTEGER,media REAL,offset INTEGER,size INTEGER,elapsed REAL,state TEXT);'
                'CREATE TABLE targets(key TEXT PRIMARY KEY,seq INTEGER,epoch INTEGER,kind TEXT,id TEXT,revision TEXT,label TEXT,body TEXT);'
                'CREATE INDEX target_seq ON targets(seq); CREATE INDEX target_id ON targets(id);'
                'CREATE INDEX event_kind ON events(kind,seq);')
            activity = ActivityBuilder(db)
            evaluations = EvaluationBuilder(db)
            hash_ = hashlib.sha256()
            with path.open('rb') as stream:
                for seq in range(1, 10000001):
                    if job['cancel']:
                        job['status'] = 'cancelled'
                        return
                    offset = stream.tell()
                    line = stream.readline(4_000_001)
                    if not line:
                        break
                    if len(line) > 4_000_000:
                        raise ValueError('event_size')
                    e = json.loads(line)
                    if e['event_seq'] != seq or str(e['schema_version']) not in ('2', '3'):
                        raise ValueError('event_order_or_schema')
                    hash_.update(line)
                    kind, p, epoch = e['kind'], e['payload'], e.get('epoch')
                    db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?)',
                               (seq, kind, epoch, e.get('media_s'), offset, len(line), e.get('elapsed_s'),
                                p.get('clock', {}).get('state') if kind == 'display' else None))
                    records = p.get('records', []) if kind == 'atomic_replacement' else [p] if kind in ('record', 'hypothesis', 'transcript_version') else []
                    for i, r in enumerate(records):
                        rid = r.get('id') or r.get('segment_id') or f'event:{seq}'
                        rk = 'transcript' if kind == 'transcript_version' else r.get('kind', kind)
                        db.execute('INSERT INTO targets VALUES (?,?,?,?,?,?,?,?)',
                            (f'r:{seq}:{i}', seq, epoch, rk, rid, str(r.get('revision', 1)),
                             (r.get('label') or r.get('text') or rid)[:500], dumps(r)))
                    if kind == 'local_asset':
                        job['asset'] = p
                    if kind=='run_manifest' and p.get('diagnostic_kind'):
                        job['diagnostic_kind']=p['diagnostic_kind']
                    activity.consume(e)
                    evaluations.consume(e)
                    job.update(bytes=stream.tell(), events=seq)
                    if seq % 500 == 0:
                        db.commit()
            if path.stat().st_size != job['total'] or job['bytes'] != job['total']:
                raise ValueError('source_changed_during_index')
            db.commit()
            job['activity'] = activity.finish()
            if job.get('diagnostic_kind') and activity.valid_clock:
                job['activity'].update(mode='elapsed',duration=activity.previous_elapsed,diagnostic_kind=job['diagnostic_kind'])
            job.update(status='ready', sha256=hash_.hexdigest(), mtime_ns=path.stat().st_mtime_ns,
                       displays=db.execute("SELECT count(*) FROM events WHERE kind='display'").fetchone()[0])
        except Exception as error:
            job.update(status='error', error=str(error) if isinstance(error, ValueError) else type(error).__name__)
        finally:
            db.close()

    @contextmanager
    def connect(self, run):
        job = self.status(run)
        stat = self.path(run).stat()
        if job['status'] != 'ready':
            raise ValueError('index_not_ready')
        if stat.st_size != job['total'] or stat.st_mtime_ns != job['mtime_ns']:
            raise ValueError('source_changed_reindex_required')
        conn = sqlite3.connect(f'file:{(self.cache / (run + ".sqlite")).as_posix()}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def event(self, run, seq):
        with self.connect(run) as db:
            row = db.execute('SELECT * FROM events WHERE seq=?', (int(seq),)).fetchone()
        if not row:
            raise ValueError('event_missing')
        with self.path(run).open('rb') as stream:
            stream.seek(row['offset'])
            return json.loads(stream.read(row['size']))

    def events(self, run, cursor, offset=0, kind='', limit=50):
        limit = max(1, min(int(limit), 100))
        with self.connect(run) as db:
            rows = db.execute('SELECT seq,kind,epoch,media FROM events WHERE seq<=? AND (?="" OR kind=?) ORDER BY seq DESC LIMIT ? OFFSET ?',
                              (int(cursor), kind, kind, limit, max(0, int(offset)))).fetchall()
        return [dict(r) for r in rows]

    def display(self, run, cursor):
        with self.connect(run) as db:
            row = db.execute("SELECT seq FROM events WHERE kind='display' AND seq<=? ORDER BY seq DESC LIMIT 1", (int(cursor),)).fetchone()
        if not row:
            return {'cursor': None, 'frame': None, 'missing_reasons': ['LEGACY_MISSING:display']}
        e = self.event(run, row['seq'])
        return {'cursor': e['event_seq'], 'epoch': e.get('epoch'), 'frame': e['payload']}

    def adjacent_display(self, run, cursor, direction):
        if direction not in ('prev', 'next'):
            raise ValueError('display_direction')
        op, order = ('<', 'DESC') if direction == 'prev' else ('>', 'ASC')
        with self.connect(run) as db:
            row = db.execute(f"SELECT seq FROM events WHERE kind='display' AND seq {op} ? ORDER BY seq {order} LIMIT 1", (int(cursor),)).fetchone()
        return {'cursor': row[0] if row else None}

    def targets(self, run, cursor, offset=0, kind='', query='', limit=50):
        with self.connect(run) as db:
            args = (int(cursor), kind, kind, '%' + query[:200] + '%')
            where = 'seq<=? AND (?="" OR kind=?) AND (id || label) LIKE ?'
            total = db.execute('SELECT count(*) FROM targets WHERE ' + where, args).fetchone()[0]
            rows = db.execute('SELECT key,seq,epoch,kind,id,revision,label FROM targets WHERE ' + where + ' ORDER BY seq DESC LIMIT ? OFFSET ?',
                              (*args, min(max(int(limit), 1), 100), max(int(offset), 0))).fetchall()
        return {'total': total, 'items': [dict(r) for r in rows]}

    def target(self, run, cursor, key):
        with self.connect(run) as db:
            row = db.execute('SELECT * FROM targets WHERE key=? AND seq<=?', (key, int(cursor))).fetchone()
        if not row:
            raise ValueError('target_missing_as_of_cursor')
        data = dict(row)
        data['body'] = json.loads(data['body'])
        return data

    def record(self, run, cursor, rid):
        with self.connect(run) as db:
            row = db.execute('SELECT key FROM targets WHERE id=? AND seq<=? ORDER BY seq DESC LIMIT 1', (rid, int(cursor))).fetchone()
        return self.target(run, cursor, row[0]) if row else None

    def links(self, run, cursor, key):
        """Real reference edges only; includes reverse edges and request/response records."""
        target = self.target(run, cursor, key)
        rid = target['id']
        out = []
        def mentions(value):
            if isinstance(value, str):
                return value == rid
            if isinstance(value, list):
                return any(mentions(v) for v in value)
            if isinstance(value, dict):
                return rid in value or any(mentions(v) for v in value.values())
            return False
        with self.connect(run) as db:
            candidates = db.execute('SELECT key,seq,kind,body FROM targets WHERE seq<=? AND body LIKE ? ORDER BY seq LIMIT 501', (int(cursor), '%' + rid + '%')).fetchall()
            for r in candidates:
                if r['key'] != key and mentions(json.loads(r['body'])):
                    out.append({'key': r['key'], 'seq': r['seq'], 'kind': r['kind'], 'direction': 'references_selected'})
            for dep in target['body'].get('dependencies', {}):
                record = self.record(run, cursor, dep)
                out.append({'key': record['key'] if record else None, 'id': dep, 'direction': 'dependency',
                            'required_revision': target['body']['dependencies'][dep], 'missing': record is None})
            def strings(value):
                if isinstance(value, str):
                    yield value
                elif isinstance(value, dict):
                    for k, v in value.items():
                        yield k
                        yield from strings(v)
                elif isinstance(value, list):
                    for v in value:
                        yield from strings(v)
            existing = {r.get('key') for r in out}
            for ref in set(strings(target['body'])):
                if ref == rid or len(ref) > 200:
                    continue
                linked = db.execute('SELECT key,seq,kind FROM targets WHERE id=? AND seq<=? ORDER BY seq DESC LIMIT 1', (ref, int(cursor))).fetchone()
                if linked and linked['key'] not in existing:
                    out.append({'key': linked['key'], 'seq': linked['seq'], 'kind': linked['kind'], 'direction': 'saved_forward_reference'})
                    existing.add(linked['key'])
            events = db.execute("SELECT seq,kind FROM events WHERE seq<=? AND kind IN ('candidate_link','evaluation_requested','dispatcher_result','invalidation','atomic_replacement') ORDER BY seq", (int(cursor),)).fetchall()
        for e in events:
            p = self.event(run, e['seq'])['payload']
            if mentions(p):
                out.append({'key': f"e:{e['seq']}", 'seq': e['seq'], 'kind': e['kind'], 'direction': 'event_reference'})
        return {'items': out[:500], 'truncated': len(out) > 500, 'basis': 'literal_saved_references_only'}

    def timeline(self, run, cursor):
        kinds = ('local_model_admitted', 'local_model_completed', 'local_slot_requested', 'local_slot_coalesced', 'local_model_failed',
                 'local_result_discarded', 'dispatcher_result', 'stop', 'invalidation', 'cut', 'decode_completed')
        with self.connect(run) as db:
            rows = db.execute('SELECT seq,kind,epoch,media FROM events WHERE seq<=? AND kind IN (' + ','.join('?' for _ in kinds) + ') ORDER BY seq', (int(cursor), *kinds)).fetchall()
        inputs, states, decodes = [], [], {}
        for r in rows:
            p = self.event(run, r['seq'])['payload']
            if r['kind'] == 'decode_completed':
                decodes[(r['epoch'], p.get('completed_wall_s'))] = (p.get('audio_interval'), r['seq'])
                continue
            if r['kind'] == 'local_model_admitted' and p.get('agent') == 'SpeechASR':
                interval = p.get('input_audio_interval')
                source = r['seq']
                basis = 'direct'
                if interval is None:
                    interval, source = decodes.get((r['epoch'], p.get('source_available_wall_s')), (None, None))
                    basis = 'derived:matching_decode_completed_wall_s' if interval else 'LEGACY_MISSING'
                inputs.append({'interval': interval, 'basis': basis, 'epoch': r['epoch'], 'event_cursor': r['seq'], 'source_cursor': source,
                               'input_cursor': p.get('input_cursor'), 'source_media_s': p.get('source_media_s')})
            states.append({**dict(r), 'agent': p.get('agent'), 'status': p.get('status'), 'reason': p.get('reason') or p.get('error'),
                           'slot_wait_s': p.get('slot_wait_s'), 'inference_s': (p['completed_wall_s'] - p['accepted_wall_s']) if 'completed_wall_s' in p and 'accepted_wall_s' in p else None,
                           'payload': p})
        # Bounded response; caller can inspect older events via the paged event accessor.
        return {'asr_inputs': inputs, 'states': states[-200:], 'states_total': len(states),
                'asr_input_status': 'recorded_or_derived' if inputs else 'LEGACY_MISSING:ASR_admission_intervals',
                'warning': 'Recognition segments, released audio and actual model inputs are distinct. Unrecognized time has unknown speech content.'}
