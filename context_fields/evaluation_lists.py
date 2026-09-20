"""H1.2 disposable, prefix-only projection. No evaluation/transport is run here.

Evidence completion needs candidate registration, exact observation revision,
hypothesis and a matching saved request hash/unit. Inputs to Dominance never
complete Evidence. Missing legacy edges remain unknown. Filtering precedes paging.
"""
import copy
import hashlib
import json
import math
from .structured_results import enrich, present, field, source_refs
from .evaluation_status import EvaluationStatus, at as status_at


class EvaluationBuilder:
    def __init__(self, db):
        self.db = db
        self.progress = EvaluationStatus(db)
        self.requests, self.cards, self.seen, self.pending = {}, {}, set(), []
        self.lifecycle_rows={};self.request_rows={};self.evaluation_aliases={};self.wire_questions={}
        db.executescript('DROP TABLE IF EXISTS h12_links; DROP TABLE IF EXISTS h12_units; '
            'DROP TABLE IF EXISTS h12_cards; DROP TABLE IF EXISTS h12_invalid; '
            'CREATE TABLE h12_links(seq INTEGER,epoch INTEGER,oid TEXT,rev TEXT,hid TEXT);'
            'CREATE TABLE h12_units(seq INTEGER,epoch INTEGER,oid TEXT,rev TEXT,hid TEXT,card TEXT,valid INTEGER);'
            'CREATE TABLE h12_cards(row_id TEXT,seq INTEGER,arrival INTEGER,body TEXT,PRIMARY KEY(row_id,seq));'
            'CREATE TABLE h12_invalid(seq INTEGER,epoch INTEGER,id TEXT,reason TEXT);'
            'CREATE INDEX h12_units_obs ON h12_units(epoch,oid,rev,seq);'
            'CREATE INDEX h12_links_obs ON h12_links(epoch,oid,rev,seq);')

    def target(self, ident, epoch, seq, revision=None):
        rows = self.db.execute('SELECT key,body FROM targets WHERE id=? AND epoch=? AND seq<=? ORDER BY seq DESC', (ident, epoch, seq))
        for key, raw in rows:
            p = json.loads(raw)
            if revision is None or str(p.get('revision')) == str(revision):
                return key, p
        return None, {}

    def save(self, row, seq):
        row['available_cursor'] = seq
        self.db.execute('INSERT OR REPLACE INTO h12_cards VALUES (?,?,?,?)',
                        (row['row_id'], seq, row['result_seq'], json.dumps(row, ensure_ascii=False)))

    def consume(self, e):
        self.progress.consume(e)
        seq, epoch, kind, p = e['event_seq'], e.get('epoch'), e['kind'], e['payload']
        if kind=='jev_lifecycle':
            self.lifecycle(e);return
        if kind=='jev_diagnostic_result':
            self.diagnostic(e);return
        if kind == 'candidate_link':
            _, obs = self.target(p.get('observation_id'), epoch, seq)
            if obs.get('revision') is not None and p.get('hypothesis_id'):
                self.db.execute('INSERT INTO h12_links VALUES (?,?,?,?,?)', (seq, epoch, p['observation_id'], str(obs['revision']), p['hypothesis_id']))
        elif kind == 'evaluation_requested':
            raw = p.get('request', '')
            if isinstance(raw, str):
                try:
                    request = json.loads(raw)
                except (ValueError, TypeError):
                    return
                self.requests[(epoch, hashlib.sha256(raw.encode()).hexdigest())] = (seq, request, p.get('role'))
                request_hash=hashlib.sha256(raw.encode()).hexdigest()
                # Legacy full requests prove waiting, but not when HTTP started.
                # Partial historical schemas remain results-only.
                if request.get('model') and request.get('questions') and not any(ep==epoch and h==request_hash for ep,h,u in self.request_rows):
                    self.lifecycle({**e,'payload':{'stage':'requested','logical_id':'saved-'+str(seq),'request':raw,
                        'request_hash':request_hash,'role':p['role']}})
                if p.get('role') == 'evidence':
                    for name, unit in request.get('state', {}).items():
                        oid = unit.get('observation_id') if isinstance(unit, dict) else None
                        _, obs = self.target(oid, epoch, seq)
                        candidates = self.db.execute('SELECT DISTINCT hid FROM h12_links WHERE epoch=? AND oid=? AND rev=? AND seq<=?',
                            (epoch, oid, str(obs.get('revision')), seq)).fetchall()
                        for (hid,) in candidates:
                            _, h = self.target(hid, epoch, seq)
                            if unit.get('claim') == h.get('label') and unit.get('facet') == h.get('facet') and unit.get('subject') == h.get('subject'):
                                self.pending.append({'epoch': epoch, 'oid': oid, 'rev': str(obs.get('revision')), 'hid': hid, 'request_seq': seq, 'unit': name, 'closed': False})
                                self.db.execute('INSERT INTO h12_units VALUES (?,?,?,?,?,?,?)', (seq, epoch, oid, str(obs.get('revision')), hid, None, -1))
        elif kind == 'invalidation':
            for ident in p.get('ids', []):
                self.db.execute('INSERT INTO h12_invalid VALUES (?,?,?,?)', (seq, epoch, ident, p.get('reason')))
        elif kind in ('record', 'atomic_replacement'):
            for i, record in enumerate(p.get('records', []) if kind == 'atomic_replacement' else [p]):
                if record.get('kind') in ('evidence_evaluation', 'dominance_evaluation', 'dominance_readout'):
                    self.record(e, record, f'r:{seq}:{i}')
        elif kind == 'evaluation_rejected' or kind == 'dispatcher_result' and p.get('status') not in ('accepted', 'completed'):
            if p.get('logical_ids'):return # P1 has exact per-unit lifecycle rows.
            # A rejection has no hypothesis/revision in old journals. Keep its explicit
            # unit and request possibilities for inspection; never turn it into success.
            role = p.get('role') or (p.get('reservation') or {}).get('role')
            row = self.base(e, 'jev:event:' + str(seq), 'DominanceReader' if role == 'dominance' else 'EvidenceEvaluator', f'e:{seq}')
            row['status'] = 'error'
            row['evaluation_state'] = '評価エラー'
            row['result_fields'] = [field('reason', '理由', p.get('reason') or p.get('status'), 'reason', f'e:{seq}')]
            row['failure_units'] = p.get('unit_ids', [p.get('unit')])
            row['text'] = str(p.get('reason') or p.get('status'))
            row['request_details'] = copy.deepcopy(p)
            for oid in row['failure_units']:
                pending = [x for x in self.pending if not x['closed'] and x['epoch']==epoch and x['oid']==oid]
                if len(pending) == 1:
                    bound = pending[0]
                    bound['closed'] = True
                    self.db.execute('INSERT INTO h12_units VALUES (?,?,?,?,?,?,?)',
                        (seq, epoch, oid, bound['rev'], bound['hid'], row['row_id'], 0))
                    source_key, obs = self.target(oid, epoch, seq, bound['rev'])
                    if source_key:
                        original = self.base(e, '', obs.get('agent', ''), source_key)
                        enrich(original, e, source_key, obs, self.db)
                        row.setdefault('original_targets', []).append(source_key)
                        row.setdefault('original_fields', []).extend(original.get('result_fields', []))
                        row.setdefault('source_refs', []).extend(original.get('source_refs', []))
            self.save(row, seq)

    def base(self, e, rid, agent, key):
        return {'row_id': rid, 'birth_cursor': e['event_seq'], 'result_seq': e['event_seq'],
                'epoch': e.get('epoch'), 'agent': agent, 'producer': agent, 'status': 'done',
                'result_record_s': e.get('elapsed_s'), 'target_key': key, 'members': [key],
                'event_refs': [e['event_seq']], 'outputs': [], 'text': '', 'pane': 'evaluations'}

    def lifecycle(self,e):
        p=e['payload'];stage=p.get('stage');lid=p.get('logical_id');seq=e['event_seq'];epoch=e.get('epoch')
        if not lid:return
        if stage=='batch_finalized':
            self.wire_questions[(epoch,lid,p['wire_id'])]=tuple(p.get('question_ids',()))
            return
        if stage=='requested':
            request=json.loads(p['request']);self.requests[(epoch,p['request_hash'])]=(seq,request,p['role'])
            units=request['state'].get('units',{}) if p['role']=='dominance' else request['state']
            for name,unit in units.items():
                if not any(q.startswith(name+'.') for q in request['questions']):continue
                uid=unit.get('observation_id',name)
                rid='jev:req:'+lid+':'+name
                row=self.base(e,rid,'EvidenceEvaluator' if p['role']=='evidence' else 'DominanceReader',f'e:{seq}')
                row.update(logical_id=lid,unit_id=uid,unit_name=name,request_hash=p['request_hash'],request_cursor=seq,
                    evaluation_state='評価待ち',status='running',wire_ids=[],result_fields=[],state_cursor=seq)
                row['result_fields']=[field('target','対象',unit.get('claim') or unit.get('profile',{}).get('definition') or unit.get('facet') or uid,'state.'+name,f'e:{seq}')]
                if p.get('source_refs'):
                    row['source_refs']=source_refs(self.db,p,seq,epoch)
                    row['original_fields']=[field('source_text','参照原文・元結果',p.get('source_text'),'source_text',f'e:{seq}')]
                key,obs=self.target(uid,epoch,seq)
                if key:
                    row['original_targets']=[key];original=self.base(e,'',obs.get('agent',''),key);enrich(original,e,key,obs,self.db)
                    row['original_fields']=original.get('result_fields',[]);row['source_refs']=original.get('source_refs',[])
                self.lifecycle_rows[(epoch,lid,name)]=rid;self.request_rows[(epoch,p['request_hash'],uid)]=rid
                self.cards[rid]=row;self.save(row,seq)
            return
        states={'queued':'評価待ち','sent':'応答待ち','settled':'評価済み','error':'評価エラー','expired':'期限切れ','discarded':'旧版','coalesced':'取消','cancelled':'取消','unsent':'未送信'}
        if stage not in states:return
        for (ep,logical,name),rid in list(self.lifecycle_rows.items()):
            if ep!=epoch or logical!=lid:continue
            # A split HTTP request may contain only one of this logical request's
            # units. Use the recorded batch/question edge, never infer all units
            # were sent or failed together merely from their parent logical ID.
            questions=self.wire_questions.get((epoch,lid,p.get('wire_id')))
            if questions is not None and stage in ('sent','error') and not any(q.startswith(name+'.') for q in questions):continue
            row=copy.deepcopy(self.cards[rid]);row['state_cursor']=seq
            if p.get('wire_id') and p['wire_id'] not in row['wire_ids']:row['wire_ids'].append(p['wire_id'])
            if stage!='settled' or row.get('evaluation_state') in ('評価待ち','応答待ち'):
                row['evaluation_state']=states[stage]
            if stage=='unsent' and row['wire_ids']:
                row['evaluation_state']='一部送信・中止'
            row['status']='running' if stage in ('queued','sent') else 'error' if stage=='error' else 'done'
            row['lifecycle_reason']=p.get('reason');row['event_refs'].append(seq)
            # Status alone does not move a card. Adoption/result records do.
            self.cards[rid]=row;self.save(row,seq)

    def diagnostic(self,e):
        p=e['payload'];epoch=e.get('epoch')
        rid=self.lifecycle_rows.get((epoch,p['logical_id'],p['unit_name']))
        if not rid:return
        row=copy.deepcopy(self.cards[rid]);key=f"e:{e['event_seq']}"
        row.update(result_seq=e['event_seq'],result_record_s=e.get('elapsed_s'),target_key=key,status='done',evaluation_state='評価済み · 保存入力の診断')
        row['members'].append(key);row['result_fields']=[field('scope','用途','保存入力の性能診断・現在の公開値なし','diagnostic',key)]
        for qid,value in p['results'].items():
            for name,v in value.items():
                if type(v) in (int,float,str,bool):row['result_fields'].append(field(qid+':'+name,qid+' · '+name,v,'results.'+qid+'.'+name,key,'number' if type(v) in (int,float) else 'text'))
        row['source_run_ref']=p.get('source_ref');self.cards[rid]=row;self.save(row,e['event_seq'])

    def record(self, e, p, key):
        seq, epoch = e['event_seq'], e.get('epoch')
        kind = p['kind']
        identity = (epoch, p.get('id'), p.get('revision'))
        if identity in self.seen:
            return
        self.seen.add(identity)
        evidence = kind == 'evidence_evaluation'
        # Only an actual evaluation_id aliases a readout; no request-wide merging.
        eid = p.get('evaluation_id') if kind == 'dominance_readout' else p.get('id')
        required = p.get('dependencies', {}).get(eid) if kind == 'dominance_readout' else p.get('revision')
        rid = 'jev:' + str(epoch) + ':' + str(eid or p.get('id')) + ':' + str(required or p.get('revision'))
        if kind=='dominance_readout':rid=self.evaluation_aliases.get((epoch,eid),rid)
        else:
            uid=p.get('observation_id') if evidence else p.get('profile_id','').removeprefix('initial-')
            rid=self.request_rows.get((epoch,p.get('input_hash'),uid),rid)
            self.evaluation_aliases[(epoch,eid)]=rid
        existing = self.cards.get(rid)
        row = copy.deepcopy(existing) if existing else self.base(e, rid, 'EvidenceEvaluator' if evidence else 'DominanceReader', key)
        if key not in row['members']:
            row['members'].append(key)
        saved_request = self.requests.get((epoch, p.get('input_hash')))
        if saved_request:
            row['request_cursor'] = saved_request[0]
            request_key = f'e:{saved_request[0]}'
            if request_key not in row['members']:
                row['members'].append(request_key)
        row['target_key'] = key
        if row.get('logical_id'):row['result_seq']=seq;row['result_record_s']=e.get('elapsed_s')
        row['evaluation_id'] = eid
        row['status'] = 'error' if p.get('status') == 'error' else 'done'
        row['evaluation_state'] = '評価エラー' if row['status'] == 'error' else '評価記録'
        if row.get('logical_id'):
            row['evaluation_state']='情報不足' if p.get('status') in ('insufficient','conflicting') else '評価エラー' if row['status']=='error' else '評価済み'
        row['text'] = p.get('reason') or ''
        row['expires_after_media_s'] = p.get('expires_after_media_s', row.get('expires_after_media_s'))
        if kind == 'dominance_readout':
            row['readout_id'] = p.get('id')
        enrich(row, e, key, p, self.db)
        if evidence:
            oid, hid = p.get('observation_id'), p.get('hypothesis_id')
            rev = p.get('dependencies', {}).get(oid)
            source_key, obs = self.target(oid, epoch, seq, rev) if rev is not None else (None, {})
            request = self.requests.get((epoch, p.get('input_hash')))
            unit = None
            if request and request[2] == 'evidence':
                matches = [(name, item) for name, item in request[1].get('state', {}).items()
                           if isinstance(item, dict) and item.get('observation_id') == oid]
                if len(matches) == 1:
                    unit = matches[0]
            _, hypothesis = self.target(hid, epoch, seq)
            registered = self.db.execute('SELECT 1 FROM h12_links WHERE epoch=? AND oid=? AND rev=? AND hid=? AND seq<=?',
                (epoch, oid, str(rev), hid, seq)).fetchone()
            direct = bool(source_key and registered and unit and hypothesis and
                          unit[1].get('facet') == obs.get('facet') == hypothesis.get('facet') and
                          unit[1].get('subject') == hypothesis.get('subject') and unit[1].get('claim') == hypothesis.get('label'))
            valid = bool(direct and p.get('parsed') and p.get('status') not in ('error', 'cancelled', 'expired') and
                         all(type(p.get(k)) in (int, float) and math.isfinite(p[k]) for k in ('p_support', 'p_contradict', 'r', 'a')))
            row['direct_target'] = {'observation_id': oid, 'revision': rev, 'hypothesis_id': hid,
                'unit': unit[0] if unit else None, 'request_cursor': request[0] if request else None,
                'input_hash': p.get('input_hash'), 'source_key': source_key, 'verified': direct}
            row['direct_target']['missing'] = [label for label, good in (
                ('observation_revision',source_key), ('candidate_registration',registered),
                ('request_hash_unit',unit), ('hypothesis_record',hypothesis),
                ('facet_subject_claim',direct)) if not good]
            row['evaluation_state'] = '評価済み' if valid else '評価エラー' if row['status'] == 'error' else '対応未確認'
            if source_key:
                row['original_targets'] = [source_key]
                # Preserve the original per-item target refs and source image metadata.
                original = self.base(e, '', obs.get('agent', ''), source_key)
                enrich(original, e, source_key, obs, self.db)
                row['original_fields'] = original.get('result_fields', [])
                row['source_refs'] = original.get('source_refs', [])
            self.db.execute('INSERT INTO h12_units VALUES (?,?,?,?,?,?,?)', (seq, epoch, oid, str(rev), hid, rid, 1 if valid else 0 if row['status']=='error' else -2))
            for pending in self.pending:
                if (pending['epoch'],pending['oid'],pending['rev'],pending['hid']) == (epoch,oid,str(rev),hid):
                    pending['closed'] = True
        self.cards[rid] = row
        self.save(row, seq)


