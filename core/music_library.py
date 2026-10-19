"""Music/SFX catalog with source licenses and project-owned used assets."""
from __future__ import annotations
import hashlib
import json
import math
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from core import ai_connections
from core.project import atomic_write_json

AUDIO={'.mp3','.wav','.m4a','.aac','.ogg','.flac','.aiff','.opus'}
MAX_BYTES=100*1024*1024


def root():
    from core.storage import data_root
    configured=ai_connections.preferences().get('library_dir')
    return Path(configured).expanduser().resolve() if configured else data_root()/'library'


def set_root(folder):
    path=Path(str(folder)).expanduser()
    if not path.is_absolute():raise ValueError('Choose an absolute music library folder')
    path.mkdir(parents=True,exist_ok=True)
    with ai_connections.LOCK:
        doc=ai_connections.preferences();doc['library_dir']=str(path.resolve());atomic_write_json(ai_connections.preferences_path(),doc)


def _json(url,headers=None):
    try:
        with urlopen(Request(url,headers=headers or {}),timeout=25) as response:
            raw=response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:raise ValueError('The music search response is too large')
            return json.loads(raw)
    except (HTTPError,URLError):raise RuntimeError('Music service unavailable. Check the key, allowance and connection in Connectors.') from None


def reusable(license_url):
    text=str(license_url).lower().replace('http://','https://')
    return text.startswith('https://creativecommons.org/publicdomain/zero/') or text.startswith('https://creativecommons.org/licenses/by/')


def search(provider,query):
    if provider not in ('jamendo','freesound'):raise ValueError('Choose Jamendo or Freesound')
    key=ai_connections.api_key(provider)
    if not key:raise ValueError('Add your '+provider+' key in Connectors first')
    query=str(query).strip()[:150]
    if not query:raise ValueError('Describe the music or sound you want')
    if provider=='jamendo':
        data=_json('https://api.jamendo.com/v3.0/tracks/?'+urlencode(dict(client_id=key,search=query,format='json',limit=40,audioformat='mp32',audiodlformat='mp32',ccnc='false',ccnd='false',ccsa='false')))
        if data.get('headers',{}).get('status')!='success':raise ValueError('Jamendo refused the search. Check your client ID.')
        records=[dict(source='jamendo',id=str(r['id']),name=r['name'],artist=r.get('artist_name',''),duration=float(r.get('duration') or 0),
                      license=r.get('license_ccurl',''),preview=r.get('audio',''),download=r.get('audiodownload','') if r.get('audiodownload_allowed') is True else '',
                      page=r.get('shareurl',''),preview_only=False) for r in data.get('results',[])]
    else:
        data=_json('https://freesound.org/apiv2/search/text/?'+urlencode(dict(query=query,filter='license:"Creative Commons 0"',page_size=40,fields='id,name,username,license,previews,duration,url')),
                   {'Authorization':'Token '+key})
        records=[dict(source='freesound',id=str(r['id']),name=r['name'],artist=r.get('username',''),duration=float(r.get('duration') or 0),
                      license=r.get('license',''),preview=r.get('previews',{}).get('preview-hq-mp3',''),download=r.get('previews',{}).get('preview-hq-mp3',''),
                      page=r.get('url',''),preview_only=True) for r in data.get('results',[])]
    for r in records:r['attribution']=f"{r['name']} — {r['artist']} ({r['license']}) {r['page']}"
    return [r for r in records if reusable(r['license'])]


def audio_bytes(record,preview=False):
    url=record.get('preview' if preview else 'download','')
    if not url:raise ValueError('This artist has not enabled downloads')
    host=urlsplit(url).hostname or '';suffix='.jamendo.com' if record['source']=='jamendo' else '.freesound.org'
    if urlsplit(url).scheme!='https' or not (host.endswith(suffix) or host==suffix[1:]):raise ValueError('The service returned an unsupported audio address')
    try:
        with urlopen(Request(url),timeout=45) as response:
            final_host=urlsplit(response.geturl()).hostname or ''
            if not (final_host.endswith(suffix) or final_host==suffix[1:]):raise ValueError('The audio download changed to another service')
            body=response.read(MAX_BYTES+1)
            if len(body)>MAX_BYTES:raise ValueError('Choose a track smaller than 100 MB')
            return body
    except (HTTPError,URLError):raise RuntimeError('Could not download this audio. Check the service connection.') from None


def import_audio(stream,name,metadata=None):
    folder=root();folder.mkdir(parents=True,exist_ok=True)
    ext=Path(name).suffix.lower()
    if ext not in AUDIO:raise ValueError('Choose an audio file')
    fd,pending=tempfile.mkstemp(prefix='.music-import-',suffix=ext,dir=folder)
    import os
    digest=hashlib.sha256();total=0
    try:
        with os.fdopen(fd,'wb') as out:
            while chunk:=stream.read(1024*1024):
                total+=len(chunk)
                if total>MAX_BYTES:raise ValueError('Choose an audio file smaller than 100 MB')
                digest.update(chunk);out.write(chunk)
        from core.ffmpeg import ffprobe
        info=ffprobe(pending);duration=float(info.get('format',{}).get('duration') or 0)
        if not any(s.get('codec_type')=='audio' for s in info.get('streams',[])) or not math.isfinite(duration) or duration<=0:raise ValueError('This file has no playable audio')
        target=folder/(digest.hexdigest()+ext)
        if not target.exists():Path(pending).replace(target)
        meta={**(metadata or {}),'id':digest.hexdigest(),'name':str((metadata or {}).get('name') or Path(name).stem)[:200],
              'source':(metadata or {}).get('source','local'),'duration':duration,'path':str(target),
              'license':(metadata or {}).get('license','User supplied'),'attribution':(metadata or {}).get('attribution','')}
        atomic_write_json(target.with_suffix(target.suffix+'.json'),meta)
        return meta
    finally:Path(pending).unlink(missing_ok=True)


def local(query=''):
    folder=root();result=[]
    if not folder.is_dir():return []
    for p in sorted(folder.rglob('*')):
        if len(result)>=2000:break
        if not p.is_file() or p.suffix.lower() not in AUDIO or p.name.startswith('.'):continue
        try:meta=json.loads(p.with_suffix(p.suffix+'.json').read_text())
        except (OSError,ValueError):meta={}
        item={**meta,'id':meta.get('id') or hashlib.sha256(str(p.resolve()).encode()).hexdigest(),'name':meta.get('name') or p.stem,'path':str(p.resolve()),'license':meta.get('license','User supplied'),'source':meta.get('source','local')}
        if str(query).casefold() in item['name'].casefold():result.append(item)
    return result


def project_tracks(project,ids):
    known={r['id']:r for r in local()};out={}
    for id in set(ids):
        if not id:continue
        if id not in known:raise ValueError('A selected music track is missing from the library; choose it again')
        record=known[id];source=Path(record['path'])
        if not record.get('duration'):
            from core.ffmpeg import ffprobe
            record['duration']=float(ffprobe(str(source)).get('format',{}).get('duration') or 0)
        folder=project.folder/'assets/music';folder.mkdir(parents=True,exist_ok=True)
        # Snapshot the actual bytes used; later library cleanup cannot break this project.
        digest=hashlib.sha256(source.read_bytes()).hexdigest();dest=folder/(digest+source.suffix)
        if not dest.exists():shutil.copy2(source,dest)
        out[id]={**record,'path':str(dest)};atomic_write_json(dest.with_suffix(dest.suffix+'.json'),out[id])
    return out
