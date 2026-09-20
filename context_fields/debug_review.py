"""H0 review service: saved records and media decode, never inference."""
import base64
import hashlib
import io
import json
import math
import re
from pathlib import Path
from .human_review import HumanReviewStore, safe_export
from .review_index import ReviewIndex, digest, dumps
from .media import Asset, AssetStore, SUPPORTED, probe

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ('media_sync', 'asr_raw', 'visual_observation', 'text_context', 'evidence_evaluation', 'field_state',
            'dominance_readout', 'unknown_triage', 'context_reader', 'review_storage', 'review_export', 'capabilities')
FUTURE = ('future_category', 'future_codex_review', 'future_registry', 'future_asr_detection', 'future_asr_correction', 'future_asr_review')


def feature_builds(root=ROOT):
    # Explicit dependency groups. A documentation-only change does not reset all checks.
    groups = {
        'media_sync': ['context_fields/media.py', 'context_fields/debug_review.py', 'context_fields/debug_server.py', 'web/debug.js'],
        'asr_raw': ['context_fields/transcript.py', 'context_fields/review_index.py', 'web/debug.js'],
        'visual_observation': ['context_fields/review_index.py', 'context_fields/debug_review.py', 'web/debug.js'],
        'text_context': ['context_fields/review_index.py', 'web/debug.js'],
        'evidence_evaluation': ['context_fields/jev.py', 'context_fields/review_index.py', 'web/debug.js'],
        'field_state': ['context_fields/fields.py', 'web/app.js', 'web/debug.js'],
        'dominance_readout': ['context_fields/fields.py', 'context_fields/jev.py', 'web/app.js', 'web/debug.js'],
        'unknown_triage': ['context_fields/triage.py', 'web/app.js', 'web/debug.js'],
        'context_reader': ['context_fields/session.py', 'context_fields/review_index.py', 'web/debug.js'],
        'review_storage': ['context_fields/human_review.py', 'context_fields/debug_server.py', 'config/human_review_record.schema.json', 'web/debug.js'],
        'review_export': ['context_fields/human_review.py'],
        'capabilities': ['context_fields/debug_review.py', 'context_fields/debug_local_server.py'],
    }
    for feature, files in groups.items():
        if feature in ('evidence_evaluation', 'dominance_readout', 'context_reader'):
            files.extend(['context_fields/parallel_dispatcher.py', 'context_fields/dispatch_policy.py',
                          'context_fields/question_batch.py', 'context_fields/session.py'])
        if feature not in ('review_export', 'capabilities'):
            files.extend(['context_fields/activity.py', 'context_fields/evaluation_lists.py', 'context_fields/evaluation_status.py', 'context_fields/score_policy.py', 'context_fields/structured_results.py', 'context_fields/thumbnail_cache.py',
                          'web/activity.js', 'web/activity.css', 'web/activity-view.js'])
    return {k: hashlib.sha256(dumps([(p, digest(root / p) if (root / p).is_file() else 'missing') for p in files]).encode()).hexdigest()
            for k, files in groups.items()}


