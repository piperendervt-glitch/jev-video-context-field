"""Opt-in CPU/model integration, kept separate from deterministic unit suite."""
import pytest
pytestmark = pytest.mark.local

def test_audio_measure_silence_and_synthetic_sine():
    import numpy as np
    from context_fields.observers import AudioMeasure
    a=AudioMeasure();silent=a.measure(np.zeros(3200),16000)
    assert silent["pitch_hz"] is None and silent["dbfs"] is None
    signal=.2*np.sin(2*np.pi*200*np.arange(3200)/16000)
    result=a.measure(signal,16000)
    assert result["rms"]==pytest.approx(.2/2**.5)
    assert result["pitch_hz"]==pytest.approx(200,abs=4)

def test_installed_cpu_observers_and_demo_decoder():
    import cv2
    import numpy as np
    from pathlib import Path
    from context_fields.observers import MotionCut,FaceTrack,ObserverPool
    cap=cv2.VideoCapture(str(Path(__file__).parents[1]/"fixtures"/"synthetic.webm"))
    ok,frame=cap.read();cap.release();assert ok and frame.shape==(360,640,3)
    motion=MotionCut();faces=FaceTrack();pool=ObserverPool()
    try:
        first=pool.submit("MotionCut",motion.measure,frame,0,1)
        face=pool.submit("FaceTrack",faces.measure,frame,"shot01")
        assert first.result()["flow_mean_px"] is None
        assert face.result()["identity"] is None
        black=np.zeros((180,320,3),dtype=np.uint8);white=black+255
        motion.reset();motion.measure(black,0,1)
        cut=motion.measure(white,.1,1)
        assert cut["cut"] and cut["flow_mean_px"] is None
        assert motion.measure(white,1,1)["discontinuity"]
        assert {t["agent"] for t in pool.timings}=={"FaceTrack","MotionCut"}
    finally:pool.close()
