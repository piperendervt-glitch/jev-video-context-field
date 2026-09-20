"""H1.1 presentation projection. No inference, eval, score adoption or source mutation."""
import hashlib
import json
import math

NAMES = {'MotionCut': 'オプティカルフロー・場面転換', 'AudioMeasure': '音量・声の高さ',
         'EvidenceEvaluator': 'Jev・根拠評価', 'DominanceReader': 'Jev・文脈読取り'}
LABELS = {'description': '説明', 'scene': '場面', 'time_of_day': '時間帯（推定）', 'text': '原文',
          'topic': '話題', 'register': '会話様式', 'quote': '根拠引用', 'mentioned_place': '言及場所',
          'expression': '表情', 'interpretation': '解釈', 'subject': '匿名対象'}
ENUMS = {'time_of_day': {'day': '昼', 'night': '夜', 'twilight': '薄明', 'unknown': '不明'}}


def field(name, label, value, key, target, typ='text', unit=None, reason=None):
    if value is not None and (isinstance(value, (dict, list)) or isinstance(value, float) and not math.isfinite(value)):
        value, reason = None, 'invalid_field_type'
    return dict(id=name, label=label, type=typ, value=value, unit=unit, source_key=key,
                target_ref=target, missing_reason=reason if value is None else None,
                display_value=ENUMS.get(name, {}).get(value) if type(value) in (str, int, float, bool) else None)


def fields_for(agent, payload, target):
    """Only named schemas. Unknown/malformed strings remain in technical originals."""
    if not isinstance(payload, dict):
        return [field('unsupported', '結果', None, 'payload', target, reason='unsupported_schema')]
    p = payload
    if agent in ('MotionCut', 'AudioMeasure'):
        m = p.get('measurement', p if 'flow_mean_px' in p or 'dbfs' in p else None)
        if not isinstance(m, dict):
            return [field('unsupported', '結果', None, 'measurement', target, reason='unsupported_schema')]
        if agent == 'MotionCut':
            why = 'discontinuity' if m.get('discontinuity') else 'cut_detected' if m.get('cut') else 'comparison_or_scale_not_recorded'
            result = [field('flow_mean_px', 'フロー量', m.get('flow_mean_px'), 'measurement.flow_mean_px', target, 'number', reason=why),
                      field('cut', '場面転換', m.get('cut'), 'measurement.cut', target, 'boolean', reason='not_recorded')]
            # Existing logs do not bind their CPU build/scale; no invented px unit.
            if m.get('discontinuity'):
                result.append(field('discontinuity', '比較', '入力が不連続', 'measurement.discontinuity', target))
            return result
        result = [field('dbfs', '音量', m.get('dbfs'), 'measurement.dbfs', target, 'number', 'dBFS', m.get('dbfs_reason') or 'not_recorded'),
                  field('pitch_hz', '声の高さ', m.get('pitch_hz'), 'measurement.pitch_hz', target, 'number', 'Hz', m.get('pitch_reason') or 'not_recorded')]
        if (m.get('clip_fraction') or 0) > 0:
            result.append(field('clip_fraction', 'clip率', m['clip_fraction'], 'measurement.clip_fraction', target, 'number'))
        return result
    if agent in ('EvidenceEvaluator', 'DominanceReader'):
        if agent == 'EvidenceEvaluator':
            parsed = p.get('parsed') or {}
            relation = parsed.get('relation', {}).get('probabilities', {})
            result = [field(k, label, p.get(k, relation.get(r)), k, target, 'number', reason=p.get('reason') or 'not_evaluated')
                      for k, r, label in [('p_support', 'support', '支持'), ('p_contradict', 'contradict', '反証'), ('p_insufficient', 'insufficient', '情報不足')]]
            result.append(field('adoption', '処理状態', p.get('status', '評価記録あり'), 'status', target))
            return result
        result = [field('profile', '切り口', p.get('profile_id'), 'profile_id', target, reason='not_recorded')]
        if p.get('adoption_policy_id')=='cd-distribution-display-v1' and p.get('numeric_diagnostics'):
            result.append(field('calculation','表示計算','分布から算出','adoption_policy_id',target))
            if 'NUMERIC_DISCREPANCY' in p.get('warning_codes',[]):
                result.append(field('numeric_warning','確認事項','数値不一致','warning_codes',target))
        # Only the saved readout supplies historical public values. An evaluation is not display eligibility.
        if p.get('kind') == 'dominance_readout' and isinstance(p.get('items'), dict):
            for name, value in p['items'].items():
                result.append(field(name, '当時公開 · ' + p.get('item_labels', {}).get(name, name), value if p.get('status') == 'valid' else None,
                                    'items.' + name, target, 'number', '%', p.get('reason') or 'not_evaluated'))
        else:
            result.append(field('public', '公開値', None, 'parsed', target, reason=p.get('reason') or 'readout_not_arrived'))
        result.append(field('adoption', '処理状態', p.get('reason') or p.get('status') or '未評価', 'status', target))
        for qid,d in p.get('numeric_diagnostics',{}).items():
            for key,label in [('provider_score','元score'),('mean_used','分布期待値'),('numeric_delta','差（元score−期待値）')]:
                result.append(field(qid+':'+key,qid+' · '+label,d.get(key),'numeric_diagnostics.'+qid+'.'+key,target,'number'))
        return result
    keys = {'SpeechASR': ('text',), 'FrameInterpreter': ('description', 'scene', 'time_of_day'),
            'FaceExpression': ('subject', 'description', 'expression'), 'FaceTrack': ('subject', 'text'),
            'TextContext': ('topic', 'interpretation', 'register', 'quote', 'mentioned_place'),
            'ContextReader': ('description', 'scene', 'time_of_day', 'text')}.get(agent)
    if not keys:
        return [field('unsupported', '結果', None, 'payload', target, reason='unsupported_schema')]
    typed = {k: p[k] for k in keys if k in p and isinstance(p[k], (str, int, float, bool))}
    source_prefix = ''
    if not any(k in typed for k in keys if k != 'subject') and isinstance(p.get('model_raw'), str):
        try:
            raw = json.loads(p['model_raw'])
        except (ValueError, TypeError):
            raw = None
        if isinstance(raw, dict) and any(k in raw for k in keys) and all(raw.get(k) is None or isinstance(raw[k], str) for k in keys):
            typed.update({k: raw[k] for k in keys if k in raw})
            source_prefix = 'model_raw.'
    # Frame observations retain the original description in text; model_raw is a known JSON schema.
    if agent in ('FrameInterpreter', 'FaceExpression', 'TextContext', 'ContextReader') and isinstance(p.get('model_raw'), str):
        try:
            raw = json.loads(p['model_raw'])
        except (ValueError, TypeError):
            raw = None
        if isinstance(raw, dict):
            for k in keys:
                if k not in typed and isinstance(raw.get(k), str):
                    typed[k] = raw[k]
    if agent in ('FrameInterpreter', 'FaceExpression') and not typed.get('description') and isinstance(p.get('text'), str) and not p['text'].lstrip().startswith(('{', '[')):
        typed['description'] = p['text']
    return [field(k, '検出結果' if agent == 'FaceTrack' and k == 'text' else LABELS[k], typed[k],
                  source_prefix + k if k in p else 'model_raw.' + k, target, 'enum' if k in ENUMS else 'text')
            for k in keys if k in typed] or [field('unsupported', '結果', None, 'payload', target, reason='unsupported_schema')]


