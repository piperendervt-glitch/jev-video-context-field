"""Local CPU observers and explicit disabled model adapters.

Browser frame timestamps are recorded as derived microsecond PTS, never claimed
to be original container PTS. Semantic LOCAL inference is disabled until an
exact-PTS decoder and explicitly selected models are available.
"""
import math
import time
import threading
from concurrent.futures import ThreadPoolExecutor

REGISTRY = {
    "AudioMeasure": {"enabled": True, "method": "numpy-rms-pitch-v1", "period_s": .2},
    "SpeechASR": {"enabled": False, "reason": "faster_whisper_and_model_unavailable", "period_s": 1, "window_s": 6},
    "FrameInterpreter": {"enabled": False, "reason": "local_vlm_not_configured", "period_s": 2},
    "FaceTrack": {"enabled": True, "method": "opencv-haar-anonymous-iou-v1", "period_s": .2},
    "MotionCut": {"enabled": True, "method": "opencv-farneback-histogram-v1", "period_s": .1},
    "TextContext": {"enabled": False, "reason": "local_llm_not_configured", "period_s": 2},
    "apparent_age_band": {"enabled": False, "reason": "explicit_opt_in_required"},
    "model_sex_class": {"enabled": False, "reason": "explicit_opt_in_required"},
}

class ModelAdapter:
    """Extension contract: a stateless callable receives only explicit gated input.

    No model client, weight search, server history, or network is created here.
    A future authorized local integration supplies a callable and model/build ID.
    """
    name = "ModelAdapter"
    heavy = True

    def __init__(self, infer=None, model_id=None):
        self.infer, self.model_id = infer, model_id

    def observe(self, immutable_slice, clock):
        import copy
        if self.infer is None:
            return {"agent": self.name, "status": "disabled", "reason": REGISTRY[self.name]["reason"], "value": None}
        if not self.model_id:
            raise ValueError("model_manifest_required")
        if not clock.permits(*immutable_slice["interval"], immutable_slice["modality"], immutable_slice["epoch"]):
            raise ValueError("unreleased_model_input")
        # No field, dominance result or hidden history can be passed into inference.
        allowed = {k: copy.deepcopy(immutable_slice[k]) for k in
                   ("interval", "modality", "epoch", "input_roots", "dependency_versions", "data")}
        return {"agent": self.name, "model": self.model_id, "status": "observed", "value": self.infer(allowed)}

class SpeechASR(ModelAdapter):
    name = "SpeechASR"

class FrameInterpreter(ModelAdapter):
    name = "FrameInterpreter"

class TextContext(ModelAdapter):
    name = "TextContext"

class AudioMeasure:
    def measure(self, samples, sample_rate):
        import numpy as np
        data = np.asarray(samples, dtype=np.float64)
        if data.ndim != 1 or not 1 <= len(data) <= 96000 or not 8000 <= sample_rate <= 96000 or not np.all(np.isfinite(data)) or np.any(abs(data) > 8):
            raise ValueError("invalid_audio_slice")
        frame_size = max(1, int(sample_rate * .02))
        rms_frames = [float(np.sqrt(np.mean(x*x))) for i in range(0,len(data),frame_size) if len(x := data[i:i+frame_size])]
        rms = float(np.sqrt(np.mean(data*data)))
        pitch = None
        # One bounded 40 ms autocorrelation; null for silence or weak periodicity.
        if rms > .01 and len(data) >= sample_rate*.04:
            signal = data[-int(sample_rate*.04):]
            signal = signal-signal.mean()
            corr = np.correlate(signal, signal, mode="full")[len(signal)-1:]
            lo, hi = int(sample_rate/400), min(len(corr)-1, int(sample_rate/60))
            lag = lo+int(np.argmax(corr[lo:hi+1]))
            if corr[0] > 0 and corr[lag]/corr[0] > .4:
                pitch = float(sample_rate/lag)
        return {"rms": rms, "dbfs": 20*math.log10(rms) if rms > 0 else None,
                "dbfs_reason": None if rms > 0 else "digital_silence", "clip_fraction": float(np.mean(abs(data) >= .999)),
                "pitch_hz": pitch, "pitch_reason": None if pitch else "unvoiced_or_unavailable",
                "silent_frame_fraction": sum(v < .01 for v in rms_frames)/len(rms_frames), "frame_ms": 20,
                "basis": "measurement", "physical_spl": None}

