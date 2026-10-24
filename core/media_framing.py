"""Automatic framing for ordinary imported clips, never for keyed effect footage.

Black edge strips are removed, and a watermark (a channel logo, a site's mark) is zoomed out
of the picture when it can be found safely: only in a clip that changes (cuts or movement),
where a small mark in a corner or along an edge keeps its outline while everything around it
changes. In a still tripod shot a poster on the wall cannot be told from a logo, so nothing is
done there. The zoom is at most 1.3x; the person can keep the mark instead (a small sidecar
file next to the clip, ``<clip>.framing.json``).
"""
import json
from functools import lru_cache
from pathlib import Path
import cv2
import numpy as np

MAX_WATERMARK_ZOOM = 1.3


def active_rectangle(frames):
    """Remove only black edge strips present in every usable sampled frame."""
    usable = [f for f in frames if f is not None and np.mean(f) > 18]
    if len(usable) < 3:
        return None
    h,w=usable[0].shape[:2]
    edges=[]
    for f in usable:
        black=np.max(f,axis=2)<16
        rows=np.mean(black,axis=1)>.995
        cols=np.mean(black,axis=0)>.995
        def lead(a):
            indices=np.flatnonzero(~a)
            return int(indices[0]) if len(indices) else 0
        edges.append([lead(cols),lead(rows),lead(cols[::-1]),lead(rows[::-1])])
    l,t,r,b=np.min(edges,axis=0)
    # Dark shots and dramatic lighting must not cause large automatic crops.
    if l+r>=w*.65 or t+b>=h*.65:return None
    l,t,r,b=[int(v) if v>=2 else 0 for v in (l,t,r,b)]
    return (l,t,w-l-r,h-t-b)


def _edges(gray):
    return cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))


