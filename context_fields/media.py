"""Opaque local assets and PTS-based, gated decoding. No semantic pre-analysis."""
import hashlib
import math
import secrets
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PureWindowsPath
from threading import RLock

MAX_UPLOAD = 2 * 1024**3
SUPPORTED = {".mp4", ".webm", ".mov", ".m4v", ".mkv"}

def probe(path):
    import av
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("video_stream_missing")
        video = container.streams.video[0]
        origin = float(Fraction(container.start_time or 0, av.time_base))
        duration = float(Fraction(container.duration, av.time_base)) if container.duration else None
        if duration is None or not math.isfinite(duration) or duration <= 0:
            raise ValueError("duration_unavailable")
        stream_info = [{"index": s.index, "type": s.type, "codec": s.codec_context.name,
                        "start_pts": s.start_time, "time_base": [s.time_base.numerator,s.time_base.denominator]}
                       for s in container.streams if s.type in {"video","audio"}]
        first = next(container.decode(video), None)
        if first is None or first.pts is None:
            raise ValueError("video_pts_or_decode_unavailable")
        if first.width*first.height > 7680*4320:
            raise ValueError("video_dimensions_limit")
    audio_ok = None
    with av.open(str(path)) as container:
        if container.streams.audio:
            first_audio=next(container.decode(container.streams.audio[0]),None)
            if first_audio is None or first_audio.pts is None:
                raise ValueError("audio_pts_or_decode_unavailable")
            audio_ok=True
    return {"duration_s": duration, "origin_s": origin, "has_audio": audio_ok is True,
            "width": first.width, "height": first.height, "streams": stream_info,
            "backend_decode": "ready", "browser_decode": "pending",
            "time_mapping": "media_s = PTS * time_base - container_start_time; audio sample offsets added explicitly"}

@dataclass(frozen=True)
class Asset:
    id: str
    path: Path
    name: str
    size: int
    sha256: str
    metadata: dict

    def public(self):
        return {"id": self.id, "name": self.name, "bytes": self.size, "sha256": self.sha256, **self.metadata}

class AssetStore:
    def __init__(self, root):
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.assets={}
        self.lock=RLock()
        # Diagnosis/replay can still refer to old imports. Startup must not
        # silently erase them; explicit Viewer deletion remains available.

    def import_stream(self, stream, size, filename):
        if type(size) is not int or not 0 < size <= MAX_UPLOAD:
            raise ValueError("media_size_limit_2GiB")
        filename=PureWindowsPath(filename).name
        suffix=Path(filename).suffix.lower()
        if suffix not in SUPPORTED:
            raise ValueError("unsupported_container_extension")
        aid="asset-"+secrets.token_hex(16)
        path=self.root/(aid+suffix)
        digest=hashlib.sha256()
        try:
            with path.open("xb") as f:
                remaining=size
                while remaining:
                    chunk=stream.read(min(1024*1024,remaining))
                    if not chunk:
                        raise ValueError("incomplete_media_upload")
                    f.write(chunk);digest.update(chunk);remaining-=len(chunk)
            metadata=probe(path)
            asset=Asset(aid,path,filename[:240],size,digest.hexdigest(),metadata)
            with self.lock:self.assets[aid]=asset
            return asset
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def get(self, aid):
        with self.lock:
            if aid not in self.assets:raise ValueError("asset_not_registered")
            return self.assets[aid]

    def delete(self, aid):
        with self.lock:
            asset=self.get(aid)
            if asset.path.resolve().parent != self.root:raise ValueError("asset_path_boundary")
            asset.path.unlink(missing_ok=True)
            del self.assets[aid]

class PTSDecoder:
    def __init__(self, asset):
        self.asset=asset

    def frame(self, start, cutoff):
        import av
        origin=self.asset.metadata["origin_s"]
        with av.open(str(self.asset.path)) as container:
            stream=container.streams.video[0]
            container.seek(int((cutoff+origin)/float(stream.time_base)),stream=stream,backward=True)
            chosen=None
            for frame in container.decode(stream):
                if frame.pts is None:raise ValueError("frame_pts_missing")
                stamp=float(frame.pts*frame.time_base)-origin
                if stamp > cutoff+1e-9:break
                if stamp >= start-1e-9:chosen=(frame,stamp)
            if chosen is None:return None
            frame,stamp=chosen
            # Bound model/CPU input without losing original source dimensions or PTS.
            width=min(640,frame.width);height=max(1,round(frame.height*width/frame.width))
            image=frame.reformat(width=width,height=height).to_ndarray(format="bgr24");image.setflags(write=False)
            return {"image":image,
                    "media_s":stamp,"pts":frame.pts,"time_base":[frame.time_base.numerator,frame.time_base.denominator],
                    "media_origin_s":origin,"stream":str(stream.index),"source_dimensions":[frame.width,frame.height],
                    "frame_id":f"{self.asset.id}:v{stream.index}:pts{frame.pts}"}

    def audio(self, start, cutoff):
        import av
        import numpy as np
        if not self.asset.metadata["has_audio"] or cutoff <= start:return None
        if cutoff-start > 6.000001:raise ValueError("asr_window_limit")
        origin=self.asset.metadata["origin_s"]
        chunks=[]
        with av.open(str(self.asset.path)) as container:
            stream=container.streams.audio[0]
            container.seek(int((start+origin)/float(stream.time_base)),stream=stream,backward=True)
            resampler=av.AudioResampler(format="fltp",layout="mono",rate=16000)
            for frame in container.decode(stream):
                if frame.pts is None:raise ValueError("audio_pts_missing")
                raw_start=float(frame.pts*frame.time_base)-origin
                if raw_start > cutoff:break
                for out in resampler.resample(frame):
                    if out.pts is None:raise ValueError("resample_pts_missing")
                    stamp=float(out.pts*out.time_base)-origin
                    lo=max(0,math.ceil((start-stamp)*16000-1e-7))
                    hi=min(out.samples,math.floor((cutoff-stamp)*16000+1e-7))
                    if hi<=lo:continue
                    samples=out.to_ndarray().reshape(-1)[lo:hi].copy()
                    samples.setflags(write=False)
                    chunks.append({"samples":samples,"start_s":stamp+lo/16000,"end_s":stamp+(hi-1)/16000,
                                   "pts":out.pts,"time_base":[out.time_base.numerator,out.time_base.denominator],
                                   "sample_offset":lo,"sample_rate":16000,"media_origin_s":origin,
                                   "container_pts":frame.pts,"container_time_base":[frame.time_base.numerator,frame.time_base.denominator],
                                   "stream":str(stream.index)})
        if not chunks:return None
        for a,b in zip(chunks,chunks[1:]):
            if abs(b["start_s"]-a["end_s"]-1/16000)>2/16000:
                raise ValueError("audio_discontinuity")
        samples=np.concatenate([c["samples"] for c in chunks]);samples.setflags(write=False)
        return {"samples":samples,"sample_rate":16000,"start_s":chunks[0]["start_s"],"end_s":chunks[-1]["end_s"],"chunks":chunks}
