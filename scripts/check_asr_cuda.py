"""Explicit installed ASR CUDA smoke, synthetic speech, no downloads."""
import ctypes
import json
import os
import time
from pathlib import Path
import torch
import numpy as np
import av
from faster_whisper import WhisperModel

root=Path(__file__).resolve().parents[1]
libs=Path(torch.__file__).parent/'lib'
handle=os.add_dll_directory(str(libs))
for name in ('cublas64_12.dll','cublasLt64_12.dll','cudnn64_9.dll'):ctypes.WinDLL(str(libs/name))
t=time.monotonic();model=WhisperModel(str(root/'models/whisper-small'),device='cuda',compute_type='int8_float16',local_files_only=True)
load=time.monotonic()-t
with av.open(str(root/'fixtures/local-diagnostic-speech.wav')) as c:
    resampler=av.AudioResampler(format='fltp',layout='mono',rate=16000)
    pcm=np.concatenate([o.to_ndarray().reshape(-1) for f in c.decode(audio=0) for o in resampler.resample(f)])[:96000]
results=[]
for run in range(3):
    t=time.monotonic();segments,info=model.transcribe(pcm,beam_size=1,word_timestamps=True,vad_filter=True,condition_on_previous_text=False)
    text=' '.join(s.text for s in segments)
    results.append({'elapsed_s':time.monotonic()-t,'text':text})
report={'material':'synthetic local speech','model':'faster-whisper-small','compute_type':'int8_float16','device':'cuda','load_s':load,'runs':results}
(root/'artifacts/asr-cuda-smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
