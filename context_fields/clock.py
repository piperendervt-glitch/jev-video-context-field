"""All release decisions use player media time, never elapsed wall time."""
import math
from dataclasses import dataclass, field
from fractions import Fraction

def finite(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError("nonfinite_number")
    return float(x)

def pts_seconds(pts, time_base):
    if type(pts) is not int or len(time_base) != 2 or time_base[1] <= 0:
        raise ValueError("invalid_pts")
    return float(pts * Fraction(*time_base))

@dataclass
class MediaClock:
    epoch: int = 1
    media_s: float = 0.0
    epoch_start_media_s: float = 0.0
    seq: int = -1
    state: str = "paused"
    released: dict = field(default_factory=lambda: {"audio": [], "video": []})
    last_wall: float | None = None
    duration_s: float = 120.0
    strict_continuity: bool = False

    def seek(self, target):
        target = finite(target)
        if not 0 <= target <= self.duration_s:
            raise ValueError("invalid_seek")
        self.epoch += 1
        self.media_s = self.epoch_start_media_s = target
        self.seq = -1
        self.released = {"audio": [], "video": []}
        self.state = "paused"
        self.last_wall = None
        return self.epoch

    def notify(self, epoch, seq, media_s, video_presented_s, state, wall_s,
               audio_presented_s=None, rate=1):
        if epoch != self.epoch or type(seq) is not int or seq <= self.seq:
            return False
        media_s, video_presented_s, wall_s = map(finite, (media_s, video_presented_s, wall_s))
        if rate != 1 or state not in {"playing", "paused", "ended", "stopped"}:
            raise ValueError("unsupported_playback")
        if media_s < self.media_s or media_s > self.duration_s or video_presented_s > media_s + 0.1:
            raise ValueError("invalid_clock")
        if self.strict_continuity:
            elapsed=max(0,wall_s-self.last_wall) if self.last_wall is not None else 0
            if media_s-self.media_s>elapsed+.35:
                raise ValueError('playback_discontinuity_requires_seek')
        # A pause notification may release the last presented interval, but
        # repeated paused notifications cannot move media time or expose samples.
        advancing = self.state == "playing" or state == "playing"
        if not advancing and media_s > self.media_s + 1e-6:
            raise ValueError("paused_clock_advanced")
        if audio_presented_s is not None:
            audio_presented_s = finite(audio_presented_s)
            if audio_presented_s > media_s + 1e-6:
                raise ValueError("future_audio")
        if advancing:
            for modality, end in (("video", min(media_s, video_presented_s)),
                                  ("audio", audio_presented_s)):
                if end is None:
                    continue
                end = finite(end)
                if end > media_s + 1e-6:
                    raise ValueError("future_audio")
                if end >= self.epoch_start_media_s:
                    previous = self.released[modality]
                    old_end = previous[-1][1] if previous else self.epoch_start_media_s
                    self.released[modality] = [(self.epoch_start_media_s, max(old_end, end))]
        self.media_s, self.seq, self.state, self.last_wall = media_s, seq, state, wall_s
        return True

    def permits(self, start, end, modality, epoch):
        start, end = finite(start), finite(end)
        return epoch == self.epoch and start <= end and any(
            a <= start and end <= b for a, b in self.released.get(modality, []))

    def window(self, seconds, modality):
        intervals = self.released.get(modality, [])
        if not intervals or intervals[-1][1] <= intervals[-1][0]:
            return None
        a, b = intervals[-1]
        return (max(a, b - seconds), b)

    def synchronized(self, wall_s):
        return self.last_wall is not None and wall_s - self.last_wall <= 1.0

def root_key(media_id, epoch, modality, stream, media_s):
    return f"{media_id}:{epoch}:{modality}:{stream}:{math.floor(finite(media_s) / 2)}"
