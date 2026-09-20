"""Read fixed saved-log prefixes; independent Decimal arithmetic, never Jev I/O.

Decimals describe serialized application log tokens, NOT original HTTP lexemes.
Production validation is called only in a separate comparison path.
"""
from collections import Counter
from decimal import Decimal, localcontext
import hashlib
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.offline_guard import install, ROOT, COUNTS
install()

D = Decimal
TOLERANCE = D('0.000001')
MISSING = 'LEGACY_MISSING'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, D)):
        raise ValueError('not_number')
    result = D(str(value))
    if not result.is_finite():
        raise ValueError('not_finite')
    return result


def independent_score(answer, question):
    """No application imports, no normalization in the primary expected value."""
    result = {'errors': [], 'decimal_token_origin': 'application_json_reserialization'}
    try:
        if not isinstance(answer, dict) or answer.get('type') != 'score':
            raise ValueError('answer_type')
        criteria = question.get('criteria')
        if not isinstance(criteria, list) or len(criteria) != 5:
            raise ValueError('criteria')
        legend = {str(i): text for i, text in enumerate(criteria)}
        if answer.get('legend') != legend:
            result['errors'].append('score_legend')
        p = answer.get('probabilities')
        if not isinstance(p, dict) or set(p) != set(legend):
            raise ValueError('probability_keys')
        values = {key: number(value) for key, value in p.items()}
        score, confidence = number(answer.get('score')), number(answer.get('confidence'))
        if not D(0) <= score <= D(4):
            result['errors'].append('score_range')
        if not D(0) <= confidence <= D(1):
            result['errors'].append('confidence_range')
        if any(not D(0) <= value <= D(1) for value in values.values()):
            result['errors'].append('probability_range')
        with localcontext() as ctx:
            ctx.prec = 60
            total = sum(values.values(), D(0))
            expected = sum(D(key)*value for key, value in values.items())
            delta = score - expected
            result.update(score=score, probabilities=values, confidence=confidence,
                          probability_sum=total, expected_raw=expected,
                          delta_raw=delta, absolute_delta_raw=abs(delta),
                          normalization_allowed=abs(total-1) <= TOLERANCE and not result['errors'])
            if abs(total-1) > TOLERANCE:
                result['errors'].append('probability_sum')
            if abs(delta) > TOLERANCE:
                result['errors'].append('score_expectation_raw')
            if total and result['normalization_allowed']:
                result['expected_if_normalized'] = expected/total
                result['delta_if_normalized'] = score-expected/total
                result['normalized_consistent'] = abs(score-expected/total) <= TOLERANCE
    except (ValueError, TypeError, KeyError) as exc:
        result['errors'].append(str(exc))
    result['raw_consistent'] = not result['errors']
    return result


def production_comparison(answer, question):
    from context_fields.jev import validate_answer
    # Reparse the saved dictionary exactly as the application uses ordinary JSON floats.
    a = json.loads(json.dumps(answer, default=float))
    result = {}
    try:
        p = a['probabilities']; total = sum(p.values())
        result.update(float_sum=total, float_expected_raw=sum(int(k)*v for k, v in p.items()),
                      float_expected_normalized=sum(int(k)*v/total for k, v in p.items()) if total else None)
        result['parsed'] = validate_answer(a, question)
        result['status'] = 'accepted'
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        result.update(status='rejected', reason=str(exc))
    return result


def validate_all_saved_answers(raw, questions):
    from context_fields.jev import validate_answer
    checks={}
    for qid, q in questions.items():
        try:
            a=json.loads(json.dumps(raw['answers'][qid],default=float))
            checks[qid]={'type':q['type'],'status':'accepted','parsed':validate_answer(a,q)}
        except (ValueError,KeyError,TypeError,AttributeError) as exc:
            checks[qid]={'type':q['type'],'status':'rejected','reason':str(exc)}
    return checks


def frozen_events(path, size):
    remaining = size
    with path.open('rb') as stream:
        while remaining:
            line = stream.readline(remaining)
            remaining -= len(line)
            if not line or not line.endswith(b'\n'):
                raise ValueError('incomplete_frozen_event')
            yield json.loads(line), line


def merge_intervals(intervals):
    result = []
    for a, b in sorted(intervals):
        if b < a:
            raise ValueError('interval_order')
        if result and a <= result[-1][1]:
            result[-1][1] = max(b, result[-1][1])
        else:
            result.append([a, b])
    return result


