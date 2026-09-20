import copy
import hashlib
import json
import time
from pathlib import Path
import pytest
from context_fields.debug_review import DebugReview
from context_fields.activity import page, row_at


def fixture_events():
    def display(media, state='playing'):
        return {'clock': {'media_s': media, 'state': state, 'epoch': 2}, 'snapshot': {'config_hash': 'fixture'}, 'profiles': []}
    audio = {'id': 'asr-1', 'kind': 'observation', 'revision': 1, 'agent': 'SpeechASR', 'epoch': 2,
             'text': 'same words', 'facet': 'transcript', 'dependencies': {},
             'source_refs': [{'root': 'audio-1', 'modality': 'audio', 'interval': [0, .8], 'pts': 0, 'time_base': [1, 16000]}]}
    visual = {'id': 'image-1', 'kind': 'observation', 'revision': 1, 'agent': 'FrameInterpreter', 'epoch': 2,
              'text': 'same words', 'facet': 'objects', 'dependencies': {},
              'source_refs': [{'root': 'video-1', 'modality': 'video', 'interval': [.4, .4], 'pts': 4, 'time_base': [1, 10]}]}
    records = [(0, 'display', display(0)),
        (.2, 'local_model_admitted', {'agent': 'SpeechASR', 'accepted_wall_s': 10.2, 'input_cursor': 1}),
        (1, 'display', display(1, 'paused')),
        (2, 'local_model_completed', {'agent': 'SpeechASR', 'accepted_wall_s': 10.2, 'input_cursor': 1, 'output': {'text': 'same words'}}),
        (2, 'record', audio),
        (2.2, 'local_model_admitted', {'agent': 'FrameInterpreter', 'accepted_wall_s': 12.2, 'input_cursor': 1}),
        (3, 'local_model_completed', {'agent': 'FrameInterpreter', 'accepted_wall_s': 12.2, 'input_cursor': 1, 'output': {'description': 'same words'}}),
        (3, 'record', visual),
        (3, 'record', {**visual, 'id': 'image-2', 'facet': 'scene'}),
        (3.1, 'record', visual),
        (3.2, 'local_model_admitted', {'agent': 'TextContext', 'accepted_wall_s': 13.2, 'input_cursor': 5}),
        (4, 'local_model_failed', {'agent': 'TextContext', 'accepted_wall_s': 13.2, 'input_cursor': 5, 'reason': 'quote_not_in_asr'}),
        (4, 'display', display(1, 'paused')),
        (5, 'display', display(0)),
        (5.05, 'record', {'id': 'motion-1', 'kind': 'observation', 'revision': 1, 'agent': 'MotionCut', 'measurement': {'flow_mean_px': 1}}),
        (5.1, 'record', {'id': 'motion-2', 'kind': 'observation', 'revision': 1, 'agent': 'MotionCut', 'measurement': {'flow_mean_px': 2}}),
        (6, 'display', display(1)),
        (6.1, 'evaluation_requested', {'role': 'evidence', 'unit_ids': ['asr-1'], 'request': 'fixture'}),
        (6.5, 'record', {'id': 'eval-1', 'revision': 1, 'kind': 'evidence_evaluation', 'evaluation_kind': 'evidence',
                       'accepted_wall_s': 16.1, 'observation_id': 'asr-1', 'dependencies': {'asr-1': 1},
                       'status': 'error', 'reason': 'score_expectation', 'parsed': None, 'raw': {'score': 2.69}}),
        (6.6, 'dispatcher_result', {'status': 'error', 'reason': 'score_expectation', 'unit_ids': ['asr-1']}),
        (7, 'display', display(2))]
    return [{'schema_version': '3', 'event_seq': i, 'epoch': 2 if i < 14 else 3, 'kind': kind,
             'media_s': p['clock']['media_s'] if kind == 'display' else None, 'elapsed_s': elapsed,
             'created_monotonic_s': 10 + elapsed, 'payload': p} for i, (elapsed, kind, p) in enumerate(records, 1)]


def make(tmp_path, events):
    service = DebugReview(tmp_path, fixture=True)
    folder = tmp_path / 'artifacts/sessions';folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'run-123.jsonl'
    path.write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf8')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    service.index.start('run-123')
    end = time.monotonic() + 10
    while service.index.status('run-123')['status'] == 'indexing' and time.monotonic() < end:time.sleep(.01)
    assert service.index.status('run-123')['status'] == 'ready'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    return service


@pytest.fixture
def service(tmp_path):return make(tmp_path, fixture_events())


@pytest.mark.parametrize('required,expected', [(1, False), (2, True)])
def test_nested_source_dependency_revision_is_not_silently_replaced(tmp_path, required, expected):
    events = fixture_events()
    def record(seq, body):
        return {'schema_version': '3', 'event_seq': seq, 'kind': 'record', 'epoch': 3, 'elapsed_s': seq,
                'created_monotonic_s': seq + 10, 'payload': body}
    events.extend([
        record(22, {'id': 'audio-dep', 'revision': 2, 'epoch': 3, 'kind': 'observation', 'agent': 'SpeechASR',
                    'source_refs': [{'root': 'audio', 'modality': 'audio', 'interval': [0, 1]}]}),
        record(23, {'id': 'nested', 'revision': 1, 'epoch': 3, 'kind': 'observation', 'agent': 'TextContext',
                    'dependencies': {'audio-dep': required}}),
        record(24, {'id': 'evaluation', 'revision': 1, 'epoch': 3, 'kind': 'evidence_evaluation',
                    'dependencies': {'nested': 1}})])
    s = make(tmp_path, events)
    target = s.target('run-123', 24, 'r:24:0')
    assert bool(target['source_refs']) is expected


