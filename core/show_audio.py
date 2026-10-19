"""Project-local narration cleanup, word-timed soft censorship and mix controls."""
from __future__ import annotations
import hashlib
import io
import json
import math
import re
import subprocess
import threading
import time
import unicodedata
import wave
from pathlib import Path
import numpy as np
from core.project import atomic_write_json

VERSION='show-audio-v3'
RATE=48000
DEFAULTS={'revision':0,'censor':True,'denoise':True,'narration_volume':1.,'excluded_words':[]}
TERMS=frozenset('fuck fucking fucked fucker fuckers motherfucker motherfuckers shit bullshit bitch bitches asshole assholes cunt joder coño cojones hostia mierda puta putas puto putos cabron cabrones'.split())
LOCK=threading.RLock()

def load(project):
    path=project.artifacts_dir/'show_audio.json'
    return {**DEFAULTS,**(json.loads(path.read_text()) if path.is_file() else {})}

def save(project, changes):
    with LOCK:
        doc=load(project)
        for name in ('censor','denoise'):
            if name in changes:
                if not isinstance(changes[name],bool):raise ValueError('Choose on or off for '+name)
                doc[name]=changes[name]
        if 'narration_volume' in changes:
            value=float(changes['narration_volume'])
            if not math.isfinite(value) or not 0<=value<=2:raise ValueError('Volume must be between 0 and 200%')
            doc['narration_volume']=value
        if 'excluded_words' in changes:
            if not isinstance(changes['excluded_words'],list) or len(changes['excluded_words'])>100:raise ValueError('Enter up to 100 uncensored words')
            doc['excluded_words']=[str(word).lower().strip()[:40] for word in changes['excluded_words']]
        doc['revision']+=1;atomic_write_json(project.artifacts_dir/'show_audio.json',doc)
        return doc

def normalized(word):
    return ''.join(c for c in unicodedata.normalize('NFD',word.lower()) if not unicodedata.combining(c))

def censor_spans(words, excluded=()):
    terms={normalized(word) for word in TERMS}-{normalized(word) for word in excluded}
    found=[]
    for text,start,end in words:
        text=str(text);start=float(start);end=float(end)
        if not math.isfinite(start+end) or end<=start:continue
        for token in re.finditer(r"[^\W\d_]+(?:['’][^\W\d_]+)?",text,re.UNICODE):
            word=normalized(token.group())
            if word not in terms:continue
            # Whisper normally supplies a single token. Multiword legacy spans are estimated.
            single=len(list(re.finditer(r'\S+',text)))==1
            a=start if single else start+(end-start)*token.start()/max(1,len(text))
            b=end if single else start+(end-start)*token.end()/max(1,len(text))
            found.append({'word':token.group(),'start':max(0.,a-.025),'end':b+.025,'estimated':not single})
    return found

def soft_error(seconds=.24, rate=RATE, amplitude=.1):
    """An original muted two-note 'not allowed' sound, with rounded transients."""
    out=np.zeros(max(1,int(round(seconds*rate))),np.float32)
    for at,length,hz in ((0.,.11,490.),(.09,.14,330.)):
        lo=int(at*rate);n=min(len(out)-lo,int(length*rate))
        if n<=0:continue
        t=np.arange(n,dtype=np.float32)/rate
        envelope=np.sin(np.pi*np.minimum(1.,t/length))**2
        out[lo:lo+n]+=amplitude*envelope*(np.sin(2*np.pi*hz*t)+.12*np.sin(4*np.pi*hz*t))
    return out

