"""Explicit internet reference search/import; no private media is sent to search engines."""
from __future__ import annotations
import hashlib
import html
import io
import json
import math
import re
import urllib.parse
import urllib.request
from pathlib import Path
from PIL import Image, ImageOps
from core.project import atomic_write_json

API = 'https://commons.wikimedia.org/w/api.php'
AGENT = 'ZuckerEditor/2.6.4 (reference search; github.com/amehcgracia-byte/zucker_videos)'
HOSTS = {'commons.wikimedia.org', 'upload.wikimedia.org', 'thumb.wikimedia.org'}


def _checked(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None,443):
        raise ValueError('Only Wikimedia reference images can be downloaded here')
    return url


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _checked(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url, limit=2_000_000):
    _checked(url)
    opener = urllib.request.build_opener(_Redirect())
    with opener.open(urllib.request.Request(url, headers={'User-Agent':AGENT}),timeout=20) as response:
        _checked(response.geturl())
        data = response.read(limit+1)
    if len(data)>limit:
        raise ValueError('Reference is too large; choose a smaller image')
    return data


def _plain(value):
    return html.unescape(re.sub('<[^>]*>','',str(value or '')))[:1200]


def _query(**params):
    return json.loads(_fetch(API+'?'+urllib.parse.urlencode(dict(action='query',format='json',**params))))


def _items(data):
    result=[]
    for page in sorted(data.get('query',{}).get('pages',{}).values(),key=lambda p:p.get('index',0)):
        infos=page.get('imageinfo') or []
        if not infos:continue
        info=infos[0]
        if info.get('mime') not in ('image/jpeg','image/png','image/webp'):continue
        meta=info.get('extmetadata') or {}
        get=lambda k:_plain(meta.get(k,{}).get('value',''))
        result.append(dict(title=page['title'],name=page['title'].removeprefix('File:'),
                           thumb=_checked(info.get('thumburl') or info['url']),url=_checked(info.get('thumburl') or info['url']),
                           source=info.get('descriptionurl',''),author=get('Artist'),license=get('LicenseShortName'),
                           license_url=get('LicenseUrl'),credit=get('Credit')))
    return result


def search(query):
    query=str(query).strip()
    if not query or len(query)>160:raise ValueError('Search for a person, object or place (1–160 characters)')
    return _items(_query(generator='search',gsrsearch=query+' filetype:bitmap',gsrnamespace=6,gsrlimit=12,
                         prop='imageinfo',iiprop='url|mime|extmetadata',iiurlwidth=360))


def import_photo(project, title):
    if not isinstance(title,str) or not title.startswith('File:') or len(title)>300:
        raise ValueError('Choose an image from the search results')
    found=_items(_query(titles=title,prop='imageinfo',iiprop='url|mime|extmetadata',iiurlwidth=1600))
    if not found:raise ValueError('This reference is no longer available as a supported image')
    item=found[0]
    raw=_fetch(item['url'],20_000_000)
    with Image.open(io.BytesIO(raw)) as opened:
        if opened.width*opened.height>24_000_000:raise ValueError('Choose an image under 24 megapixels')
        picture=ImageOps.exif_transpose(opened).convert('RGBA')
    output=io.BytesIO();picture.save(output,format='PNG');data=output.getvalue()
    key=hashlib.sha256(data).hexdigest()
    library=project.folder/'assets'/'show-library';library.mkdir(parents=True,exist_ok=True)
    target=library/f'reference-{key}.png'
    if not target.exists():target.write_bytes(data)
    catalog_path=library/'catalog.json'
    catalog=json.loads(catalog_path.read_text()) if catalog_path.is_file() else {}
    catalog[str(target)]={**item,'name':item['name'],'origin':'Wikimedia Commons · '+item['license'],'retired':False,'sha256':key}
    atomic_write_json(catalog_path,catalog)
    photos=project.folder/'photos';photos.mkdir(parents=True,exist_ok=True)
    photo=photos/target.name
    if not photo.exists():photo.write_bytes(data)
    index=photos/'photos.json';items=json.loads(index.read_text()) if index.is_file() else []
    record=dict(name=item['name'],photo=str(photo),chosen='internet reference',reference=item)
    atomic_write_json(index,[entry for entry in items if entry['photo']!=str(photo)]+[record])
    return dict(path=str(target),photo=record,reference=item)


def video_reference(project, body):
    url=str(body.get('url') or '').strip()
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or p.username or p.password or p.hostname not in {'youtube.com','www.youtube.com','youtu.be','vimeo.com','www.vimeo.com'} or p.port not in (None,443):
        raise ValueError('Use an HTTPS YouTube or Vimeo reference link')
    start,end=float(body.get('start',0)),float(body.get('end',0))
    if not all(map(math.isfinite,(start,end))) or start<0 or end<=start:
        raise ValueError('Choose an end after the start (seconds)')
    record=dict(id=hashlib.sha256(json.dumps([url,start,end]).encode()).hexdigest()[:20],url=url,start=start,end=end,
                scene_id=str(body.get('scene_id') or ''),selected_text=str(body.get('selected_text') or '')[:400],
                at=float(body.get('at',0)),status='link_only')
    if not math.isfinite(record['at']):raise ValueError('Choose a valid scene time')
    path=project.artifacts_dir/'show_references.json'
    items=json.loads(path.read_text()) if path.is_file() else []
    atomic_write_json(path,[item for item in items if item['id']!=record['id']]+[record])
    return record


def head_box(photo):
    """Suggest one face/head rectangle; the existing keep/erase/box tools remain available."""
    import cv2
    import numpy as np
    image=np.asarray(Image.open(photo).convert('RGB'))
    height,width=image.shape[:2]
    scale=min(1,1000/max(width,height))
    grey=cv2.cvtColor(cv2.resize(image,(round(width*scale),round(height*scale))),cv2.COLOR_RGB2GRAY)
    classifier=cv2.CascadeClassifier(str(Path(cv2.data.haarcascades)/'haarcascade_frontalface_default.xml'))
    found=classifier.detectMultiScale(grey,scaleFactor=1.1,minNeighbors=5,minSize=(30,30))
    if not len(found):return None
    x,y,w,h=max(found,key=lambda box:box[2]*box[3]);x,y,w,h=[float(v)/scale for v in (x,y,w,h)]
    x0,y0=max(0,x-w*.18),max(0,y-h*.4)
    x1,y1=min(width,x+w*1.18),min(height,y+h*1.2)
    return [x0/width,y0/height,(x1-x0)/width,(y1-y0)/height]