def test_start_pending_then_arrival_without_future_payload(service):
    a = page(service.index, 'run-123', 1.9)
    assert len(a['items']) == 1
    assert a['items'][0]['status'] == 'running' and a['items'][0]['text'] == ''
    assert 'same words' not in json.dumps(a)
    assert a['items'][0]['result_record_s'] is None
    b = page(service.index, 'run-123', 2)
    assert b['items'][0]['row_id'] == a['items'][0]['row_id']
    assert b['items'][0]['status'] == 'done' and 'same words' in b['items'][0]['text']


def test_pause_late_arrival_is_not_video_second(service):
    result = page(service.index, 'run-123', 2)
    assert result['clock']['media'] == 1 and result['clock']['state'] == 'paused'
    assert result['items'][0]['result_record_s'] == 2
    target = service.target('run-123', result['cursor'], 'r:5:0')
    assert target['source_refs'][0]['media_range'] == {'start_s': 0, 'end_s': .8}


def test_same_words_different_producers_not_deduped(service):
    result = page(service.index, 'run-123', 3)
    rows = [r for r in result['items'] if 'same words' in r['text']]
    assert {r['producer'] for r in rows} == {'音声認識', '画像解析'}


def test_multiple_outputs_and_republished_record_are_distinct(service):
    r = row_at(service.index, 'run-123', 10, 'e:6')
    assert {'r:8:0', 'r:9:0', 'r:10:0'} <= set(r['members'])
    assert len(r['outputs']) == 3  # original output and two distinct facets
    assert row_at(service.index, 'run-123', 8, 'e:6')['members'] == ['e:6', 'e:7', 'r:8:0']


def test_failed_job_updates_same_row(service):
    before = page(service.index, 'run-123', 3.3)['items'][-1]
    after = page(service.index, 'run-123', 4)['items'][-1]
    assert before['status'] == 'running'
    assert before['row_id'] == after['row_id']
    assert after['status'] == 'error' and 'quote_not_in_asr' in after['text']


def test_cpu_measurement_buckets_retain_each_event(service):
    r = row_at(service.index, 'run-123', 16, 'cpu:3:MotionCut:5')
    assert r['members'] == ['r:15:0', 'r:16:0']
    assert '2' in r['text']
    assert '1' in row_at(service.index, 'run-123', 15, 'cpu:3:MotionCut:5')['text']


def test_jev_c0_error_not_promoted(service):
    result = page(service.index, 'run-123', 6.6)
    jev = next(r for r in result['items'] if r['agent'] == 'EvidenceEvaluator')
    assert jev['status'] == 'error' and 'score_expectation' in jev['text']
    assert '2.69' not in jev['text']
    assert service.index.target('run-123', result['cursor'], 'r:19:0')['body']['parsed'] is None


def test_no_recorded_start_means_no_fabricated_pending(tmp_path):
    events = [e for e in fixture_events() if e['kind'] != 'local_model_admitted']
    for seq, e in enumerate(events, 1):e['event_seq'] = seq
    s = make(tmp_path, events)
    assert page(s.index, 'run-123', 1.9)['items'] == []
    r = page(s.index, 'run-123', 2)['items'][0]
    assert r['start_record_s'] is None and r['result_record_s'] == 2


@pytest.mark.parametrize('broken', ['missing', 'backwards'])
def test_missing_time_mapping_uses_order_only(tmp_path, broken):
    events = fixture_events()
    if broken == 'missing':
        for e in events:e.pop('elapsed_s')
    else:events[4]['elapsed_s'] = .1
    s = make(tmp_path, events)
    assert page(s.index, 'run-123', 3)['meta']['mode'] == 'order'
    assert page(s.index, 'run-123', 3)['items'][0]['status'] == 'running'


def test_paging_and_future_row_rejection(service):
    a = page(service.index, 'run-123', 7, limit=2)
    assert len(a['items']) == 2 and a['total'] > 2
    assert page(service.index, 'run-123', 7, offset=100)['items'] == []
    with pytest.raises(ValueError, match='not_available'):
        row_at(service.index, 'run-123', 1, 'e:6')


def test_selection_pins_exact_record_version_and_source(service):
    pin = service.pin({'run': 'run-123', 'cursor': 9, 'key': 'r:8:0'})
    page(service.index, 'run-123', 7)
    assert pin['target']['event_cursor'] == 9 and pin['target']['record_version'] == '1'
    assert pin['target']['source_refs'][0]['media_range']['start_s'] == .4
    assert pin['context']['review_playhead_media_s'] is None


def test_row_time_is_arrival_not_source(service):
    r = row_at(service.index, 'run-123', 9, 'e:6')
    assert r['result_record_s'] == 3
    target = service.target('run-123', 9, 'r:8:0')
    assert target['source_refs'][0]['media_range']['start_s'] == .4