def evaluation_at(index, run, cursor, row_id):
    with index.connect(run) as db:
        found = db.execute('SELECT body FROM h12_cards WHERE row_id=? AND seq<=? ORDER BY seq DESC LIMIT 1', (row_id, int(cursor))).fetchone()
    if not found:
        raise ValueError('evaluation_not_available_at_cursor')
    row = present(json.loads(found[0]), run)
    row['original_activities'] = []
    with index.connect(run) as db:
        for key in row.get('original_targets', []):
            for original in db.execute('SELECT body FROM activity a WHERE seq=(SELECT max(seq) FROM activity b WHERE b.row_id=a.row_id AND seq<=?) AND body LIKE ?', (row['result_seq'], '%"'+key+'"%')):
                value = json.loads(original[0])
                if key in value['members']:
                    row['original_activities'].append({**present(value, run), 'inspection_cursor': row['result_seq']})
    return row


def classify(db, row, cursor, units, links, failures):
    if row['agent'] in ('MotionCut', 'AudioMeasure'):
        return {'state': '計測のみ', 'complete': False, 'targets': []}
    observations = {}
    for key in row['members']:
        if not key.startswith('r:'):
            continue
        found = db.execute("SELECT body FROM targets WHERE key=? AND kind='observation' AND seq<=?", (key, cursor)).fetchone()
        if found:
            obs = json.loads(found[0])
            observations[obs.get('id')] = (key, obs)
    targets, unknown = [], not bool(observations)
    # A job-level scene/time/topic/register value must acquire its own observation
    # ref before the job's finite set is known. Description is auxiliary in H1.1.
    primary = {'FrameInterpreter': {'scene','time_of_day'}, 'TextContext': {'topic','register'},
               'FaceExpression': {'expression'}}.get(row['agent'], set())
    if any(f['id'] in primary and not str(f.get('target_ref','')).startswith('r:') for f in row.get('result_fields', [])):
        unknown = True
    for oid, (key, obs) in observations.items():
        identity = (row['epoch'], oid, str(obs.get('revision')))
        expected = links.get(identity, set())
        if not expected:
            unknown = True
        for hid in sorted(expected):
            matches = units.get((*identity, hid), [])
            valid = next((x for x in reversed(matches) if x['valid'] == 1), None)
            targets.append({'observation_id': oid, 'revision': obs.get('revision'), 'hypothesis_id': hid,
                'facet': obs.get('facet'), 'source_key': key, 'evaluation_row': valid['card'] if valid else None,
                'state': 'done' if valid else 'error' if any(x['valid']==0 for x in matches) or identity in failures else 'unknown' if any(x['valid']==-2 for x in matches) else 'running' if matches else 'pending'})
    done = sum(t['state'] == 'done' for t in targets)
    complete = bool(targets and not unknown and done == len(targets))
    state = '評価済み' if complete else '一部評価済み' if done else '評価エラー' if any(t['state']=='error' for t in targets) else '評価待ち' if targets else '対応未確認'
    if state == '評価待ち' and any(t['state']=='running' for t in targets):
        state = '評価中'
    if not done and any(t['state']=='unknown' for t in targets):
        state = '対応未確認'
    if row['status'] == 'running':
        state = '処理中'
    elif row['status'] == 'error' and not targets:
        state = '処理エラー'
    return {'state': state, 'complete': complete, 'targets': targets, 'target_set_known': not unknown}


