"""Extract a private 88-second opening preview; never changes the source/workshop.

Landmark preparation remains separate: build_show_face_sample.py --enhance and
build_show_hand_sample.py. User footage and output must remain outside Git.
"""
import argparse
import json
import subprocess
from pathlib import Path


def build(source: Path, transcript: Path, output: Path, ffmpeg: str):
    output.mkdir(parents=True, exist_ok=True)
    video, voice = output/'original.pending.mp4', output/'voice.pending.m4a'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(source),
        '-t', '88', '-map', '0:v:0', '-vf', 'scale=1280:720', '-an', '-c:v', 'libx264',
        '-preset', 'ultrafast', '-crf', '20', '-pix_fmt', 'yuv420p', '-y', str(video),
        '-t', '88', '-map', '0:a:0', '-vn', '-c:a', 'aac', '-b:a', '160k', '-y', str(voice)], check=True)
    video.replace(output/'original.mp4');voice.replace(output/'voice.m4a')
    # Alias the same speech asset, without creating a second audio encode.
    mix = output/'mix.m4a'
    if not mix.exists():
        try:mix.symlink_to('voice.m4a')
        except OSError:
            import shutil
            shutil.copyfile(output/'voice.m4a', mix)
    data=json.loads(transcript.read_text());captions=[]
    for s in data['sources'][0]['segments']:
        if s['start_sec']>=88 or s['text'].strip() in {'...', '.'}:continue
        words=[{'start':w['start_sec'],'end':min(88,w['end_sec']),'text':w['word']}
               for w in s.get('words',[]) if w['start_sec']<88]
        captions.append({'start':s['start_sec'],'end':min(88,s['end_sec']),'text':s['text'],'words':words})
    (output/'captions.json').write_text(json.dumps({'captions':captions,'origin':'Existing cached transcript; review its errors'}))
    (output/'finger-track.json').write_text('[]')
    recipe=output/'recipe.json'
    if not recipe.exists():
        events=[dict(id=f'opening-caption-{i}',type='caption',name='Caption',parent='base',
                     trackId='captions',fontSize=38,opacity=1,x=.5,y=.9,**cue) for i,cue in enumerate(captions)]
        recipe.write_text(json.dumps({'version':2,'duration':88,'events':events}))
    import cv2
    cap=cv2.VideoCapture(str(output/'original.mp4'));thumbs=output/'scene-thumbs';thumbs.mkdir(exist_ok=True)
    for i,t in enumerate([.5,31.5,42.6,57.9,68.5,80.5]):
        cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,frame=cap.read()
        if ok:cv2.imwrite(str(thumbs/f'opening-{i}.jpg'),cv2.resize(frame,(240,135)))
    cap.release()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('transcript',type=Path);p.add_argument('output',type=Path);p.add_argument('--ffmpeg',default='ffmpeg');a=p.parse_args();build(a.source,a.transcript,a.output,a.ffmpeg)
