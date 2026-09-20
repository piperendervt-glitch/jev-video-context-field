"""Append-only human sidecars. Self-declared local identity is not authentication."""
import copy
import hashlib
import json
import math
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .review_index import dumps

ROOT = Path(__file__).resolve().parents[1]


def validate_schema(value, schema):
    """Validate the bundled finite schema subset without fetching any schemas."""
    if 'const' in schema and value != schema['const']:
        raise ValueError('schema_const')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('schema_enum')
    kinds = {'null': value is None, 'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int,
             'number': type(value) in (int, float) and math.isfinite(value), 'boolean': type(value) is bool}
    typ = schema.get('type')
    if typ and not any(kinds.get(t, False) for t in (typ if isinstance(typ, list) else [typ])):
        raise ValueError('schema_type')
    if isinstance(value, dict):
        if any(k not in value for k in schema.get('required', [])):
            raise ValueError('schema_required')
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is False and any(k not in props for k in value):
            raise ValueError('schema_extra_key')
        for k, v in value.items():
            if k in props:
                validate_schema(v, props[k])
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > min(schema.get('maxLength', 10000), 10000):
            raise ValueError('schema_string_length')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            raise ValueError('schema_pattern')
    if type(value) in (int, float):
        if value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            raise ValueError('schema_range')
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > min(schema.get('maxItems', 500), 500):
            raise ValueError('schema_array_length')
        for item in value:
            validate_schema(item, schema.get('items', {}))
        if schema.get('uniqueItems') and len({dumps(v) for v in value}) != len(value):
            raise ValueError('schema_unique_items')
    if 'anyOf' in schema:
        successes = 0
        for item in schema['anyOf']:
            try:
                validate_schema(value, item)
                successes += 1
            except ValueError:
                pass
        if not successes:
            raise ValueError('schema_any_of')
    for item in schema.get('allOf', []):
        validate_schema(value, item)
    if 'if' in schema:
        try:
            validate_schema(value, schema['if'])
        except ValueError:
            validate_schema(value, schema.get('else', {}))
        else:
            validate_schema(value, schema.get('then', {}))


def utc():
    return datetime.now(timezone.utc).isoformat()