def analysis_details(index, run, cursor, row):
    if row['agent'] in ('EvidenceEvaluator','DominanceReader'):
        return row
    with index.connect(run) as db:
        units, links = {}, {}
        for r in db.execute('SELECT * FROM h12_units WHERE seq<=? ORDER BY seq', (int(cursor),)):
            units.setdefault((r['epoch'],r['oid'],r['rev'],r['hid']),[]).append(dict(r))
        for r in db.execute('SELECT * FROM h12_links WHERE seq<=?', (int(cursor),)):
            links.setdefault((r['epoch'],r['oid'],r['rev']),set()).add(r['hid'])
        row['evaluation'] = classify(db,row,int(cursor),units,links,set())
    return row


def compact(row):
    item = {k: v for k, v in row.items() if k not in ('members','event_refs','outputs','text','request_details','original_fields','original_targets','direct_target')}
    item['text'] = row.get('text', '')[:240]
    fields = row.get('result_fields', [])
    # Two saved original items are visible in the card; all are accessible on select.
    if row.get('pane') == 'evaluations':
        fields = fields[:4] + [{**f, 'label': '元結果 · ' + f['label']} for f in row.get('original_fields', [])[:2]]
    item['field_count'] = len(row.get('result_fields', [])) + len(row.get('original_fields', []))
    item['result_fields'] = [{**f, 'value': f['value'][:180] if isinstance(f['value'], str) else f['value']} for f in fields[:6]]
    item['source_count'] = len(row.get('source_refs', []))
    item['source_refs'] = row.get('source_refs', [])[:2]
    if 'aggregation' in item:
        item['aggregation'] = {k: v for k, v in item['aggregation'].items() if k != 'event_refs'}
    if 'evaluation' in item:
        item['evaluation'] = {k: v for k, v in item['evaluation'].items() if k != 'targets'}
    return item