def frame_at(asset, stamp, direction='at', stream_index=None):
    """Adjacent decoded PTS, including VFR and a nonzero container origin."""
    import av
    if not math.isfinite(stamp) or not 0 <= stamp <= asset.metadata['duration_s'] or direction not in ('at', 'prev', 'next'):
        raise ValueError('frame_time_or_direction')
    origin = asset.metadata['origin_s']
    before, after = None, None
    with av.open(str(asset.path)) as container:
        stream = container.streams.video[0] if stream_index is None else next((s for s in container.streams.video if s.index == stream_index), None)
        if stream is None:
            raise ValueError('source_video_stream_missing')
        container.seek(int((max(0, stamp - 2) + origin) / float(stream.time_base)), stream=stream, backward=True)
        for frame in container.decode(stream):
            if frame.pts is None:
                raise ValueError('frame_pts_missing')
            t = float(frame.pts * frame.time_base) - origin
            if (direction == 'prev' and t < stamp - 1e-8) or (direction != 'prev' and t <= stamp + 1e-8):
                before = (frame, t)
                continue
            after = (frame, t)
            break
        chosen = after if direction == 'next' else before
        if chosen is None:
            return {'frame': None, 'missing_reasons': ['adjacent_frame_unavailable']}
        frame, t = chosen
        image = frame.to_image()
        image.thumbnail((1280, 720))
        buf = io.BytesIO()
        image.save(buf, format='JPEG', quality=88)
        return {'jpeg': base64.b64encode(buf.getvalue()).decode(), 'media_s': t, 'pts': frame.pts,
                'time_base': str(frame.time_base), 'origin_s': origin, 'stream': stream.index,
                'source_dimensions': [frame.width, frame.height], 'preview_dimensions': list(image.size),
                'match_kind': 'exact' if abs(t - stamp) < 1e-8 else 'covering_interval',
                'pixel_provenance': 'reconstructed_from_source_not_saved_model_input',
                'missing_reasons': ['LEGACY_MISSING:model_input_pixels'], 'asset_sha256': asset.sha256}


class DebugFeatureAdapter:
    """Small extension seam; future adapters must supply real saved records."""
    def __init__(self, service, feature):
        self.service, self.feature = service, feature

    def capability(self):
        return {'feature_id': self.feature, 'status': 'planned' if self.feature in FUTURE else 'implemented',
                'build': self.service.builds.get(self.feature), 'results': None if self.feature in FUTURE else 'saved_only'}

    def list_targets(self, run, cursor, **page):
        return {'items': [], 'total': 0, 'reason': 'planned'} if self.feature in FUTURE else self.service.index.targets(run, cursor, **page)

    def get_target(self, run, cursor, key):
        if self.feature in FUTURE:
            raise ValueError('planned_no_results')
        return self.service.target(run, cursor, key)

    def get_evidence(self, run, cursor, key):
        return self.get_target(run, cursor, key)['source_refs']

    def review_schema(self):
        return self.service.reviews.schema

    def export_reference(self, value):
        return safe_export(value)


