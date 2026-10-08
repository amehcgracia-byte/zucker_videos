"""Four-worker decoder/conversion ablation; differences are not exclusive timings."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.ffmpeg import ffprobe, tool_status

parser = argparse.ArgumentParser()
parser.add_argument('--plan', required=True)
parser.add_argument('--root', required=True)
args = parser.parse_args()
root = Path(args.root); root.mkdir(parents=True, exist_ok=False)
segments = [s for s in json.loads(Path(args.plan).read_text())['segments'] if s.get('spherical_shot')]
selected = [segments[round(i * (len(segments) - 1) / 7)] for i in range(8)]
for source in {s['source_path'] for s in selected}:
    video = next(s for s in ffprobe(source)['streams'] if s.get('codec_type') == 'video')
    assert (video.get('codec_name'), video.get('pix_fmt'), video.get('color_range'), video.get('color_space')) == ('hevc', 'yuvj420p', 'pc', 'bt709'), 'Replay only validated full-range source'
ffmpeg = tool_status()['ffmpeg_path']
rows = []
for order, bgr in enumerate([False, True, True, False]):
    started = time.perf_counter()
    def run(segment):
        start = time.perf_counter()
        vf = 'format=yuvj420p,fps=30' + (',format=bgr24' if bgr else '')
        command = [ffmpeg, '-hide_banner', '-nostdin', '-benchmark', '-threads', '2',
                   '-ss', str(segment['clip_start_sec']), '-hwaccel', 'videotoolbox', '-i', segment['source_path'],
                   '-vf', vf, '-an', '-frames:v', str(round(segment['duration_sec'] * 30)), '-f', 'null', '-']
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=180)
        return dict(wall_sec=time.perf_counter()-start, command=command,
                    benchmark=[line for line in result.stderr.splitlines() if line.startswith('bench:')])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, selected))
    rows.append(dict(order=order, bgr_conversion=bgr, wall_sec=time.perf_counter()-started, segments=results))
    (root / 'results.json').write_text(json.dumps(rows, indent=2)); print(order, bgr, rows[-1]['wall_sec'], flush=True)
