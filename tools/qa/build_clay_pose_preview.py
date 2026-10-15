"""Assemble a private, timed clay pose study, not a video-generation service.

Plan JSON supplies duration, contiguous shots and per-shot sheet/pose indices.
Sheets are borderless 3 x 2 grids. Relative sheet paths resolve beside the plan.
Each source-timed cue happens once and holds; the film stays at 25 fps. No interpolation.
"""
from __future__ import annotations
import argparse
import json
import math
import subprocess
import tempfile
from pathlib import Path


def render(plan: Path, voice: Path, output: Path, ffmpeg: str, ffprobe: str) -> None:
    document = json.loads(plan.read_text())
    duration = float(document['duration'])
    shots = document['shots']
    previous = 0.0
    for shot in shots:
        start, end = float(shot['start']), float(shot['end'])
        if not all(map(math.isfinite, [start, end, duration])) or abs(start-previous) > .001 or end <= start:
            raise ValueError('Shots must be finite, positive and contiguous from zero')
        if not shot['poses'] or any(type(p) is not int or not 0 <= p < 6 for p in shot['poses']):
            raise ValueError('Each shot needs grid pose indices from zero to five')
        keys=shot.get('poseKeys',[])
        if any(not math.isfinite(k['time']) or type(k['pose']) is not int or not 0<=k['pose']<6 for k in keys) or any(a['time']>b['time'] for a,b in zip(keys,keys[1:])):
            raise ValueError('Pose cues must be finite, ordered and use indices zero to five')
        previous = end
    if not shots or abs(previous-duration) > .001:
        raise ValueError('Shot duration must match the plan')
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.stem+'.pending'+output.suffix)
    try:
        with tempfile.TemporaryDirectory(prefix='clay-pose-', dir=output.parent) as temporary:
            folder = Path(temporary)
            for i, shot in enumerate(shots):
                sheet = plan.parent / shot['sheet']
                info = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'json', str(sheet)]))['streams'][0]
                width, height = info['width'], info['height']
                tile_w, tile_h = width//3//2*2-4, height//2//2*2-4
                if tile_w < 64 or tile_h < 64:
                    raise ValueError('The sheet is too small')
                keys = shot.get('poseKeys') or [{'time':shot['start'],'pose':shot['poses'][0]}]
                def expression(axis: str) -> str:
                    def coordinate(p):
                        return (p%3)*(width//3)+2 if axis == 'x' else (p//3)*(height//2)+2
                    expr = str(coordinate(keys[0]['pose']))
                    for key in keys[1:]:
                        expr = f"if(gte(t+{shot['start']},{key['time']}),{coordinate(key['pose'])},{expr})"
                    return expr
                filters = f"crop={tile_w}:{tile_h}:x='{expression('x')}':y='{expression('y')}',scale=1280:720"
                if shot.get('close'):
                    # Match the tool's 4% x / 10% y, 80% crop exactly.
                    filters += ',crop=1024:576:51:72,scale=1280:720'
                filters += ',setsar=1'
                count = round(shot['end']*25)-round(shot['start']*25)
                subprocess.run([ffmpeg, '-hide_banner', '-v', 'error', '-y', '-filter_threads', '1', '-loop', '1', '-framerate', '25', '-i', str(sheet), '-vf', filters, '-frames:v', str(count), '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', str(folder/f'{i}.mp4')], check=True)
            listing = folder/'segments.txt'
            listing.write_text(''.join(f"file '{i}.mp4'\n" for i in range(len(shots))))
            subprocess.run([ffmpeg, '-hide_banner', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), '-i', str(voice), '-map', '0:v:0', '-map', '1:a:0', '-t', str(duration), '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', str(pending)], check=True)
            subprocess.run([ffmpeg, '-v', 'error', '-i', str(pending), '-f', 'null', '-'], check=True)
            result = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,nb_frames,duration', '-of', 'json', str(pending)]))
            video = next(s for s in result['streams'] if s['codec_type'] == 'video')
            audio = next(s for s in result['streams'] if s['codec_type'] == 'audio')
            if abs(float(result['format']['duration'])-duration) > .04 or int(video['nb_frames']) != round(duration*25) or abs(float(audio['duration'])-duration) > .04:
                raise ValueError('Unexpected preview duration, audio or frame count')
            pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('voice', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--ffprobe', default='ffprobe')
    args = parser.parse_args()
    render(args.plan, args.voice, args.output, args.ffmpeg, args.ffprobe)