class MotionCut:
    def __init__(self):
        self.previous, self.previous_s, self.epoch = None, None, None

    def reset(self):
        self.previous = self.previous_s = self.epoch = None

    def measure(self, frame, media_s, epoch):
        import cv2
        import numpy as np
        gray = cv2.cvtColor(cv2.resize(frame, (160,90)), cv2.COLOR_BGR2GRAY)
        result = {"flow_mean_px": None, "cut": False, "discontinuity": False, "basis": "measurement"}
        if self.previous is not None and self.epoch == epoch:
            gap = media_s-self.previous_s
            result["discontinuity"] = gap <= 0 or gap > .35
            if not result["discontinuity"]:
                delta = float(np.mean(np.abs(gray.astype(float)-self.previous.astype(float))))/255
                result["cut_score"] = delta
                result["cut"] = delta > .32
                if result["cut"]:
                    result.update(boundary_s=media_s, discovered_s=media_s, boundary_precision="sample_interval")
                else:
                    flow = cv2.calcOpticalFlowFarneback(self.previous, gray, None, .5, 2, 12, 2, 5, 1.1, 0)
                    result["flow_mean_px"] = float(np.mean(np.linalg.norm(flow, axis=2)))
        self.previous, self.previous_s, self.epoch = gray, media_s, epoch
        return result

class FaceTrack:
    def __init__(self):
        import cv2
        self.detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if self.detector.empty():
            raise ValueError("face_weights_unavailable")
        self.tracks, self.next_id, self.scope = {}, 1, None

    def measure(self, frame, scope):
        import cv2
        if self.scope != scope:
            self.tracks, self.next_id, self.scope = {}, 1, scope
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        boxes = self.detector.detectMultiScale(gray, 1.15, 4, minSize=(24,24))
        found, used = {}, set()
        for box in boxes:
            x,y,w,h = map(int, box)
            best, best_iou = None, .3
            for tid, (a,b,c,d) in self.tracks.items():
                overlap = max(0,min(x+w,a+c)-max(x,a))*max(0,min(y+h,b+d)-max(y,b))
                iou = overlap/(w*h+c*d-overlap)
                if tid not in used and iou > best_iou:
                    best, best_iou = tid, iou
            if best is None:
                best = f"{scope}-face{self.next_id:02d}"
                self.next_id += 1
            found[best] = [x,y,w,h]
            used.add(best)
        self.tracks = found
        return {"tracks": found, "basis": "detector", "identity": None, "voice_link": None,
                "expression": None, "expression_reason": "adapter_disabled"}

class ObserverPool:
    """CPU/I/O pool; independent per-agent jobs. Heavy lane 1 running + 8 waiting."""
    def __init__(self):
        self.cpu = ThreadPoolExecutor(max_workers=3, thread_name_prefix="cpu-observer")
        self.heavy = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-model")
        self.slots = threading.BoundedSemaphore(9)
        self.cpu_slots = threading.BoundedSemaphore(12)
        self.timings = []
        self.lock = threading.Lock()

    def submit(self, agent, fn, *args, heavy=False):
        gate = self.slots if heavy else self.cpu_slots
        if not gate.acquire(blocking=False):
            return None
        accepted = time.monotonic()
        def work():
            start = time.monotonic()
            try:
                return fn(*args)
            finally:
                with self.lock:
                    self.timings.append({"agent": agent, "accepted": accepted, "started": start, "completed": time.monotonic()})
                    self.timings[:] = self.timings[-300:]
                gate.release()
        return (self.heavy if heavy else self.cpu).submit(work)

    def close(self):
        self.cpu.shutdown(wait=True, cancel_futures=True)
        self.heavy.shutdown(wait=True, cancel_futures=True)