def audition():
    target=io.BytesIO()
    with wave.open(target,'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(RATE)
        out.writeframes((soft_error()*32767).astype('<i2').tobytes())
    return target.getvalue()

def process(samples, plan):
    options=plan.get('audio_settings')
    if not options:return samples
    out=samples.copy()*float(options.get('narration_volume',1))
    offset=float(plan.get('_edited_offset',0))
    if options.get('censor',True):
        for span in plan.get('audio_censor_spans') or []:
            first=max(0,round((span['start']-offset)*RATE));last=min(len(out),round((span['end']-offset)*RATE))
            if last<=first:continue
            # Keep only the short outside-word fades; the actual word is fully replaced.
            original=out[first:last].copy();n=last-first
            tone=soft_error(max(.24,n/RATE),amplitude=.09*float(options.get('narration_volume',1)))[:n,None]
            replacement=np.repeat(tone,out.shape[1],axis=1)
            fade=min(round(.007*RATE),n//2)
            if fade:
                ramp=np.linspace(0,1,fade,dtype=np.float32)[:,None]
                replacement[:fade]=original[:fade]*(1-ramp)+replacement[:fade]*ramp
                replacement[-fade:]=replacement[-fade:]*(1-ramp)+original[-fade:]*ramp
            out[first:last]=replacement
    # A transparent peak safety ceiling, not loudness normalization.
    peak=float(np.max(np.abs(out))) if len(out) else 0
    if peak>.98:out*=.98/peak
    return out.astype(np.float32)

def attach(project, plan):
    from core.show import edited_words
    settings=load(project)
    try:words=edited_words(plan)
    except (KeyError,TypeError,IndexError):words=[]
    return {**plan,'audio_settings':settings,'audio_censor_spans':censor_spans(words,settings['excluded_words'])}

def _run(command,cancel=None,timeout=600):
    with subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE) as process:
        # ffmpeg loglevel=error keeps stderr bounded; drain it independently.
        errors=[]
        reader=threading.Thread(target=lambda:errors.append(process.stderr.read()),daemon=True);reader.start();started=time.monotonic()
        try:
            while process.poll() is None:
                if cancel:cancel()
                if time.monotonic()-started>timeout:raise TimeoutError('Audio cleanup timed out')
                time.sleep(.1)
        except BaseException:
            process.kill();process.wait();raise
        reader.join()
        if process.returncode:raise subprocess.CalledProcessError(process.returncode,command,stderr=b''.join(errors).decode(errors='replace')[-300:])

def prepare_noise(project, source, *, ffmpeg, cancel=None):
    """Inspect a short sample, clean only a measurable quiet noise floor, keep originals."""
    from core.show_backgrounds import file_digest
    if cancel:cancel()
    source=Path(source);digest=file_digest(source)
    folder=project.folder/'assets/audio-clean';folder.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256((VERSION+digest).encode()).hexdigest()[:24]
    report=folder/(key+'.json');cleaned=folder/(key+'.flac')
    if report.is_file():
        doc=json.loads(report.read_text())
        if doc['status']!='reduced' or cleaned.is_file():return doc
    raw=subprocess.run([ffmpeg,'-v','error','-nostdin','-i',str(source),'-t','12','-vn','-ac','1','-ar','48000','-f','f32le','-'],check=True,capture_output=True,timeout=40).stdout
    sample=np.frombuffer(raw,dtype='<f4');n=len(sample)//960
    if not n:raise ValueError('No audio samples were found')
    levels=np.sqrt(np.mean(sample[:n*960].reshape(n,960)**2,axis=1)+1e-12)
    floor=float(np.percentile(levels,15));speech=float(np.percentile(levels,90))
    db=20*math.log10(max(1e-6,floor));contrast=20*math.log10(max(1e-6,speech)/max(1e-6,floor))
    doc={'version':VERSION,'source_sha256':digest,'source':str(source),'noise_db':round(db,1),'status':'clean' if db < -48 else 'uncertain'}
    if db>=-48 and contrast>=8:
        gate=min(.04,max(.001,floor*2.2));nf=max(-70,min(-25,db))
        filters=f'highpass=f=65,afftdn=nr=8:nf={nf:.2f}:tn=1,agate=threshold={gate:.6f}:ratio=1.8:range=0.25:attack=10:release=200'
        pending=cleaned.with_suffix('.pending.flac')
        try:
            _run([ffmpeg,'-v','error','-y','-nostdin','-i',str(source),'-vn','-af',filters,'-ar','48000','-ac','2','-c:a','flac',str(pending)],cancel)
            pending.replace(cleaned)
        finally:pending.unlink(missing_ok=True)
        doc.update(status='reduced',path=str(cleaned),filters=filters)
    atomic_write_json(report,doc)
    return doc

def measure(path, ffmpeg, start=0, length=6):
    raw=subprocess.run([ffmpeg,'-v','error','-nostdin','-ss',str(start),'-i',str(path),'-t',str(min(6,max(.1,length))),'-vn','-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True,timeout=30).stdout
    x=np.frombuffer(raw,dtype='<f4')
    if not len(x):raise ValueError('No audio in this source')
    levels=np.sqrt(np.mean(x[:len(x)//320*320].reshape(-1,320)**2,axis=1)+1e-12)
    voiced=levels[levels>.003]
    return float(np.median(voiced)) if len(voiced) else .001,float(np.max(np.abs(x)))

def overlay_tracks(project):
    """Voices on placed characters are tracks too, including library-default voices."""
    from core import show_scenes
    path=project.artifacts_dir/'show_scenes.json'
    if not path.is_file():return []
    tracks=[]
    for scene in show_scenes.load(path)['scenes']:
        for item in scene.get('overlays',[]):
            if item.get('action')!='talk' or item.get('voice_mode')=='mine':continue
            voice=item.get('voice') or show_scenes._character_rig(item['asset']).get('voice')
            if not voice:continue
            tracks.append(dict(id='sticker:'+item['id'],kind='character',scene_id=scene['id'],
                name=Path(voice[0]).stem,source=voice[0],start=float(voice[1]),
                at=float(item.get('at',scene['start'])),duration=float(voice[2]),
                volume=item.get('volume',1.),mode=item.get('audio_mode','own'),under_voice=item.get('under_voice',False)))
    return tracks


def balance(project, ffmpeg, progress=None):
    from core import show_proposals
    plan=json.loads((project.artifacts_dir/'show_plan.json').read_text())
    settings=load(project);noise=plan.get('noise_report') or {}
    measured=noise.get('path') if settings['denoise'] and noise.get('path') and Path(noise['path']).is_file() else plan['source']
    rms,peak=measure(measured,ffmpeg)
    gain=min(2.,.12/max(rms,.001),.9/max(peak,.001))
    path=project.artifacts_dir/'show_proposals.json'
    with show_proposals.LOCK:items=show_proposals.load(path)['proposals']
    snapshots={item['id']:json.loads(json.dumps(item['params'])) for item in items}
    changes={};failed=[];applied=0
    tracks=[item for item in items if item['kind'] in ('clip','jingle','character') and item['status']!='discarded']
    for index,item in enumerate(tracks):
        p=item['params'];source=p.get('clip') or p.get('path') or (p.get('voice') or {}).get('source')
        if not source:continue
        try:
            rms,peak=measure(source,ffmpeg,(p.get('voice') or {}).get('start',0) if item['kind']=='character' else p.get('clip_start',p.get('start',0)),(p.get('voice') or {}).get('length',item['duration']) if item['kind']=='character' else item['duration'])
            gain_track=min(2.,(.07 if item['kind']=='jingle' else .10)/max(rms,.001),.9/max(peak,.001))
            changes[item['id']]={'audio_volume':gain_track,'audio_mode':'mix','clip_audio':True,'under_voice':True} if item['kind']=='clip' else {'volume':gain_track,'duck':1.,'under_voice':True}
        except (OSError,ValueError,subprocess.SubprocessError) as exc:failed.append({'id':item['id'],'reason':str(exc)[:120]})
        if progress:progress((index+1)/max(1,len(tracks)))
    # Placed, speaking library characters are not proposal records.
    overlay_changes={}
    for track in overlay_tracks(project):
        try:
            rms,peak=measure(track['source'],ffmpeg,track['start'],track['duration'])
            overlay_changes[track['id']]=(track,min(2.,.1/max(rms,.001),.9/max(peak,.001)))
        except (OSError,ValueError,subprocess.SubprocessError) as exc:failed.append({'id':track['id'],'reason':str(exc)[:120]})
    with show_proposals.LOCK:
        stored=show_proposals.load(path)
        for item in stored['proposals']:
            if item['id'] in changes:
                if item['params']!=snapshots[item['id']]:failed.append({'id':item['id'],'reason':'This track changed while balancing; your edit was kept.'});continue
                item['params'].update(changes[item['id']]);applied+=1
        show_proposals.save(path,stored['proposals'],**{k:v for k,v in stored.items() if k not in ('proposals','version')})
        if overlay_changes:
            from core import show_scenes
            scene_path=project.artifacts_dir/'show_scenes.json';document=show_scenes.load(scene_path)
            current={t['id']:t for t in overlay_tracks(project)}
            for scene in document['scenes']:
                for overlay in scene.get('overlays',[]):
                    key='sticker:'+overlay['id']
                    if key not in overlay_changes:continue
                    before,value=overlay_changes[key]
                    if current.get(key)!=before:
                        failed.append({'id':key,'reason':'This voice changed while balancing; your edit was kept.'});continue
                    overlay.update(volume=value,under_voice=True);applied+=1
            show_scenes.save(scene_path,document)
        if load(project)['revision']==settings['revision']:save(project,{'narration_volume':gain})
        else:failed.append({'id':'narration','reason':'Narration controls changed while balancing; your edit was kept.'})
    return {'balanced':applied,'unavailable':failed}
