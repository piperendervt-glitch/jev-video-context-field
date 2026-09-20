"""Generate an explicitly synthetic 32-second VP8/WebM, no downloaded assets."""
from pathlib import Path
import cv2
import numpy as np

def main():
    path = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic.webm"
    path.parent.mkdir(exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"VP80"), 10, (640,360))
    if not writer.isOpened():
        raise RuntimeError("VP8 encoder unavailable; supply a browser-playable local video")
    for i in range(320):
        t = i/10
        night = t >= 12
        image = np.full((360,640,3), (38,29,22) if night else (203,225,239), dtype=np.uint8)
        cv2.rectangle(image, (35,65), (275,230), (67,51,36) if night else (231,191,127), -1)
        cv2.line(image, (155,65), (155,230), (60,70,80), 5)
        cv2.line(image, (35,145), (275,145), (60,70,80), 5)
        cv2.circle(image, (95,110), 21, (200,214,232) if night else (112,217,253), -1)
        cv2.rectangle(image, (0,275), (640,360), (53,71,86), -1)
        x = 410+int(12*np.sin(t))
        cv2.circle(image, (x,146), 37, (165,177,187), -1)
        cv2.ellipse(image, (x,255), (65,66), 0, 180, 360, (115,144,143), -1)
        cv2.circle(image, (x-12,140), 3, (37,40,44), -1)
        cv2.circle(image, (x+12,140), 3, (37,40,44), -1)
        cv2.ellipse(image, (x,153), (15,9), 0, 0 if not night else 180, 180 if not night else 360, (37,40,44), 2)
        cv2.putText(image, "SYNTHETIC / OFFLINE FIXTURE", (24,32), cv2.FONT_HERSHEY_SIMPLEX, .6, (248,248,248) if night else (40,53,63), 1, cv2.LINE_AA)
        title = "QUIET / NO NEW EVIDENCE" if t >= 20 else "SCENE B / NIGHT" if night else "SCENE A / DAY"
        cv2.putText(image, title, (24,319), cv2.FONT_HERSHEY_SIMPLEX, .6, (239,235,224), 1, cv2.LINE_AA)
        cv2.putText(image, f"{t:04.1f}s", (540,337), cv2.FONT_HERSHEY_SIMPLEX, .6, (239,235,224), 1, cv2.LINE_AA)
        writer.write(image)
    writer.release()
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame.shape != (360,640,3):
        raise RuntimeError("demo_decode_failed")
    print(f"Created {path} ({path.stat().st_size} bytes), VP8, 32 s, 10 fps, no audio")

if __name__ == "__main__":
    main()
