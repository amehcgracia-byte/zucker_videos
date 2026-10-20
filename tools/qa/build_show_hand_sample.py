"""Offline automatic index-tip detection. Private media/results stay outside Git.

Run with mediapipe==0.10.21, OpenCV and NumPy in an isolated environment.
Coordinates are source pixels; no manual anchor, fitted spiral or gap interpolation.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time


def detect(source, output):
    import cv2
    import mediapipe as mp
    cap = cv2.VideoCapture(str(source))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width, height = int(cap.get(3)), int(cap.get(4))
    frames, runs, current = [], [], []
    begin = time.monotonic()
    # CPU only; no transmission and no change to the running editor environment.
    with mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=2,
            model_complexity=1, min_detection_confidence=.6,
            min_tracking_confidence=.6) as detector:
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            if index % round(fps) == 0 and index:
                detector.reset()  # Redetect palms regularly; avoid tracking a cup as a hand.
            result = detector.process(rgb)
            candidates = []
            for hand in result.multi_hand_landmarks or []:
                p = hand.landmark
                tip = p[8]
                # Extended index, rather than cups, closed fists, or other fingers.
                d = lambda a,b: math.hypot(p[a].x-p[b].x,p[a].y-p[b].y)
                if d(8,0) < d(6,0)*1.08 or d(8,5) < d(6,5)*1.25:
                    continue
                if not (0 <= tip.x <= 1 and 0 <= tip.y <= 1):
                    continue
                candidates.append([index, round(tip.x*width,2), round(tip.y*height,2)])
            point = None
            if candidates:
                # Prefer continuity, never connect across a lost detection.
                point = min(candidates, key=lambda p: math.hypot(p[1]-current[-1][1],p[2]-current[-1][2])) if current else candidates[0]
                if current and math.hypot(point[1]-current[-1][1],point[2]-current[-1][2]) > width*.16:
                    if len(current)>=3: runs.append(current)
                    current=[]
                current.append(point)
                frames.append(point)
            else:
                if len(current)>=3: runs.append(current)
                current=[]
            index += 1
            if index % 250 == 0:
                print(f'{index} frames; {len(frames)} extended-index detections', flush=True)
        if len(current)>=3: runs.append(current)
    cap.release()
    data = dict(schema=1, engine='MediaPipe Hands 0.10.21 / palm reset each second / CPU',
        source_sha256=hashlib.file_digest(open(source,'rb'),'sha256').hexdigest(),
        fps=fps,width=width,height=height,frames=index,detected_frames=len(frames),
        wall_sec=round(time.monotonic()-begin,3),runs=runs,
        limitations='Extended index heuristic; lost detections split the stroke. No manual anchors or invented geometry.')
    output.write_text(json.dumps(data))
    print(json.dumps({k:v for k,v in data.items() if k!='runs'}), flush=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();detect(a.source,a.output)