def source_refs(db, p, cursor, epoch):
    refs, queue, seen, steps = [], [(p, None)], set(), 0
    while queue and steps < 64:
        steps += 1
        body, target = queue.pop(0)
        direct = body.get('source_refs', []) + body.get('audio_sample_refs', [])
        for source in direct:
            if not isinstance(source, dict) or not isinstance(source.get('interval'), list):
                continue
            r = {k: source.get(k) for k in ('root', 'frame_id', 'stream', 'pts', 'time_base', 'media_origin_s', 'modality', 'interval', 'rotation')}
            r['media_s'] = r['interval'][0]
            if r['modality'] == 'video' and r['pts'] is not None and r['time_base'] is not None and r['media_origin_s'] is not None:
                from fractions import Fraction
                tb = r['time_base']
                try:
                    r['media_s'] = float(r['pts'] * (Fraction(*tb) if isinstance(tb, list) else Fraction(tb))) - r['media_origin_s']
                except (ValueError, TypeError, ZeroDivisionError):
                    r['media_s'] = None
            r.update(roi=body.get('roi'), target_ref=target)
            r['ref_id'] = hashlib.sha256(json.dumps({k: v for k, v in r.items() if k != 'target_ref'}, sort_keys=True).encode()).hexdigest()
            if r['ref_id'] not in {x['ref_id'] for x in refs}:
                refs.append(r)
        if direct:
            continue
        deps = list(body.get('dependencies', {}).items())
        if body.get('observation_id'):
            deps.append((body['observation_id'], body.get('dependencies', {}).get(body['observation_id'])))
        if type(body.get('input_cursor')) is int and body['input_cursor'] <= cursor:
            input_row = db.execute('SELECT key,body FROM targets WHERE seq=? LIMIT 1', (body['input_cursor'],)).fetchone()
            if input_row:
                queue.append((json.loads(input_row[1]), input_row[0]))
        for ident, revision in deps:
            if (ident, revision) in seen:
                continue
            seen.add((ident, revision))
            row = db.execute('SELECT key,body FROM targets WHERE id=? AND seq<=? ORDER BY seq DESC LIMIT 1', (ident, cursor)).fetchone()
            if row:
                b = json.loads(row[1])
                if b.get('epoch', epoch) == epoch and (revision is None or str(revision) == str(b.get('revision'))):
                    queue.append((b, row[0]))
    return refs[:32]


