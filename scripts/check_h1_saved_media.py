"""Actual saved-run/media connection, plus clearly isolated draft-review fixture.

No human semantic judgment, model call, Jev transport or key is involved.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.offline_guard import install, ALLOWED_PORTS, COUNTS, ROOT
install()
import base64
import json
import tempfile
import threading
import time
import urllib.request
from context_fields.debug_server import make_debug_server
from context_fields.human_review import HumanReviewStore
from context_fields.review_index import digest


def main():
    out = ROOT / 'artifacts/human-debug-h1/20260920'
    out.mkdir(parents=True, exist_ok=True)
    budget = ROOT / 'artifacts/local-private/jev-campaign-v02.json'
    start_budget = budget.read_bytes()
    runs = ['run-1789828491134742200', 'run-1789824695716131300']
    report = {'runs': [], 'human_acceptance': 'pending', 'browser_operation': False, 'listening_or_visual_semantics': 'unperformed'}
    server = make_debug_server(0)
    ALLOWED_PORTS.add(server.server_port)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    token = server.app.token  # memory only, never exported
    base = f'http://127.0.0.1:{server.server_port}'
    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'X-Session-Token': token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    try:
        for run in runs:
            source = ROOT / 'artifacts/sessions' / (run + '.jsonl')
            before = digest(source)
            t = time.perf_counter()
            call('/api/debug/index', {'run': run})
            while True:
                status = call('/api/debug/status?run=' + run)
                if status['status'] != 'indexing':
                    break
                time.sleep(.2)
            assert status['status'] == 'ready', status
            cursor = status['events']
            first = call(f'/api/debug/activity?run={run}&position=0')
            assert not first['items'], 'no preplay future results'
            started = time.perf_counter()
            feed = call(f"/api/debug/activity?run={run}&position={status['activity']['duration']}")
            assert len(feed['items']) <= 60
            page_bytes = len(json.dumps(feed, ensure_ascii=False).encode())
            assert page_bytes < 300000, page_bytes
            activity_checks = {'meta': status['activity'], 'page_bytes': page_bytes,
                'page_seconds': time.perf_counter() - started, 'final_rows': len(feed['items']),
                'first_rows': len(first['items']), 'human_checked': False}
            with server.app.review.index.connect(run) as db:
                activity_rows = db.execute('SELECT body FROM activity').fetchall()
            examples = {}
            for raw_row in activity_rows:
                row = json.loads(raw_row[0])
                if row['agent'] in ('SpeechASR', 'FrameInterpreter', 'EvidenceEvaluator') and row['status'] != 'running':
                    examples.setdefault(row['agent'], row)
            assert 'SpeechASR' in examples and 'FrameInterpreter' in examples and 'EvidenceEvaluator' in examples
            activity_checks['examples'] = []
            for agent, row in examples.items():
                selected = call(f"/api/debug/activity-row?run={run}&cursor={row['available_cursor']}&row_id={row['row_id']}")
                target_key = next((k for k in reversed(selected['members']) if k.startswith('r:')), selected['target_key'])
                detail = call(f"/api/debug/target?run={run}&cursor={row['available_cursor']}&key={target_key}")
                activity_checks['examples'].append({'agent': agent, 'row_id': row['row_id'],
                    'cursor': row['available_cursor'], 'result_record_s': row['result_record_s'],
                    'status': row['status'], 'source_refs': detail['source_refs'], 'member_count': len(selected['members'])})
                assert row['result_record_s'] is not None
            if run == 'run-1789828491134742200':
                pending = call(f'/api/debug/activity?run={run}&position=5.7')
                later = call(f'/api/debug/activity?run={run}&position=5.9')
                pending_visual = [r for r in pending['items'] if r['agent']=='FrameInterpreter']
                later_visual = [r for r in later['items'] if r['agent']=='FrameInterpreter']
                assert any(r['status']=='running' and not r['text'] for r in pending_visual)
                assert any(r['status']=='done' for r in later_visual)
                activity_checks['actual_visual_before_after_arrival'] = [5.7, 5.9]

            media = call('/api/debug/managed-asset', {'run': run})
            match = call(f'/api/debug/match?run={run}&asset_id={media["id"]}')
            assert match['media_match'] == 'hash_verified'
            frame = call(f'/api/debug/frame?asset_id={media["id"]}&time=0&direction=at')
            assert frame['jpeg'] and frame['pts'] is not None
            decoded = base64.b64decode(frame.pop('jpeg'))
            (out / (run + '-reconstructed-frame.jpg')).write_bytes(decoded)
            display = call(f'/api/debug/display?run={run}&cursor={cursor}')
            targets = call(f'/api/debug/targets?run={run}&cursor={cursor}&kind=transcript')
            transcript_kind = 'versioned_transcript'
            if not targets['items']:
                targets = call(f'/api/debug/targets?run={run}&cursor={cursor}&kind=observation&query=SpeechASR')
                transcript_kind = 'LEGACY_MISSING:transcript_version; saved_SpeechASR_observation_only'
            assert targets['items']
            key = targets['items'][0]['key']
            target = call(f'/api/debug/target?run={run}&cursor={cursor}&key={key}')
            assert target['source_refs']
            timeline = call(f'/api/debug/timeline?run={run}&cursor={cursor}')
            timeline_summary = {'asr_input_count': len(timeline['asr_inputs']),
                                'direct': sum(x['basis'] == 'direct' for x in timeline['asr_inputs']),
                                'derived': sum(x['basis'].startswith('derived:') for x in timeline['asr_inputs']),
                                'missing': sum(x['basis'] == 'LEGACY_MISSING' for x in timeline['asr_inputs']),
                                'states_total': timeline['states_total']}
            faces = call(f'/api/debug/targets?run={run}&cursor={cursor}&kind=observation&query=FaceTrack')
            crop_check = {'status': 'no_saved_face_track'}
            if faces['items']:
                face = call(f'/api/debug/evidence-frame?run={run}&cursor={cursor}&key={faces["items"][0]["key"]}&asset_id={media["id"]}')
                assert face['jpeg'] and face['recorded_pts'] is not None
                crop_check = {'status': 'decoded', 'crop_reconstructed': face['crop'] is not None,
                              'model_pixels_exact': False, 'subject': face['subject'], 'pts': face['pts'], 'recorded_pts': face['recorded_pts']}
            assert digest(source) == before
            report['runs'].append({'run': run, 'bytes': source.stat().st_size, 'source_sha256_unchanged': before,
                'events': cursor, 'displays': status['displays'], 'activity_checks': activity_checks, 'index_and_read_seconds': time.perf_counter() - t,
                'media_match': match, 'media_bytes': media['bytes'], 'frame': frame,
                'transcript_targets': targets['total'], 'transcript_kind': transcript_kind, 'timeline': timeline_summary, 'crop_check': crop_check,
                'last_display_epoch': display['frame']['clock']['epoch'], 'last_display_media_s': display['frame']['clock']['media_s']})
            print(json.dumps({'run': run, 'events': cursor, 'match': match['media_match'], 'asr_inputs': timeline_summary}, ensure_ascii=False), flush=True)
        # A draft only, explicitly fixture-scoped. No human evidence or semantic verdict is asserted.
        fixture = out / 'test-fixture-review'
        fixture.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='h1-review-fixture-', dir=out) as td:
            server.app.review.reviews = HumanReviewStore(Path(td), fixture=True)
            pin = call('/api/debug/pin', {'run': run, 'cursor': cursor, 'key': key, 'asset_id': media['id'], 'playhead': .1})
            request = {'review_id': pin['review_id'], 'expected_revision': 0, 'idempotency_key': 'h1-http-test-fixture-0001',
                'reviewer': 'AUTOMATED TEST FIXTURE - NOT A HUMAN REVIEW',
                'judgment': {'review_status': 'draft', 'semantic_verdict': 'unreviewed',
                    'numeric_check': {'status': 'unknown', 'evidence_ids': [], 'check_version': None},
                    'pipeline_check': {'status': 'unknown', 'evidence_ids': [], 'check_version': None},
                    'issue_tags': [], 'severity': 'not_set', 'expected_text': None, 'actual_text': None,
                    'note': 'Synthetic draft to test persistence only. No person listened or judged.', 'requested_action': 'none'},
                'exposure': {'human_evidence_confirmation': False, 'reviewed_evidence': []}}
            ack = call('/api/debug/save', request)
            assert ack['ack'] and ack['record']['provenance']['is_test_fixture']
            assert call('/api/debug/save', request)['duplicate']
            server.app.review.reviews = HumanReviewStore(Path(td), fixture=True)
            assert call('/api/debug/reviews')['items'][0]['annotation']['record_id'] == ack['record']['record_id']
            exported = call('/api/debug/export', {})
            (fixture / 'findings.jsonl').write_text(exported['jsonl'], encoding='utf8')
            (fixture / 'codex-feedback.md').write_text(exported['markdown'], encoding='utf8')
        assert budget.read_bytes() == start_budget
        report.update(budget_start=json.loads(start_budget), budget_end=json.loads(budget.read_bytes()), guard=COUNTS,
                      result='passed', human_annotations_created=0, fixture_drafts_created=1)
        (out / 'saved-media-http.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    finally:
        server.shutdown()
        worker.join(5)
        server.server_close()
        ALLOWED_PORTS.discard(server.server_port)


if __name__ == '__main__':
    main()