def page(index, run, position, log_offset=0, evaluation_offset=0, log_anchor='', evaluation_anchor='', keep=''):
    status = index.status(run)
    meta = status['activity']
    position = float(position)
    if not math.isfinite(position) or position < 0:
        raise ValueError('record_position')
    protected = {x for x in (log_anchor, keep) if x}
    with index.connect(run) as db:
        cursor = (db.execute('SELECT max(seq) FROM events WHERE elapsed<=?', (position,)).fetchone()[0] or 0) if meta['mode']=='elapsed' else min(int(position), status['events'])
        units, links, failures = {}, {}, set()
        for r in db.execute('SELECT * FROM h12_units WHERE seq<=? ORDER BY seq', (cursor,)):
            units.setdefault((r['epoch'], r['oid'], r['rev'], r['hid']), []).append(dict(r))
        for r in db.execute('SELECT * FROM h12_links WHERE seq<=?', (cursor,)):
            links.setdefault((r['epoch'], r['oid'], r['rev']), set()).add(r['hid'])
        # Materialize only derived row metadata, never full raw run/display bodies.
        raw = db.execute('SELECT body FROM activity a WHERE seq=(SELECT max(seq) FROM activity b WHERE b.row_id=a.row_id AND seq<=?)', (cursor,)).fetchall()
        stopped_epochs = {r[0] for r in db.execute("SELECT DISTINCT epoch FROM events WHERE kind='stop' AND seq<=?", (cursor,))}
        logs = []
        for r in raw:
            row = json.loads(r[0])
            if row['agent'] in ('EvidenceEvaluator', 'DominanceReader'):
                continue
            row['evaluation'] = classify(db, row, cursor, units, links, failures)
            row['evaluation_state'] = row['evaluation']['state']
            if row['epoch'] in stopped_epochs and row['evaluation_state'] in ('評価中','評価待ち'):
                row['evaluation_state'] = '未評価 · 停止中'
            row['state_cursor'] = cursor
            row['pane'] = 'logs'
            if not row['evaluation']['complete'] or row['row_id'] in protected:
                logs.append(present(row, run))
        cards = [present(json.loads(r[0]), run) for r in db.execute('SELECT body FROM h12_cards a WHERE seq=(SELECT max(seq) FROM h12_cards b WHERE b.row_id=a.row_id AND seq<=?)', (cursor,))]
        invalid = {(r['epoch'],r['id']): r['reason'] for r in db.execute('SELECT * FROM h12_invalid WHERE seq<=?', (cursor,))}
        clock = db.execute("SELECT seq,epoch,media,elapsed,state FROM events WHERE kind='display' AND seq<=? ORDER BY seq DESC LIMIT 1", (cursor,)).fetchone()
        next_clock = db.execute("SELECT seq,epoch,media,elapsed,state FROM events WHERE kind='display' AND seq>? ORDER BY seq LIMIT 1", (cursor,)).fetchone()
        next_seq = db.execute('SELECT min(seq) FROM (SELECT seq FROM activity UNION SELECT seq FROM h12_cards) WHERE seq>?', (cursor,)).fetchone()[0]
        progress = status_at(db,cursor)
        for row in cards:
            row['state_cursor'] = cursor
            if (row['epoch'],row.get('evaluation_id')) in invalid or (row['epoch'],row.get('readout_id')) in invalid:
                row['evaluation_state'] += ' · 失効'
            elif clock and row.get('expires_after_media_s') is not None and clock['epoch'] == row['epoch'] and clock['media'] > row['expires_after_media_s']:
                row['evaluation_state'] += ' · 期限切れ'
            if clock and clock['epoch'] != row['epoch']:
                row['evaluation_state'] += ' · 旧区間'
            target = row.get('direct_target') or {}
            if target.get('observation_id'):
                latest = db.execute('SELECT revision FROM targets WHERE id=? AND epoch=? AND seq<=? ORDER BY seq DESC LIMIT 1',
                    (target['observation_id'],row['epoch'],cursor)).fetchone()
                if latest and latest[0] != str(target['revision']):
                    row['evaluation_state'] += ' · 旧版'
    def window(values, offset, anchor):
        values.sort(key=lambda r: (r.get('result_seq', r['birth_cursor']), r['row_id']), reverse=True)
        offset = max(0, int(offset))
        if anchor:
            rank = next((i for i, r in enumerate(values) if r['row_id']==anchor), None)
            if rank is not None:
                offset = max(0, rank-5)  # retain the anchor and a bounded preceding context
        return {'items': [compact(r) for r in values[offset:offset+30]], 'total': len(values), 'offset': offset}
    result = {'meta': meta, 'cursor': cursor, 'clock': dict(clock) if clock else None,
        'evaluation_progress':progress,
        'next_clock': dict(next_clock) if next_clock else None, 'next_cursor': next_seq,
        'logs': window(logs, log_offset, log_anchor), 'evaluations': window(cards, evaluation_offset, evaluation_anchor)}
    # Combined response remains within H1.1's 300 KB cap; two 30-row windows, not
    # two unbounded 60-row responses. Shrink tail rows while retaining anchors.
    while len(json.dumps(result, ensure_ascii=False).encode()) > 300000:
        panes = sorted(('logs','evaluations'), key=lambda k: len(result[k]['items']), reverse=True)
        pane = next((k for k in panes if len(result[k]['items']) > 6), None)
        if pane is None:
            raise ValueError('evaluation_page_size')
        result[pane]['items'].pop()
    return result