class DebugReview:
    def __init__(self, root=ROOT, fixture=False):
        self.root = Path(root)
        self.index = ReviewIndex(self.root / 'artifacts/sessions', self.root / 'artifacts/human-review-index')
        self.assets = AssetStore(self.root / 'artifacts/media')
        self.reviews = HumanReviewStore(self.root / 'human-review', fixture=fixture)
        self.builds = feature_builds()
        self.build = hashlib.sha256(dumps(self.builds).encode()).hexdigest()
        from .thumbnail_cache import ThumbnailCache
        self.thumbnails = ThumbnailCache(self)

    def capabilities(self):
        return [DebugFeatureAdapter(self, f).capability() for f in FEATURES + FUTURE] + [
            {'feature_id': 'LIVE_JEV', 'status': 'disabled', 'reason': 'H0_no_transport_no_key_no_budget'},
            {'feature_id': 'LOCAL_REAL_EVAL_MOCK', 'status': 'implemented', 'reason': 'explicit_selected_media_start_in_separate_keyless_process'}]

    def register_managed(self, run):
        expected = self.index.status(run).get('asset', {})
        aid = expected.get('id', '')
        if not re.fullmatch(r'asset-[a-f0-9]{32}', aid) or not expected.get('sha256'):
            raise ValueError('LEGACY_MISSING:managed_asset_identity')
        matches = [self.assets.root / (aid + ext) for ext in SUPPORTED if (self.assets.root / (aid + ext)).is_file()]
        if len(matches) != 1 or matches[0].resolve().parent != self.assets.root:
            raise ValueError('managed_asset_missing_or_ambiguous_select_file')
        path = matches[0]
        sha = digest(path)
        if sha != expected['sha256'] or path.stat().st_size != expected['bytes']:
            raise ValueError('managed_asset_hash_mismatch')
        asset = Asset(aid, path, aid + path.suffix, path.stat().st_size, sha, probe(path))
        with self.assets.lock:
            self.assets.assets[aid] = asset
        return asset.public()

    def evidence_frame(self, run, cursor, key, asset_id):
        if self.match(run, asset_id)['media_match'] != 'hash_verified':
            raise ValueError('verify_media_before_source_frame')
        target = self.target(run, cursor, key)
        ref = next((r for r in target['source_refs'] if r['kind'] == 'video_frame' and r['media_range']), None)
        if ref is None:
            raise ValueError('LEGACY_MISSING:source_frame_time')
        result = frame_at(self.assets.get(asset_id), ref['media_range']['start_s'], stream_index=int(ref['stream']) if ref.get('stream') is not None else None)
        result.update(target_id=target['id'], subject=(target['body'] or {}).get('subject'),
                      recorded_pts=ref['pts'], recorded_time_base=ref['time_base'], crop=None)
        roi = (target['body'] or {}).get('roi')
        if roi and result.get('jpeg') and len(roi) == 4 and all(type(x) in (int, float) and math.isfinite(x) for x in roi):
            from PIL import Image
            original = Image.open(io.BytesIO(base64.b64decode(result['jpeg'])))
            input_width = min(640, result['source_dimensions'][0])
            input_height = round(result['source_dimensions'][1] * input_width / result['source_dimensions'][0])
            x, y, w, h = roi
            if 0 <= x < x + w <= input_width and 0 <= y < y + h <= input_height:
                sx, sy = original.width / input_width, original.height / input_height
                crop = original.crop((round(x * sx), round(y * sy), round((x + w) * sx), round((y + h) * sy)))
                buf = io.BytesIO()
                crop.save(buf, format='JPEG')
                result['crop'] = {'jpeg': base64.b64encode(buf.getvalue()).decode(), 'roi': roi,
                                  'coordinate_space': [input_width, input_height], 'basis': 'saved_roi_on_reconstructed_cpu_input',
                                  'same_model_pixels': False}
        return result

    def match(self, run, asset_id):
        asset = self.assets.get(asset_id) if asset_id else None
        if not run:
            return {'media_match': 'not_applicable', 'reason': 'MEDIA_ONLY_REVIEW'}
        expected = self.index.status(run).get('asset', {})
        status = 'missing' if not asset else 'unverified' if not expected.get('sha256') else 'hash_verified' if expected['sha256'] == asset.sha256 and expected.get('bytes') == asset.size else 'mismatch'
        return {'media_match': status, 'expected_sha256': expected.get('sha256'), 'selected_sha256': asset.sha256 if asset else None}

    def target(self, run, cursor, key):
        cursor = int(cursor)
        self.index.event(run, cursor)  # Reject a cursor that never existed.
        feature = 'field_state'
        if key.startswith('r:'):
            row = self.index.target(run, cursor, key)
            body, rid, revision = row['body'], row['id'], row['revision']
            feature = 'asr_raw' if row['kind'] == 'transcript' or body.get('agent') == 'SpeechASR' else 'visual_observation' if body.get('agent') in ('FrameInterpreter', 'FaceTrack', 'MotionCut', 'FaceExpression') else 'text_context' if body.get('agent') == 'TextContext' else 'evidence_evaluation' if 'evaluation' in row['kind'] else 'dominance_readout' if 'readout' in row['kind'] else 'field_state'
            epoch = row['epoch']
        elif key.startswith('e:'):
            seq = int(key[2:])
            if seq > cursor:
                raise ValueError('future_event')
            row = self.index.event(run, seq)
            body, rid, revision, epoch = row['payload'], key, 1, row.get('epoch')
            feature = 'evidence_evaluation' if row['kind'] in ('evaluation_requested', 'dispatcher_result','jev_lifecycle','jev_diagnostic_result') else 'context_reader'
        elif key.startswith('s:'):
            frame = self.index.display(run, cursor)['frame']
            if frame is None:
                raise ValueError('display_missing')
            _, space, kind, ident = key.split(':', 3)
            if kind == 'unknown':
                triage = frame.get('unknown_triage') or {}
                body = {'UNKNOWN': frame['snapshot']['spaces'][space]['UNKNOWN'], 'triage': triage.get('spaces', {}).get(space),
                        'unmapped_observations': [r for r in triage.get('unmapped_observation_index', []) if r.get('space') == space]}
                feature = 'unknown_triage'
            elif kind == 'hypothesis':
                body = next((r for r in frame['snapshot']['spaces'][space]['hypotheses'] if r['id'] == ident), None)
                if body is None:
                    raise ValueError('snapshot_target_missing')
            elif kind == 'snapshot':
                body = frame['snapshot']
            elif kind == 'readout':
                body = frame.get('readouts', {}).get(space)
                feature = 'dominance_readout'
            else:
                raise ValueError('snapshot_target_kind')
            rid, revision, epoch = key, str(self.index.display(run, cursor)['cursor']), frame['clock']['epoch']
        else:
            raise ValueError('target_key')
        refs = []
        def add_ref(r, kind=None):
            interval = r.get('interval')
            tb = r.get('time_base')
            if r.get('modality') == 'video' and r.get('pts') is not None and tb is not None and r.get('media_origin_s') is not None:
                from fractions import Fraction
                try:
                    point = float(r['pts'] * (Fraction(*tb) if isinstance(tb, list) else Fraction(tb))) - r['media_origin_s']
                    interval = [point, point]
                except (ValueError, TypeError, ZeroDivisionError):
                    interval = None
            refs.append({'kind': kind or ('audio_interval' if r.get('modality') == 'audio' else 'video_frame'),
                         'source_id': r.get('root') or r.get('frame_id'), 'source_revision': None,
                         'stream': r.get('stream'), 'frame_id': r.get('frame_id'),
                         'media_range': {'start_s': interval[0], 'end_s': interval[1]} if interval else None,
                         'pts': r.get('pts'), 'time_base': '/'.join(map(str, tb)) if isinstance(tb, list) else tb,
                         'match_kind': 'reconstructed' if r.get('modality') != 'audio' else 'covering_interval',
                         'missing_reasons': ['LEGACY_MISSING:model_input_pixels'] if r.get('modality') != 'audio' else []})
        for r in (body or {}).get('source_refs', []) + (body or {}).get('audio_sample_refs', []):
            if isinstance(r, dict):
                add_ref(r)
        if 'media_start_s' in (body or {}):
            refs.insert(0, {'kind': 'audio_interval', 'source_id': body.get('segment_id'), 'source_revision': body.get('revision'),
                           'media_range': {'start_s': body['media_start_s'], 'end_s': body['media_end_s']},
                           'pts': None, 'time_base': None, 'match_kind': 'covering_interval', 'missing_reasons': ['segment_times_not_sample_exact']})
        if not refs and body:
            # Only recorded dependencies or explicit input_cursor establish source time.
            queue = list(body.get('dependencies', {}).items())
            if body.get('observation_id'):
                queue.append((body['observation_id'], body.get('dependencies', {}).get(body['observation_id'])))
            input_cursor = body.get('input_cursor')
            if type(input_cursor) is int and 0 < input_cursor <= cursor:
                source_event = self.index.event(run, input_cursor)
                if source_event['kind'] == 'record':
                    queue.append((source_event['payload']['id'], source_event['payload'].get('revision')))
            visited = set()
            while queue and len(visited) < 32:
                dep, required = queue.pop(0)
                if (dep, required) in visited:
                    continue
                visited.add((dep, required))
                linked = self.index.record(run, cursor, dep)
                if not linked:
                    continue
                rb = linked['body']
                if required is not None and str(required) != str(rb.get('revision')):
                    continue
                if rb.get('epoch', epoch) != epoch:
                    continue
                for source in rb.get('source_refs', []) + rb.get('audio_sample_refs', []):
                    add_ref(source)
                if not rb.get('source_refs') and not rb.get('audio_sample_refs'):
                    queue.extend(rb.get('dependencies', {}).items())
                    if rb.get('observation_id'):
                        queue.append((rb['observation_id'], rb.get('dependencies', {}).get(rb['observation_id'])))
        links = self.index.links(run, cursor, key) if key.startswith('r:') else {'items': [], 'basis': 'saved_reference_ids'}
        if not key.startswith('r:'):
            def reference_strings(v):
                if isinstance(v, str) and len(v) < 200:
                    yield v
                elif isinstance(v, dict):
                    for a, b in v.items():
                        yield a
                        yield from reference_strings(b)
                elif isinstance(v, list):
                    for b in v:
                        yield from reference_strings(b)
            for ref in set(reference_strings(body)):
                linked = self.index.record(run, cursor, ref)
                if linked:
                    links['items'].append({'key': linked['key'], 'id': ref, 'direction': 'saved_reference'})
        return {'key': key, 'id': rid, 'revision': revision, 'epoch': epoch, 'feature_id': feature, 'body': body,
                'source_refs': refs, 'missing_reasons': [] if refs else ['LEGACY_MISSING:direct_media_refs'],
                'source_as_of_media_s': (body or {}).get('as_of_media_s', (body or {}).get('input_cutoff_s')),
                'display': self.index.display(run, cursor)['cursor'],
                'links': links}

    def pin(self, request):
        run, cursor, key = request.get('run'), request.get('cursor'), request.get('key')
        if request.get('diagnostic_id'):
            sample = next((r for r in self.diagnostics()['items'] if r['sample_id'] == request['diagnostic_id']), None)
            if sample is None:
                raise ValueError('diagnostic_missing')
            target = {'target_kind': 'diagnostic_record', 'target_id': sample['sample_id'], 'feature_id': 'evidence_evaluation',
                      'run_id': sample.get('run'), 'epoch': None, 'event_cursor': None, 'record_ref': sample['sample_id'],
                      'record_version': 'saved_static_comparison', 'asset_sha256': None, 'source_refs': [],
                      'build_ref': self.build, 'policy_ref': 'C0; CP/CD_unapplied_static', 'registry_ref': None,
                      'media_match': 'not_applicable', 'missing_reasons': ['diagnostic_not_video_target']}
            context = {'mode': 'STATIC_POLICY_COMPARE', 'review_playhead_media_s': None, 'source_as_of_media_s': None,
                       'recorded_display_media_s': None, 'pinned_cursor': None, 'review_basis': 'diagnostic_only',
                       'model_output_exposure': 'shown_before_annotation', 'future_content_exposure': 'unknown',
                       'reviewed_evidence': [], 'human_evidence_confirmation': False}
            return self.reviews.pin(target, context, self.builds['evidence_evaluation'])
        asset_id = request.get('asset_id')
        asset = self.assets.get(asset_id) if asset_id else None
        player = request.get('playhead')
        if player is not None and (type(player) not in (int, float) or not math.isfinite(player) or player < 0 or not asset or player > asset.metadata['duration_s']):
            raise ValueError('playhead_range')
        if run:
            t = self.target(run, cursor, key)
            frame = self.index.display(run, cursor)['frame']
            match = self.match(run, asset_id)['media_match']
            policy = frame['snapshot'].get('config_hash') if frame else None
            registry = hashlib.sha256(dumps(frame.get('profiles', [])).encode()).hexdigest() if frame else None
        else:
            if not asset:
                raise ValueError('select_video_or_run')
            interval = request.get('interval', [player, player])
            if len(interval) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in interval) or not 0 <= interval[0] <= interval[1] <= asset.metadata['duration_s']:
                raise ValueError('media_range')
            t = dict(id='media:' + asset.sha256 + ':' + dumps(interval), revision=None, epoch=None, feature_id='media_sync',
                     source_refs=[{'kind': 'video_interval', 'source_id': asset.id, 'source_revision': None,
                                   'media_range': {'start_s': interval[0], 'end_s': interval[1]}, 'pts': None, 'time_base': None,
                                   'match_kind': 'covering_interval', 'missing_reasons': []}], missing_reasons=[])
            frame, match, policy, registry = None, 'not_applicable', None, None
        source = t.get('source_as_of_media_s')
        if source is None:
            source = next((r['media_range']['end_s'] for r in t['source_refs'] if r['media_range']), None)
        target = {'target_kind': 'model_record' if run else 'media_span', 'target_id': t['id'], 'feature_id': t['feature_id'],
                  'run_id': run, 'epoch': t['epoch'], 'event_cursor': int(cursor) if run else None, 'record_ref': key if run else None,
                  'record_version': str(t['revision']) if t['revision'] is not None else None, 'asset_sha256': asset.sha256 if asset else None,
                  # H0 annotation schema stays unchanged; stream/frame metadata remains in the pinned original record.
                  'source_refs': [{k: v for k, v in ref.items() if k not in ('stream', 'frame_id')} for ref in t['source_refs']],
                  'build_ref': self.build, 'policy_ref': policy, 'registry_ref': registry,
                  'media_match': match, 'missing_reasons': t['missing_reasons']}
        context = {'mode': 'REPLAY_EXACT' if run else 'MEDIA_ONLY_REVIEW', 'review_playhead_media_s': player, 'source_as_of_media_s': source,
                   'recorded_display_media_s': frame['clock']['media_s'] if frame else None, 'pinned_cursor': int(cursor) if run else None,
                   'review_basis': 'recorded_input' if run else 'posthoc_past_media',
                   'model_output_exposure': 'shown_before_annotation' if run else 'unknown',
                   'future_content_exposure': 'unknown', 'reviewed_evidence': [], 'human_evidence_confirmation': False}
        feature_check = request.get('feature_check')
        if feature_check:
            if feature_check not in FEATURES:
                raise ValueError('feature_not_implemented')
            target.update(target_kind='feature_check', feature_id=feature_check,
                          target_id='feature:' + feature_check + ':' + target['target_id'])
        if request.get('model_output_exposure') == 'shown_before_annotation':
            context['model_output_exposure'] = 'shown_before_annotation'
        return self.reviews.pin(target, context, self.builds[target['feature_id']])

    def checks(self):
        rows = []
        reviewed = self.reviews.list()
        for feature in FEATURES:
            last = next((r for r in reviewed if r['pin']['target']['feature_id'] == feature and r['annotation']
                         and not r['annotation']['provenance']['is_test_fixture']
                         and r['annotation']['judgment']['review_status'] == 'submitted'), None)
            current = last and last['pin'].get('feature_build') == self.builds[feature]
            judgment = last['annotation']['judgment'] if current else {}
            rows.append({'feature_id': feature, 'implementation': 'implemented', 'build_ref': self.builds[feature],
                         'automatic_test': 'see_H0_report', 'real_media_connection': 'see_H0_report',
                         'human_operation': judgment.get('pipeline_check', {}).get('status', 'pending'),
                         'semantic': judgment.get('semantic_verdict', 'pending'),
                         'review_ref': last['annotation']['record_id'] if last else None,
                         'verified_scope_only': last['pin']['target'] if last else None,
                         'recheck_required': bool(last and not current),
                         'recheck_rule': 'only_if_feature_build_or_review_scope_changed'})
        return {'human_acceptance': 'pending', 'build_ref': self.build, 'checks': rows, 'future': list(FUTURE)}

    def diagnostics(self):
        path = self.root / 'artifacts/score-contract-offline/decision-prep-20260920/comparison.jsonl'
        if not path.is_file():
            return {'items': [], 'missing_reasons': ['saved_static_comparison_missing']}
        rows = []
        with path.open(encoding='utf8') as stream:
            for line in stream:
                r = json.loads(line)
                rows.append({k: r.get(k) for k in ('sample_id', 'run', 'mode', 'question_id', 'numeric', 'eligibility', 'policies', 'evidence_stage', 'request_hash', 'body_sha256')})
        return {'label': 'C0 unchanged; CP/CD static unapplied; diagnostic case has no video timestamp', 'items': safe_export(rows)}