def enrich(row, event, target, payload, db):
    """Update one arrived row. No access to a future event or finalized array."""
    agent = row['agent']
    values = fields_for(agent, payload, target)
    previous = row.setdefault('result_fields', [])
    primary = {'place.scene_type': 'scene', 'place.apparent_time_of_day': 'time_of_day',
               'conversation.topic': 'topic', 'conversation.register': 'register', 'person.expression': 'expression',
               'place.mentioned_place': 'mentioned_place'}.get(payload.get('facet'))
    if payload.get('kind') == 'observation' and primary:
        existing_ids = {f['id'] for f in previous}
        # A facet observation owns its matching item. model_raw reprints the other job items;
        # those must not steal their sibling's annotation target or be repeated as a whole row.
        values = [f for f in values if f['id'] == primary or f['id'] in ('description', 'quote', 'subject') and f['id'] not in existing_ids]
    cpu = agent in ('MotionCut', 'AudioMeasure')
    if cpu:
        previous.clear()
        stamp = event.get('elapsed_s')
        row['aggregation'] = {'method': 'latest_arrived', 'window_record_s': [math.floor(stamp), math.floor(stamp) + 1] if type(stamp) in (float, int) and math.isfinite(stamp) else None,
                              'representative_target': target, 'event_refs': list(row['event_refs'])}
    if event['kind'] == 'transcript_version':
        if not row.get('has_transcript'):
            previous.clear()
        row['has_transcript'] = True
        for f in values:
            f['id'] += ':' + str(payload.get('segment_id'))
            f['label'] = '原文 · 改訂 ' + str(payload.get('revision'))
    if payload.get('kind') == 'dominance_readout':
        prefix = str(payload.get('profile_id') or '') + ':'
        previous[:] = [f for f in previous if f['id'] != prefix + 'public']
    for f in values:
        if agent in ('EvidenceEvaluator', 'DominanceReader'):
            f['id'] = str(payload.get('profile_id') or payload.get('hypothesis_id') or '') + ':' + f['id']
        old = next((x for x in previous if x['id'] == f['id']), None)
        if old:
            if old['value'] == f['value'] or cpu or event['kind'] == 'transcript_version':
                old.update(f)
            elif old['target_ref'] != target:
                f['id'] += ':' + target
                previous.append(f)
            else:
                old.update(f)
        else:
            previous.append(f)
    refs = source_refs(db, payload, event['event_seq'], event.get('epoch'))
    if refs:
        # A CPU bucket's images correspond to its displayed representative only.
        row['source_refs'] = refs if cpu or payload.get('roi') and agent in ('FaceTrack', 'FaceExpression') else list({r['ref_id']: r for r in row.get('source_refs', []) + refs}.values())
    row['source_media_ranges'] = [r['interval'] for r in row.get('source_refs', [])]
    row['missing_fields'] = [f['missing_reason'] for f in previous if f['missing_reason']]
    if agent == 'MotionCut':
        images = [r for r in row.get('source_refs', []) if r['modality'] == 'video']
        row['comparison'] = {'image_count': len(images), 'missing': None if len(images) == 2 else 'comparison_frame_missing',
                             'interval_s': abs(images[0]['media_s'] - images[1]['media_s']) if len(images) == 2 and all(x['media_s'] is not None for x in images) else None,
                             'scale': 'LEGACY_MISSING:recorded_cpu_build_and_scale', 'vectors': 'not_saved'}
        row['comparison']['implementation_reference'] = {'file': 'context_fields/observers.py:MotionCut.measure',
            'current_code_not_historical_build_proof': True, 'resize': [160, 90],
            'flow': 'Farneback; mean(norm(flow,axis=2)); displacement per image pair',
            'cut': 'mean(abs(gray-previous))/255 > 0.32; not probability'}
    row['thumbnail_state'] = 'pending' if any(r['modality'] == 'video' for r in row.get('source_refs', [])) else 'audio' if any(r['modality'] == 'audio' for r in row.get('source_refs', [])) else 'missing'
    row['adoption_state'] = payload.get('status') or ('recorded_evaluation' if 'evaluation' in payload.get('kind', '') else 'not_an_adoption_decision')


def present(row, run):
    result = dict(row)
    result.update(run_id=run, revision=row['available_cursor'], event_seq=row['available_cursor'],
                  processing_state=row['status'], available_at=row['result_record_s'],
                  target_ref=row['target_key'], producer=NAMES.get(row['agent'], row['producer']))
    if row.get('logical_id'):result['revision']=row['result_seq']
    for key, default in [('result_fields', []), ('source_refs', []), ('source_media_ranges', []), ('missing_fields', []),
                         ('thumbnail_ref', None), ('thumbnail_state', 'missing'), ('adoption_state', 'pending')]:
        result.setdefault(key, default)
    return result
