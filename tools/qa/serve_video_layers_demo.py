"""Local, isolated moving-person demo. Never changes a project or the installed app.

Run with the repository Python, --source HOST.mp4 --background BACKGROUND.mp4.
Private media and cached masks stay in --cache (outside the repository by default).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from flask import Flask, send_file, abort

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from core import show_backgrounds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--background', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=Path('/tmp/zucker-video-layers-demo'))
    parser.add_argument('--port', type=int, default=8810)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    folder = args.cache
    folder.mkdir(parents=True, exist_ok=True)
    # Key the preparation by the precise source and framing; never reuse a different take.
    plan = {'source': str(args.source.resolve()), 'fps': 25, 'framing': 'whole',
            'shots': [{'start_frame': 0, 'end_frame': 200, 'zoom': 1, 'focus': [.5, .5]}]}
    key = show_backgrounds._frames_key(plan, 0, 200)
    host = folder / f'host-{key}.mp4'
    if not host.exists():
        pending = host.with_name(host.stem + '-pending.mp4')
        subprocess.run([args.ffmpeg, '-v', 'error', '-nostdin', '-y', '-i', str(args.source),
                        '-t', '8', '-vf', 'scale=960:540,fps=25', '-c:v', 'libx264', '-preset', 'veryfast',
                        '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(pending)], check=True)
        pending.replace(host)
    entries = [{'background': 'demo', 'at': 0, 'duration': 8}]
    notes = show_backgrounds.prepare(host, entries, plan, folder / 'masks', ffmpeg=args.ffmpeg,
                                    progress=lambda p, s: print(s, flush=True))
    if notes:
        raise RuntimeError('; '.join(notes))
    masks = np.load(entries[0]['masks'])
    atlas = folder / f'alpha-{key}.png'
    if not atlas.exists():
        w, h = show_backgrounds.MASK_SIZE
        pixels = np.zeros((((len(masks) + 9) // 10) * h, 10 * w, 4), np.uint8)
        pixels[..., :3] = 255
        for i, mask in enumerate(masks):
            pixels[(i // 10) * h:(i // 10 + 1) * h, (i % 10) * w:(i % 10 + 1) * w, 3] = mask
        Image.fromarray(pixels).save(atlas)
    assets = {'host.mp4': host, 'background.mp4': args.background.resolve(), 'alpha.png': atlas}
    backgrounds = ROOT / 'tools/qa/show-backgrounds'
    for item in json.loads((backgrounds / 'manifest.json').read_text()):
        assets[item['file']] = backgrounds / item['file']
    app = Flask(__name__)

    @app.get('/')
    def index():
        return send_file(ROOT / 'tools/qa/video-layers/index.html')

    @app.get('/media/<name>')
    def media(name):
        path = assets.get(name)
        if path is None:
            abort(404)
        return send_file(path, conditional=True)

    @app.get('/sample.json')
    def sample():
        return {'fps': 25, 'frames': len(masks), 'maskWidth': 384, 'maskHeight': 216, 'columns': 10,
                'backgrounds': json.loads((backgrounds / 'manifest.json').read_text())}

    print(f'Demo ready: http://127.0.0.1:{args.port}/', flush=True)
    app.run(host='127.0.0.1', port=args.port, threaded=True)


if __name__ == '__main__':
    main()
