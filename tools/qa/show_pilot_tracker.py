"""Loopback-only point tracker for the fixed Show pilot source (not a general file API)."""
import argparse, json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import cv2
import numpy as np


def track(source, x, y, start, end):
    cap = cv2.VideoCapture(str(source))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    start_frame = round(start * fps)
    end_frame = min(round(end * fps), start_frame + round(8 * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    ok, frame = cap.read()
    if not ok:
        cap.release()
        raise ValueError('Unable to read the marked frame')
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    point = np.array([[[max(0, min(width-1, x)), max(0, min(height-1, y))]]], np.float32)
    points = [[start_frame, float(point[0, 0, 0]), float(point[0, 0, 1])]]
    reason = 'complete'
    for index in range(start_frame + 1, end_frame + 1):
        ok, frame = cap.read()
        if not ok:
            reason = 'end of source'
            break
        current = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        forward, status, error = cv2.calcOpticalFlowPyrLK(gray, current, point, None, winSize=(31,31), maxLevel=4)
        if forward is None or not status[0,0]:
            reason = 'tracking lost'
            break
        backward, valid, _ = cv2.calcOpticalFlowPyrLK(current, gray, forward, None, winSize=(31,31), maxLevel=4)
        if backward is None or not valid[0,0] or np.linalg.norm(backward-point)>2.5 or float(error[0,0])>35:
            reason = 'low confidence; correct the next frame'
            break
        xx, yy = forward[0,0]
        if not (0 <= xx < width and 0 <= yy < height):
            reason = 'point left the frame'
            break
        points.append([index, float(xx), float(yy)])
        gray, point = current, forward
    cap.release()
    return {'points': points, 'reason': reason, 'fps': fps, 'tracked_until': points[-1][0]/fps}


def serve(source, port):
    slots = threading.BoundedSemaphore(2)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def cors(self):
            self.send_header('Access-Control-Allow-Origin', 'http://127.0.0.1:8792')
            self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        def do_OPTIONS(self):
            self.send_response(204); self.cors(); self.end_headers()
        def do_POST(self):
            code = 200
            try:
                if self.headers.get('Origin') not in (None, 'http://127.0.0.1:8792'):
                    raise ValueError('Origin not allowed')
                if self.path != '/track-finger':
                    raise ValueError('Unknown operation')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 8192:
                    raise ValueError('Invalid request size')
                data = json.loads(self.rfile.read(length))
                vals = [float(data[k]) for k in ['x','y','start','end']]
                if not all(np.isfinite(vals)):
                    raise ValueError('Invalid coordinates')
                x,y,start,end = vals
                if not (0 <= start < end <= 44 and 0 <= x < 1280 and 0 <= y < 720):
                    raise ValueError('Point or interval outside this pilot')
                if not slots.acquire(blocking=False):
                    code = 429; result = {'error':'Another tracking job is running'}
                else:
                    try: result = track(source,x,y,start,end)
                    finally: slots.release()
            except Exception as exc:
                code = 400; result = {'error':str(exc)}
            encoded = json.dumps(result).encode()
            self.send_response(code); self.cors(); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(encoded))); self.end_headers()
            try: self.wfile.write(encoded)
            except BrokenPipeError: pass
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('--port',type=int,default=8793)
    args=parser.parse_args();serve(args.source.resolve(),args.port)