def uncovered(released, covered):
    result = []
    for a, b in merge_intervals(released):
        cursor = a
        for c, d in merge_intervals(covered):
            if d <= cursor or c >= b:
                continue
            if c > cursor:
                result.append([cursor, min(c, b)])
            cursor = max(cursor, min(d, b))
        if cursor < b:
            result.append([cursor, b])
    return result


def inspect_run(path, size):
    requests, evaluations, readouts, profiles = {}, {}, [], []
    dispatch = {}; displays = []; counts = Counter(); epochs = {}; records = {}
    asr_jobs = []; asr_inputs = []; transcripts = []; decode_by_source = {}
    score_record_copies = 0; pending_asr_completion = None; accepted_jobs = []
    requests_seen = []
    last = None; media_id=None; asset_info=None; clock_states=Counter()
    for event, line in frozen_events(path, size):
        k, p, seq = event['kind'], event['payload'], event['event_seq']
        epoch = event['epoch']; counts[k] += 1
        media_id=event['media_id']
        if k=='local_asset':asset_info={key:p.get(key) for key in ('id','sha256','has_audio','duration_s','analysis_start_s','analysis_end_s')}
        ep = epochs.setdefault(epoch, {'released': [], 'decode': [], 'accepted_asr_inputs': [], 'segments': []})
        if k == 'evaluation_requested':
            h = digest(p['request'].encode('utf-8'))
            requests.setdefault(h, []).append({'event': seq, 'epoch': epoch, 'mode': p.get('mode'),
                'text': p['request'], 'body': json.loads(p['request'])})
            requests_seen.append(h)
        if k == 'record':
            records[p['id']] = (seq, p)
            score_record_copies += sum(a.get('type')=='score' for a in (p.get('raw') or {}).get('answers',{}).values())
            if p.get('kind') == 'dominance_evaluation':
                # Parse numerical tokens directly from the saved JSON line as Decimal.
                evaluations[p['id']] = (seq, json.loads(line, parse_float=D)['payload'])
            if p.get('kind') == 'dominance_readout':
                readouts.append((seq, p))
        if k == 'atomic_replacement':
            for r in p['records']:
                records[r['id']] = (seq, r)
        if k == 'profile_registered':
            profiles.append({'event': seq, 'profile': p})
        if k == 'decode_completed':
            if p.get('audio_interval'):
                ep['decode'].append(p['audio_interval'])
                decode_by_source[(epoch, p.get('completed_wall_s'))] = (seq, p)
        if k.startswith('local_') and p.get('agent') == 'SpeechASR':
            job = {'event': seq, 'epoch': epoch, 'kind': k,
                   **{key: val for key, val in p.items() if key not in ('output',)}}
            interval = p.get('input_audio_interval')
            basis = 'explicit_model_input' if interval else MISSING
            refs = []
            # Both decoder completion identity AND the input cursor pointing to AudioMeasure
            # must agree. No matching by time/label alone, and no assumed six-second window.
            dc = decode_by_source.get((epoch, p.get('source_available_wall_s')))
            cursor_record = next(((n, r) for n, r in records.values()
                                  if n == p.get('input_cursor') and r.get('agent') == 'AudioMeasure'), None)
            if not interval and dc and cursor_record:
                n, record = cursor_record
                rs = list(record.get('root_sources', {}).values())
                candidate = dc[1]['audio_interval']
                if rs and candidate[1] == p.get('source_media_s') and n > dc[0] and all(
                        candidate[0] <= src['interval'][0] <= src['interval'][1] <= candidate[1] for src in rs):
                    interval = candidate
                    basis = 'derived_input_cursor_and_decoder_completion_identity'
                    refs = [dc[0], n]
            job.update(input_audio_interval=interval, interval_basis=basis, interval_event_refs=refs)
            if k == 'local_model_completed':
                job.update(output_text_empty=not p.get('output', {}).get('text'), expired=p.get('expired'))
                pending_asr_completion=job
            asr_jobs.append(job)
        if k in ('local_result_discarded','local_model_failed') and p.get('agent')=='SpeechASR':
            pending_asr_completion=None
        if k == 'transcript_version':
            if pending_asr_completion and not pending_asr_completion.get('expired'):
                accepted_jobs.append({**pending_asr_completion,'accepted_transcript_event':seq,
                    'acceptance_basis':'completion followed by transcript_version in single writer event order',
                    'output_status':p.get('status'), 'observation_id':p.get('observation_id')})
                pending_asr_completion=None
            transcripts.append({'event':seq, **{key:val for key,val in p.items() if key not in ('text','words')}})
            if p.get('text') and p.get('status') not in {'cancelled','silence','no_speech_recognized'}:
                ep['segments'].append([p['media_start_s'], p['media_end_s']])
        if k in ('dispatcher_result', 'display'):
            entries = [p] if k == 'dispatcher_result' else p.get('evaluation_status', {}).get('last_events', [])
            for item in entries:
                if 'reservation' in item:
                    key = (bool(item.get('external')), item['reservation']['attempt'])
                    dispatch.setdefault(key, {'first_event': seq, 'event_epoch':epoch, **item})
        if k == 'display':
            last = p
            clock_states[p['clock']['state']]+=1
            ep['released'].extend(p['clock']['released'].get('audio', []))
            displays.append({'event': seq, 'epoch': epoch, 'clock': p['clock'],
                'evaluation_status': p.get('evaluation_status'),
                'readouts': {key: {n:r.get(n) for n in ('id','evaluation_id','status','reason','original_reason','input_snapshot_id','items')}
                             for key,r in p.get('readouts',{}).items()}})
    # Accepted ASR observation explicitly stores its actual input interval, separately
    # from speech segments. Presence does not imply recognition completeness.
    for seq, record in records.values():
        if record.get('agent') == 'SpeechASR' and record.get('observed_s'):
            epoch = record['epoch']; interval = record['observed_s']
            epochs[epoch]['accepted_asr_inputs'].append(interval)
            asr_inputs.append({'event':seq, 'id':record['id'], 'epoch':epoch,
                'input_audio_interval':interval,'basis':'observation.observed_s',
                'sample_refs':record.get('source_refs', MISSING)})
            epochs[epoch]['segments'].extend([x['start_s'],x['end_s']] for x in record.get('segments',[]))
    response_groups = {}
    for seq, ev in evaluations.values():
        raw = ev.get('raw')
        if not isinstance(raw, dict):
            continue
        # Equal payloads at DIFFERENT accept/completion identities remain separate.
        rh = digest(json.dumps(raw, sort_keys=True, default=str).encode())
        key = (ev['epoch'], ev['input_hash'], str(ev.get('accepted_wall_s')), str(ev.get('completed_wall_s')), rh)
        group = response_groups.setdefault(key, {'evaluations':[], 'raw':raw})
        group['evaluations'].append((seq,ev))
    rows, groups = [], []
    for identity, group in response_groups.items():
        epoch, h, accepted, completed, raw_hash = identity
        refs = group['evaluations']; seq, ev = refs[0]; raw=group['raw']
        matches = [r for r in requests.get(h,[]) if r['epoch']==epoch and r['event']<seq]
        request = matches[0] if len(matches)==1 else None
        body = request['body'] if request else {}; qs = body.get('questions',{})
        state = body.get('state',{}); state=json.loads(state) if isinstance(state,str) else state
        units = state.get('units',{})
        evalids = {e['id'] for _,e in refs}
        unit_records = [(n,r) for n,r in readouts if r.get('evaluation_id') in evalids]
        candidates = [d for d in dispatch.values() if d['reservation']['role']=='dominance'
            and d['event_epoch']==epoch and request is not None
            and set(d.get('unit_ids',[]))==set(units)
            and d['reservation']['chars']==len(request['text'])
            and d['reservation']['units']==len(units) and d['reservation']['questions']==len(qs)
            and d.get('accepted_wall_s') is not None and str(D(str(d['accepted_wall_s'])))==accepted
            and str(D(str(d.get('completed_wall_s'))))==completed]
        attempt = candidates[0]['reservation']['attempt'] if len(candidates)==1 else MISSING
        # Old logs omit transport times: keep the attempt unjoined, list nearby evidence
        # separately. Never infer identity merely from error labels or media time.
        meta = {'run':path.stem,'epoch':epoch,'request_hash':h,'reserialized_response_hash':raw_hash,
            'request_event':request['event'] if request else MISSING,'request_match_count':len(matches),
            'attempt':attempt,'accepted_wall_s':accepted,'completed_wall_s':completed,
            'evaluation_events':[n for n,_ in refs], 'readout_events':[n for n,_ in unit_records],
            'snapshot_id':ev.get('input_snapshot_id'), 'input_cursor':ev.get('input_cursor'),
            'mode':ev.get('mode'),'raw_http_body':'NOT_SAVED','http_status':MISSING,
            'missing_reason_codes':['RAW_BODY_MISSING']+(['REQUEST_MISSING_OR_AMBIGUOUS'] if not request else []),
            'attempt_join_basis':'epoch+role+unit_ID_set+units/questions/chars+accepted/completed' if attempt!=MISSING else MISSING,
            'possible_attempts_by_request_shape_only':[d['reservation']['attempt'] for d in dispatch.values()
                if request and d['reservation']['role']=='dominance' and d['reservation']['chars']==len(request['text'])
                and d['reservation']['questions']==len(qs) and d['reservation']['units']==len(units)],
            'http_request_id':MISSING,'duplicate_original_json_keys':'UNDETERMINABLE_AFTER_PARSE',
            'earliest_observable_stage':'application_reserialized_raw_after_json_decode',
            'units':[{'event':n,'space':r['space'],'profile_id':r.get('profile_id',MISSING),
                      'status':r['status'],'reason':r.get('reason'),'items':r['items']} for n,r in unit_records],
            'answers_total':len(raw.get('answers',{})), 'unit_count':len(units)}
        meta['question_checks']=validate_all_saved_answers(raw,qs)
        groups.append(meta)
        expected_ids={qid for qid,q in qs.items() if q.get('type')=='score'}
        observed_ids={qid for qid,a in raw.get('answers',{}).items() if isinstance(a,dict) and a.get('type')=='score'}
        for qid in sorted(expected_ids|observed_ids):
            answer=raw.get('answers',{}).get(qid,{})
            q = qs.get(qid)
            if answer.get('type')!='score' and (not q or q.get('type')!='score'):
                continue
            unitid=qid.rsplit('.',1)[0]
            unit = units.get(unitid,{}) if isinstance(units,dict) else {}
            dec = independent_score(answer,q) if q else {'errors':['request_or_question_missing']}
            prod = production_comparison(answer,q) if q else {'status':'unmatched'}
            if 'float_expected_raw' in prod and 'expected_raw' in dec:
                prod['float64_minus_decimal_expected']=D.from_float(prod['float_expected_raw'])-dec['expected_raw']
            unit_ev = next((e for _,e in refs if qid in (e.get('parsed') or {})),None)
            profile=unit.get('profile') or next((r.get('profile') for _,r in unit_records if r.get('profile_id')==unitid),None)
            matched_readout=next((r for _,r in unit_records if r.get('profile_id')==unitid or r['space']==unitid),{})
            row={**meta,'question_id':qid,'unit_id':unitid,'profile':profile or MISSING,
                 'profile_version':profile['revision'] if profile else matched_readout.get('profile_version',MISSING),
                 'rubric_version':matched_readout.get('rubric_version',MISSING),
                 'candidate_set_version':matched_readout.get('candidate_set_version',MISSING),
                 'criteria':q.get('criteria') if q else MISSING,'legend':answer.get('legend'),
                 'saved_answer':answer,'independent':dec,'production':prod,
                 'parsed_record_event':next((n for n,e in refs if e is unit_ev),MISSING),
                 'classification':'INSUFFICIENT_RAW_EVIDENCE' if prod['status']=='rejected' else 'SAVED_REPRESENTATION_CONSISTENT'}
            rows.append(row)
    coverage={}
    for epoch, ep in epochs.items():
        jobs=[j for j in asr_jobs if j['epoch']==epoch]
        byphase={k:[j['input_audio_interval'] for j in jobs if j['kind']==k and j['input_audio_interval']]
                 for k in ('local_slot_requested','local_model_admitted','local_model_completed')}
        release=merge_intervals(ep['released']);accepted=merge_intervals(ep['accepted_asr_inputs'])
        completed=merge_intervals(byphase['local_model_completed'])
        adopted=merge_intervals(j['input_audio_interval'] for j in accepted_jobs if j['epoch']==epoch and j['input_audio_interval'])
        coverage[str(epoch)]={'released':release,'decoded':merge_intervals(ep['decode']),
            'slot_requested':merge_intervals(byphase['local_slot_requested']),
            'admitted':merge_intervals(byphase['local_model_admitted']), 'completed':completed,
            'accepted_observation_input':accepted,'recognized_segments':merge_intervals(ep['segments']),
            'accepted_output_input_including_no_text':adopted,
            'accepted_output_count':sum(j['epoch']==epoch for j in accepted_jobs),
            'released_minus_known_completed':uncovered(release,completed),
            'released_minus_known_accepted_input':uncovered(release,accepted),
            'counts':dict(Counter(j['kind'] for j in jobs)),
            'missing_interval_counts':dict(Counter(j['kind'] for j in jobs if j['input_audio_interval'] is None)),
            'speech_miss_rate':'UNAVAILABLE_NO_GROUND_TRUTH',
            'gap_meaning':'uncovered by recorded known intervals; unknown jobs may cover it; not proof of missed speech'}
    return {'run':path.stem,'counts':dict(counts),'scores':rows,'response_groups':groups,
        'media_id':media_id,'asset':asset_info,'clock_states':dict(clock_states),
        'score_record_copies':score_record_copies,'deduplicated_score_answers':len(rows),
        'score_record_duplicate_copies':score_record_copies-len(rows),
        'raw_readouts_without_evaluation':[n for n,r in readouts if r.get('evaluation_id') not in evaluations],
        'profiles':profiles,'asr_jobs':asr_jobs,'accepted_asr_inputs':asr_inputs,'transcripts':transcripts,
        'accepted_asr_jobs':accepted_jobs,
        'coverage':coverage,'dispatch':list(dispatch.values()),'display_trace':displays,
        'last_state':{k:last.get(k) for k in ('clock','external','evaluation_status','profiles')} if last else None}


