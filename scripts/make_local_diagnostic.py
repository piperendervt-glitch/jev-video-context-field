"""Mux locally generated speech and two synthetic moving colors. Not user footage."""
from pathlib import Path
from fractions import Fraction
import av
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
def main():
    wav=ROOT/'fixtures/local-diagnostic-speech.wav'
    with av.open(str(wav)) as source:
        resampler=av.AudioResampler(format='fltp',layout='mono',rate=16000)
        chunks=[out.to_ndarray().reshape(-1) for frame in source.decode(audio=0) for out in resampler.resample(frame)]
    speech=np.concatenate(chunks)
    duration=40
    pcm=np.zeros(duration*16000,dtype=np.float32)
    for start in range(0,duration,8):pcm[start*16000:start*16000+len(speech)]=speech
    path=ROOT/'fixtures/local-diagnostic.mp4'
    with av.open(str(path),'w') as output:
        video=output.add_stream('libx264',rate=10);video.width=320;video.height=180;video.pix_fmt='yuv420p';video.options={'preset':'ultrafast','crf':'25'}
        audio=output.add_stream('aac',rate=16000);audio.layout='mono'
        for i in range(duration*10):
            image=np.zeros((180,320,3),dtype=np.uint8);image[:]=[70,130,180] if i<80 else [150,60,50]
            image[60:120,(i*2)%240:(i*2)%240+40]=[235,220,110]
            vf=av.VideoFrame.from_ndarray(image,format='rgb24');vf.pts=i;vf.time_base=Fraction(1,10)
            for packet in video.encode(vf):output.mux(packet)
            samples=pcm[i*1600:(i+1)*1600].reshape(1,-1)
            af=av.AudioFrame.from_ndarray(samples,format='fltp',layout='mono');af.sample_rate=16000;af.pts=i*1600;af.time_base=Fraction(1,16000)
            for packet in audio.encode(af):output.mux(packet)
        for stream in (video,audio):
            for packet in stream.encode():output.mux(packet)
    print(path,path.stat().st_size)

if __name__=='__main__':main()