def watermark_box(frames):
    """(left, top, right, bottom) of a watermark, shares of the frame, or None.
    ``frames``: samples spread over the clip (BGR, same size)."""
    usable = [f for f in frames if f is not None and np.mean(f) > 18]
    if len(usable) < 5:
        return None
    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in usable])
    h, w = gray.shape[1:]
    change = gray.std(axis=0)
    if np.median(change) < 18:
        return None                                  # a still shot: a logo cannot be told from the scenery
    edges = np.stack([_edges(g) > 60 for g in gray])
    lasting = edges.mean(axis=0) >= (len(usable) - 1) / len(usable) - 1e-6   # the same outline in (almost) every sample
    zone = np.zeros((h, w), bool)
    bx, by = int(w * 0.3), int(h * 0.24)
    zone[:by] = zone[-by:] = True
    zone[:, :bx] = zone[:, -bx:] = True
    marks = (lasting & zone).astype(np.uint8)
    joined = cv2.dilate(marks, np.ones((7, 7), np.uint8))   # a logo's letters become one shape
    count, _, stats, _ = cv2.connectedComponentsWithStats(joined)
    best, best_score = None, 0
    for index in range(1, count):
        x, y, bw, bh, _ = stats[index]
        if bw * bh < 0.001 * w * h or bw > 0.35 * w or bh > 0.25 * h or bh < 0.025 * h or bw > 8 * bh:
            continue                                 # a speck, a line or a frame rather than a mark
        inside = int(marks[y:y + bh, x:x + bw].sum())
        if inside < 20:
            continue
        if min(x, w - x - bw) > 0.12 * w or min(y, h - y - bh) > 0.12 * h:
            continue                                 # marks sit in a corner
        pad = max(6, int(0.03 * min(w, h)))
        x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(w, x + bw + pad), min(h, y + bh + pad)
        ring = np.ones((y1 - y0, x1 - x0), bool)
        ring[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = False
        around = change[y0:y1, x0:x1][ring]
        if not len(around) or np.median(around) < 16:
            continue                                 # what surrounds it does not change: scenery
        own = np.median(change[y:y + bh, x:x + bw][marks[y:y + bh, x:x + bw] > 0])
        if own > 25 or np.median(around) < 1.8 * own:
            continue                                 # a mark laid over the video barely changes; scenery changes like its surroundings
        if inside > best_score:
            best, best_score = (x / w, y / h, (x + bw) / w, (y + bh) / h), inside
    return best


def hide_box(box, max_zoom=MAX_WATERMARK_ZOOM, margin=0.015):
    """The largest window of the same shape that leaves ``box`` out (x, y, width, height as
    shares), cutting from the side the mark is on; None when it needs more than ``max_zoom``."""
    left, top, right, bottom = box
    options = []
    if right < 0.5:
        f = 1 - (right + margin); options.append((f, (1 - f, (1 - f) / 2, f, f)))
    if left > 0.5:
        f = left - margin; options.append((f, (0.0, (1 - f) / 2, f, f)))
    if bottom < 0.5:
        f = 1 - (bottom + margin); options.append((f, ((1 - f) / 2, 1 - f, f, f)))
    if top > 0.5:
        f = top - margin; options.append((f, ((1 - f) / 2, 0.0, f, f)))
    if not options:
        return None
    f, window = max(options)
    return window if f >= 1 / max_zoom else None


def _keep_mark(path):
    try:
        return json.loads(Path(str(path) + ".framing.json").read_text()).get("watermark") == "keep"
    except (OSError, ValueError, AttributeError):
        return False


def set_watermark(source, keep):
    """The person's choice for this clip: keep its watermark visible, or let it be zoomed out."""
    sidecar = Path(str(source) + ".framing.json")
    if keep:
        sidecar.write_text(json.dumps({"watermark": "keep"}))
    else:
        sidecar.unlink(missing_ok=True)
    _inspect.cache_clear()


@lru_cache(maxsize=128)
def _inspect(path, size, stamp, keep=False):
    cap=cv2.VideoCapture(path)
    try:
        w,h=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not cap.isOpened() or min(w,h,count)<=0:return None
        # Bounded reads, not a full decode; one stable crop for the entire clip.
        frames=[]
        ratio=min(1.,480/max(w,h))
        for index in np.linspace(0,max(0,count-1),9).astype(int):
            cap.set(cv2.CAP_PROP_POS_FRAMES,int(index));ok,frame=cap.read()
            if ok:frames.append(cv2.resize(frame,(max(1,round(w*ratio)),max(1,round(h*ratio)))))
        rectangle=active_rectangle(frames)
        sx,sy,sw,sh=rectangle or (0,0,frames[0].shape[1],frames[0].shape[0]) if frames else (0,0,1,1)
        mark=watermark_box([f[sy:sy+sh,sx:sx+sw] for f in frames]) if frames else None
        window=hide_box(mark) if mark else None
        if window and not keep:
            # The mark is zoomed out of the picture: the same shape, cut from the mark's side.
            wx,wy,ww,wh=window
            rectangle=(sx+wx*sw,sy+wy*sh,ww*sw,wh*sh)
        if rectangle:
            x,y,cw,ch=rectangle
            # Even dimensions/offsets for chroma-subsampled decoding.
            x,y=int(x/ratio)//2*2,int(y/ratio)//2*2
            cw,ch=min(w-x,round(cw/ratio))//2*2,min(h-y,round(ch/ratio))//2*2
        else:x,y,cw,ch=0,0,w//2*2,h//2*2
        info=dict(x=x,y=y,width=cw,height=ch,portrait=cw<ch)
        if mark:
            info["watermark"]={"box":[round(v,4) for v in mark],"zoom":round(1/window[2],3) if window else None,
                               "hidden":bool(window) and not keep}
        return info
    finally:cap.release()


def inspect(source):
    path=Path(source)
    try:
        stat=path.stat()
        return _inspect(str(path.resolve()),stat.st_size,stat.st_mtime_ns,_keep_mark(path))
    except OSError:return None


def filters(input_label, output_label, source, width, height):
    info=inspect(source)
    crop=f"crop={info['width']}:{info['height']}:{info['x']}:{info['y']}," if info else ''
    cover=f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    if info and info['portrait'] and width>height:
        key=output_label
        return [f"[{input_label}]{crop}split=2[{key}front][{key}back]",
                f"[{key}back]{cover},gblur=sigma=24[{key}blur]",
                f"[{key}front]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[{key}fit]",
                f"[{key}blur][{key}fit]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[{output_label}]"]
    return [f"[{input_label}]{crop}{cover},format=yuv420p[{output_label}]"]


def frame(rgb,width,height,info=None):
    """The equivalent framing for live/composited video readers (RGB)."""
    if info:
        rgb=rgb[info['y']:info['y']+info['height'],info['x']:info['x']+info['width']]
    h,w=rgb.shape[:2]
    scale=max(width/w,height/h)
    enlarged=cv2.resize(rgb,(max(width,round(w*scale)),max(height,round(h*scale))))
    eh,ew=enlarged.shape[:2]
    result=enlarged[(eh-height)//2:(eh-height)//2+height,(ew-width)//2:(ew-width)//2+width].copy()
    if w<h and width>height:
        result=cv2.GaussianBlur(result,(0,0),max(2,width/40))
        scale=min(width/w,height/h);small=cv2.resize(rgb,(max(1,round(w*scale)),max(1,round(h*scale))))
        sh,sw=small.shape[:2];result[(height-sh)//2:(height-sh)//2+sh,(width-sw)//2:(width-sw)//2+sw]=small
    return result
