"""Bounded derivative cache of saved source frames. Decode only, no observations."""
import base64
import hashlib
import io
import json
import threading
from pathlib import Path

TRANSFORM = 'h11-source-frame-roi-128-v1'


class ThumbnailCache:
    def __init__(self, review, max_files=128, max_bytes=16 * 1024 * 1024):
        self.review = review
        self.root = review.root / 'artifacts/human-review-thumbnails'
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_files, self.max_bytes = max_files, max_bytes
        self.lock, self.slots = threading.Lock(), threading.BoundedSemaphore(2)

    @staticmethod
    def identity(asset_sha, ref):
        return {'asset_sha256': asset_sha, 'stream': ref.get('stream'), 'frame_id': ref.get('frame_id'),
                'pts': ref.get('pts'), 'time_base': ref.get('time_base'), 'interval': ref.get('interval'),
                'origin': ref.get('media_origin_s'), 'crop': ref.get('roi'), 'rotation': ref.get('rotation'),
                'size': [128, 88], 'transform': TRANSFORM}

    def get(self, run, cursor, row_id, revision, ref_id, asset_id, generation):
        from .activity import row_at
        from .debug_review import frame_at
        if self.review.match(run, asset_id)['media_match'] != 'hash_verified':
            raise ValueError('verify_media_before_thumbnail')
        row = row_at(self.review.index, run, cursor, row_id)
        if row['revision'] != int(revision):
            raise ValueError('thumbnail_row_revision_mismatch')
        ref = next((r for r in row['source_refs'] if r['ref_id'] == ref_id and r['modality'] == 'video'), None)
        if not ref:
            raise ValueError('thumbnail_source_not_in_row')
        binding = dict(run_id=run, epoch=row['epoch'], row_id=row_id, revision=row['revision'],
                       generation=generation, ref_id=ref_id)
        if ref.get('pts') is None or ref.get('time_base') is None or ref.get('stream') is None:
            return {**binding, 'state': 'missing', 'reason': 'frame_identity_incomplete'}
        if not self.slots.acquire(blocking=False):
            return {**binding, 'state': 'pending', 'reason': 'thumbnail_queue_busy'}
        try:
            asset = self.review.assets.get(asset_id)
            identity = self.identity(asset.sha256, ref)
            cache_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            path = self.root / (cache_key + '.json')
            with self.lock:
                if path.is_file():
                    cached = json.loads(path.read_text(encoding='utf8'))
                    return {**cached, **binding, 'cached': True}
            if ref.get('media_s') is None:
                return {**binding, 'state': 'missing', 'reason': 'source_time_missing'}
            result = frame_at(asset, ref['media_s'], stream_index=int(ref['stream']))
            if not result.get('jpeg'):
                return {**binding, 'state': 'missing', 'reason': 'source_frame_missing'}
            tb = ref['time_base']
            from fractions import Fraction
            expected_tb = Fraction(*tb) if isinstance(tb, list) else Fraction(tb)
            if result['pts'] != ref['pts'] or Fraction(result['time_base']) != expected_tb:
                return {**binding, 'state': 'missing', 'reason': 'decoded_pts_mismatch'}
            from PIL import Image
            frame = Image.open(io.BytesIO(base64.b64decode(result['jpeg'])))
            roi = ref.get('roi')
            if roi:
                w = min(640, result['source_dimensions'][0])
                h = round(result['source_dimensions'][1] * w / result['source_dimensions'][0])
                x, y, rw, rh = roi
                if not (0 <= x < x + rw <= w and 0 <= y < y + rh <= h):
                    return {**binding, 'state': 'missing', 'reason': 'invalid_saved_roi'}
                frame = frame.crop((round(x * frame.width / w), round(y * frame.height / h),
                                    round((x + rw) * frame.width / w), round((y + rh) * frame.height / h)))
            frame.thumbnail((128, 88))
            buf = io.BytesIO(); frame.save(buf, format='JPEG', quality=80)
            cached = {'state': 'ready', 'jpeg': base64.b64encode(buf.getvalue()).decode(), 'cache_key': cache_key,
                      'identity': identity, 'decoded_pts': result['pts'], 'media_s': result['media_s'],
                      'provenance': 'reconstructed_source_not_original_model_pixels'}
            data = json.dumps(cached, ensure_ascii=False).encode('utf8')
            with self.lock:
                existing = sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime_ns)
                total = sum(p.stat().st_size for p in existing)
                while existing and (len(existing) >= self.max_files or total + len(data) > self.max_bytes):
                    victim = existing.pop(0); total -= victim.stat().st_size
                    if victim.resolve().parent != self.root.resolve():
                        raise ValueError('thumbnail_cache_scope')
                    victim.unlink()  # only a verified derivative file inside this cache
                path.write_bytes(data)
            return {**cached, **binding, 'cached': False}
        finally:
            self.slots.release()