def safe_export(value):
    """Deliberately redact path/credential-like text even inside user notes."""
    if isinstance(value, dict):
        return {k: safe_export(v) for k, v in value.items() if not re.search(r'(?i)(authorization|cookie|api.?key|token|private.?path|jpeg|raw.?body)', k)}
    if isinstance(value, list):
        return [safe_export(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'(?i)(?:[A-Z]:[\\/]|\\\\)[^\s<>"\n]+|/(?:home|Users|tmp|etc|mnt|var)/[^\s<>"\n]+', '[PRIVATE_PATH]', value)
        value = re.sub(r'(?i)(?:Bearer\s+|(?:api[_-]?key|authorization|cookie|token)\s*[:=]\s*)[^\s,;]+', '[CREDENTIAL_REDACTED]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]{12,}', '[CREDENTIAL_REDACTED]', value)
    return value


class HumanReviewStore:
    def __init__(self, root, fixture=False):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.fixture = fixture
        self.lock = threading.RLock()
        self.schema = json.loads((ROOT / 'config/human_review_record.schema.json').read_text(encoding='utf8'))
        with self.db() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS pins(id TEXT PRIMARY KEY,body TEXT);'
                'CREATE TABLE IF NOT EXISTS annotations(id TEXT PRIMARY KEY,review_id TEXT,revision INTEGER,idem TEXT UNIQUE,request_hash TEXT,body TEXT);'
                'CREATE TABLE IF NOT EXISTS issues(id INTEGER PRIMARY KEY,review_id TEXT,revision INTEGER,body TEXT);')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.root / 'reviews.sqlite', timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def pin(self, target, context, feature_build=None):
        review_id = 'review-' + uuid.uuid4().hex
        body = {'review_id': review_id, 'target': copy.deepcopy(target), 'context': copy.deepcopy(context), 'created_at': utc(), 'feature_build': feature_build}
        with self.db() as db:
            db.execute('INSERT INTO pins VALUES (?,?)', (review_id, dumps(body)))
        directory = self.root / review_id
        directory.mkdir()
        (directory / 'manifest.json').write_text(dumps(body), encoding='utf8')
        return body

    def get_pin(self, review_id):
        with self.db() as db:
            row = db.execute('SELECT body FROM pins WHERE id=?', (review_id,)).fetchone()
        if not row:
            raise ValueError('review_not_found')
        return json.loads(row[0])

    def list(self):
        with self.db() as db:
            rows = db.execute('SELECT p.body,(SELECT body FROM annotations a WHERE a.review_id=p.id ORDER BY revision DESC LIMIT 1) FROM pins p ORDER BY rowid DESC LIMIT 200').fetchall()
        return [{'pin': json.loads(p), 'annotation': json.loads(a) if a else None} for p, a in rows]

    def save(self, request):
        pin = self.get_pin(request['review_id'])
        idem = request.get('idempotency_key', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{12,100}', idem):
            raise ValueError('idempotency_key_required')
        reqhash = hashlib.sha256(dumps(request).encode()).hexdigest()
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT request_hash,body FROM annotations WHERE idem=?', (idem,)).fetchone()
            if existing:
                if existing[0] != reqhash:
                    raise ValueError('idempotency_conflict')
                return {'ack': True, 'duplicate': True, 'record': json.loads(existing[1])}
            previous = db.execute('SELECT id,revision FROM annotations WHERE review_id=? ORDER BY revision DESC LIMIT 1', (pin['review_id'],)).fetchone()
            revision = previous[1] if previous else 0
            if type(request.get('expected_revision')) is not int or request['expected_revision'] != revision:
                raise ValueError('revision_conflict_reload_required')
            # Pin's clocks, run and source cannot be replaced by the save request.
            context = copy.deepcopy(pin['context'])
            supplied = request.get('exposure', {})
            allowed = {'review_basis', 'model_output_exposure', 'future_content_exposure', 'reviewed_evidence', 'human_evidence_confirmation'}
            if set(supplied) - allowed:
                raise ValueError('immutable_view_context')
            context.update(supplied)
            if pin['context']['model_output_exposure'] == 'shown_before_annotation' and context['model_output_exposure'] == 'not_shown_before_annotation':
                raise ValueError('exposure_cannot_be_undone')
            record = dict(schema_version='human-review-v1', record_kind='human_review', review_id=pin['review_id'],
                record_id='annotation-' + uuid.uuid4().hex, record_revision=revision + 1,
                supersedes_record_id=previous[0] if previous else None, idempotency_key=idem,
                reviewer={'label': request.get('reviewer', ''), 'author_kind': 'human', 'identity_assurance': 'local_self_declared'},
                target=pin['target'], view_context=context, judgment=request['judgment'],
                provenance={'created_at': utc(), 'reference_basis': request.get('reference_basis', 'personal_reference'),
                            'source_run_mutated': False, 'feedback_into_inference': False, 'is_test_fixture': self.fixture})
            validate_schema(record, self.schema)
            meaningful = record['judgment']['semantic_verdict'] in ('consistent_with_evidence', 'partially_inconsistent', 'contradicted_by_evidence', 'ambiguous')
            if record['judgment']['review_status'] == 'submitted' and meaningful:
                if not context['human_evidence_confirmation'] or not context['reviewed_evidence']:
                    raise ValueError('human_evidence_confirmation_required')
                if pin['target']['target_kind'] in ('model_record', 'feature_check') and pin['target']['run_id'] and pin['target']['media_match'] != 'hash_verified':
                    raise ValueError('verify_media_before_semantic_review')
            db.execute('INSERT INTO annotations VALUES (?,?,?,?,?,?)', (record['record_id'], pin['review_id'], revision + 1, idem, reqhash, dumps(record)))
        self._mirror(pin['review_id'])
        return {'ack': True, 'duplicate': False, 'record': record}

    def _mirror(self, review_id):
        self.get_pin(review_id)
        with self.lock, self.db() as db:
            for table in ('annotations', 'issues'):
                rows = db.execute(f'SELECT body FROM {table} WHERE review_id=? ORDER BY revision', (review_id,)).fetchall()
                target = self.root / review_id / (table + '.jsonl')
                temp = target.with_suffix('.tmp')
                temp.write_text(''.join(r[0] + '\n' for r in rows), encoding='utf8')
                temp.replace(target)

    def issue(self, request):
        rid = request['review_id']
        self.get_pin(rid)
        status = request['status']
        if status not in ('open', 'triaged', 'fixed_pending_human', 'verified_by_human', 'reopened', 'deferred'):
            raise ValueError('issue_status')
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,body FROM issues WHERE review_id=? ORDER BY revision DESC LIMIT 1', (rid,)).fetchone()
            revision = row[0] if row else 0
            if request.get('expected_revision') != revision:
                raise ValueError('issue_revision_conflict')
            old = json.loads(row[1])['status'] if row else None
            legal = {None: {'open'}, 'open': {'triaged', 'deferred'}, 'triaged': {'fixed_pending_human', 'deferred'},
                     'fixed_pending_human': {'verified_by_human', 'reopened', 'deferred'}, 'verified_by_human': {'reopened'},
                     'reopened': {'triaged', 'deferred'}, 'deferred': {'reopened'}}
            if status not in legal[old]:
                raise ValueError('issue_transition')
            if status in ('deferred', 'fixed_pending_human') and not request.get('reason'):
                raise ValueError('issue_reason_required')
            if status == 'fixed_pending_human' and not request.get('fix_build'):
                raise ValueError('fix_build_required')
            if status == 'verified_by_human':
                reference = db.execute('SELECT body FROM annotations WHERE id=?', (request.get('verification_record_id'),)).fetchone()
                if not reference:
                    raise ValueError('new_human_review_required')
                annotation = json.loads(reference[0])
                prior = json.loads(row[1])
                old_target = self.get_pin(rid)['target']
                if (annotation['review_id'] == rid or not annotation['view_context']['human_evidence_confirmation']
                    or annotation['judgment']['review_status'] != 'submitted'
                    or annotation['judgment']['semantic_verdict'] != 'consistent_with_evidence'
                    or annotation['target']['build_ref'] != prior.get('fix_build')
                    or annotation['target']['feature_id'] != old_target['feature_id']
                    or annotation['target']['asset_sha256'] != old_target['asset_sha256']
                    or (prior.get('new_run') and annotation['target']['run_id'] != prior['new_run'])
                    or (annotation['provenance']['is_test_fixture'] and not self.fixture)):
                    raise ValueError('new_human_review_for_fix_build_required')
            body = {k: request.get(k) for k in ('review_id', 'status', 'reason', 'fix_build', 'new_run', 'verification_record_id')}
            body.update(revision=revision + 1, created_at=utc())
            db.execute('INSERT INTO issues(review_id,revision,body) VALUES (?,?,?)', (rid, revision + 1, dumps(body)))
        self._mirror(rid)
        return body

    def issues(self):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM issues ORDER BY id')]

    def export(self):
        with self.db() as db:
            records = [safe_export(json.loads(r[0])) for r in db.execute('SELECT body FROM annotations ORDER BY rowid')]
        issues = safe_export(self.issues())
        lines = '\n'.join(dumps(r) for r in records + [{'record_kind': 'issue', **r} for r in issues]) + '\n'
        md = '# Human debug feedback — ローカル生成・未送信\n\nHuman acceptance: pending（保存した対象の判断のみ。全動画の保証ではありません）\n\n'
        for r in records:
            t, j = r['target'], r['judgment']
            md += f"## {r['record_id']} / revision {r['record_revision']}\n\n"
            # Escaped JSON inside fenced blocks; fence chars removed from untrusted text.
            md += '```json\n' + json.dumps({'target': t, 'view_context': r['view_context'], 'judgment': j, 'reviewer': r['reviewer'], 'provenance': r['provenance']}, ensure_ascii=False, indent=2).replace('`', '\\u0060') + '\n```\n\n'
        md += '## Issues / 再確認\n\n```json\n' + json.dumps(issues, ensure_ascii=False, indent=2).replace('`', '\\u0060') + '\n```\n'
        directory = self.root / 'export'
        directory.mkdir(exist_ok=True)
        (directory / 'findings.jsonl').write_text(lines, encoding='utf8')
        (directory / 'codex-feedback.md').write_text(md, encoding='utf8')
        return {'jsonl': lines, 'markdown': md, 'records': len(records), 'sent': False}