def main():
    task=sys.argv[1] if len(sys.argv)>1 else (ROOT/'artifacts/score-contract-offline/LATEST.txt').read_text().strip()
    out=ROOT/'artifacts/score-contract-offline'/task
    manifest=json.loads((out/'manifest-before.json').read_text(encoding='utf-8'))
    summary=[];all_scores=[]
    for rel, info in manifest['fixed_inputs'].items():
        report=inspect_run(ROOT/rel,info['size']);dump(out/(report['run']+'.json'),report)
        scores=report['scores']
        all_scores.extend(scores)
        summary.append({'run':report['run'],'response_groups':len(report['response_groups']),
            'score_record_copies':report['score_record_copies'],'duplicate_score_copies':report['score_record_duplicate_copies'],
            'score_answers':len(scores),'verdicts':dict(Counter(r['production']['status'] for r in scores)),
            'mismatches':[{'question':r['question_id'],'score':r['independent'].get('score'),
                'expected':r['independent'].get('expected_raw'),'delta':r['independent'].get('delta_raw'),
                'attempt':r['attempt'],'events':r['evaluation_events']} for r in scores if r['production']['status']=='rejected'],
            'last_state':report['last_state'],'coverage':report['coverage']})
    fields=['run_id','request_ref','request_hash','attempt','unit','question','profile_version',
        'rubric_version','candidate_set_version','evidence_level','returned_score','raw_probabilities',
        'probability_sum','expected_raw','abs_delta','expected_normalized','float_expected_raw',
        'first_divergence_stage','validation_result','missing_fields','evaluation_events','readout_events']
    with (out/'score-samples.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for r in all_scores:
            d=r['independent'];prod=r['production']
            writer.writerow(dict(zip(fields,[r['run'],f"artifacts/sessions/{r['run']}.jsonl:event:{r['request_event']}",
                r['request_hash'],r['attempt'],r['unit_id'],r['question_id'],r['profile_version'],
                r['rubric_version'],r['candidate_set_version'],'application-reserialized-json',
                d.get('score'),json.dumps(d.get('probabilities'),default=str),d.get('probability_sum'),
                d.get('expected_raw'),d.get('absolute_delta_raw'),d.get('expected_if_normalized'),
                prod.get('float_expected_raw'),r['earliest_observable_stage'] if prod['status']=='rejected' else 'none_observed',
                prod['status']+':'+str(prod.get('reason','consistent')),
                'RAW_BODY_MISSING;original_number_lexemes;HTTP_status;HTTP_request_ID;duplicate_JSON_keys'+
                (';attempt_join' if r['attempt']==MISSING else '')+(';profile_version' if r['profile_version']==MISSING else ''),
                r['evaluation_events'],r['readout_events']])))
    dump(out/'summary.json',summary);dump(out/'diagnostic-isolation.json',COUNTS)
    print(json.dumps([{k:v for k,v in x.items() if k not in ('last_state','coverage')} for x in summary],default=str,indent=2))


if __name__=='__main__':main()
