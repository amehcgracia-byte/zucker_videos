"""Fixed-camera crops. Person locations are visual evidence, never instrument IDs.

Five small proxy stills per song; no downloads, source separation or model calls.
Confirmed roles and measured stem activity can refine the otherwise visual rotation.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
import statistics

from core.project import atomic_write_json
from core.stages.base import stable_fingerprint

VERSION = 5
ROLES = {'unknown', 'pianist', 'bassist', 'saxophonist', 'guitarist', 'drummer', 'singer'}
ALIASES = {'keys': 'pianist', 'keys_l': 'pianist', 'keys_r': 'pianist', 'piano': 'pianist',
           'bass': 'bassist', 'sax': 'saxophonist', 'saxophone': 'saxophonist',
           'guitar': 'guitarist', 'guitar_electric': 'guitarist', 'guitar_acoustic': 'guitarist',
           'drums': 'drummer', 'vocal': 'singer', 'vocals': 'singer'}
LOGGER = logging.getLogger(__name__)


def role(value):
    value = str(value).strip().lower()
    return ALIASES.get(value, value)


def _people(clusters):
    people = []
    for cluster in sorted(clusters, key=lambda c: c['x']):
        if len(cluster['samples']) < 3:
            continue
        zoom = max(1.2, min(1.8, 1 / (statistics.median(s[2] for s in cluster['samples']) * 1.5)))
        people.append({'id': f'person-{len(people)+1}', 'x': round(cluster['x'], 4),
                       'y': round(cluster['y'], 4), 'zoom': round(zoom, 3), 'role': 'unknown'})
    return people[:8]


def _detect(path, window, offset, checkpoint):
    import cv2
    from core.operator_avoidance import _model_paths
    from core.reel_framing import _detect_frame_boxes
    # A private network: OpenCV setInput/forward is not safe on a shared singleton.
    proto, model = _model_paths()
    net = cv2.dnn.readNetFromCaffe(str(proto), str(model)) if proto.is_file() and model.is_file() else None
    cap = cv2.VideoCapture(str(path))
    face = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / 'haarcascade_frontalface_alt2.xml'))
    clusters = []
    try:
        duration = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / max(1., float(cap.get(cv2.CAP_PROP_FPS) or 30))
        start = max(0., float(window.get('start_sec') or 0) - offset)
        span = max(0., min(20., float(window.get('duration_sec') or duration), duration - start - .1))
        for sample in range(5):
            checkpoint(sample)
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + span * sample / 5) * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            frame = cv2.resize(frame, (min(w, 960), max(2, round(h / w * min(w, 960)))))
            h, w = frame.shape[:2]
            # Lift only the detector's input in dark venues; rendered light is unchanged.
            import numpy as np
            if float(np.median(frame)) < 45:
                table = np.array([((i/255.)**.6)*255 for i in range(256)], dtype=np.uint8)
                frame = cv2.LUT(frame, table)
            boxes = [dict(zip(('x1','y1','x2','y2'), b['coords']), confidence=b['confidence'])
                     for b in _detect_frame_boxes(net, frame)] if net is not None else []
            if net is not None and w >= h:
                # Two overlapping views keep small seated players visible to the 300px network.
                for left, right in ((0., .6), (.4, 1.)):
                    for box in _detect_frame_boxes(net, frame[:, int(left*w):int(right*w)]):
                        x1,y1,x2,y2 = box['coords']
                        boxes.append({'x1':left+x1*(right-left),'x2':left+x2*(right-left),
                                      'y1':y1,'y2':y2,'confidence':box['confidence']})
            faces = [] if face.empty() else face.detectMultiScale(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                        scaleFactor=1.1, minNeighbors=5, minSize=(14,14))
            # Use faces for seated players missed by the body detector, without duplicates.
            for x,y,fw,fh in faces:
                fx, fy = (x+fw/2)/w, (y+fh/2)/h
                if not any(b['x1'] <= fx <= b['x2'] and b['y1'] <= fy <= b['y2'] for b in boxes):
                    boxes.append({'x1': max(0.,(x-fw)/w), 'x2': min(1.,(x+2*fw)/w),
                                  'y1': max(0.,(y-.4*fh)/h), 'y2': min(1.,(y+5*fh)/h), 'confidence': .65})
            # SSD can return torso and full-body boxes for the same individual.
            unique = []
            for box in sorted(boxes, key=lambda b: b['confidence'], reverse=True):
                cx, cy = (box['x1']+box['x2'])/2, (box['y1']+box['y2'])/2
                if not any(abs(cx-(v['x1']+v['x2'])/2)<.07 and abs(cy-(v['y1']+v['y2'])/2)<.3 for v in unique):
                    unique.append(box)
            used = set()
            for b in sorted(unique, key=lambda b: b['x1']):
                if b['confidence'] < .30 or not .025 < b['x2']-b['x1'] < .6:
                    continue
                x = max(0., min(1., (b['x1']+b['x2'])/2))
                y = max(0., min(1., (b['y1']+b['y2'])/2))
                candidates = [(abs(c['x']-x)+abs(c['y']-y)*.5, i)
                              for i,c in enumerate(clusters) if i not in used]
                distance, i = min(candidates, default=(1.,len(clusters)))
                if distance > .12:
                    i = len(clusters)
                    clusters.append({'x':x, 'y':y, 'samples':[]})
                c = clusters[i]
                c['samples'].append((x,y,b['x2']-b['x1']))
                c['x'] = statistics.median(s[0] for s in c['samples'])
                c['y'] = statistics.median(s[1] for s in c['samples'])
                used.add(i)
    finally:
        cap.release()
    return _people(clusters)


def detect(project, source, window, checkpoint=lambda _: None):
    """Fail soft on unavailable vision, but always propagate cancellation callbacks."""
    from core.shot_quality import _quality_media_path
    path = _quality_media_path(source)
    if not path.is_file():
        return []
    stat = path.stat()
    offset = float(source.get('offset_sec') or 0)
    key = stable_fingerprint({'v':VERSION,'path':str(path),'size':stat.st_size,
                              'mtime':stat.st_mtime_ns,'window':window,'offset':offset})[:24]
    cache = project.cache_dir / 'stage-performers' / f'{key}.json'
    try:
        data = json.loads(cache.read_text())
        if data.get('version') == VERSION:
            return data['people']
    except (ValueError, KeyError, OSError):
        pass
    callback_error = None
    def report(sample):
        nonlocal callback_error
        try:
            checkpoint(sample)
        except Exception as error:
            callback_error = error
            raise
    try:
        people = _detect(path, window, offset, report)
    except Exception:
        if callback_error is not None:
            raise callback_error
        LOGGER.warning('Fixed-camera person analysis unavailable; preserving general shot', exc_info=True)
        return []
    atomic_write_json(cache, {'version':VERSION, 'people':people})
    return people


def validate_activity(raw):
    """Bounded, finite one-second evidence. Missing evidence is never silence."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get('source') != 'separate_tracks' or raw.get('interval_sec') != 1:
        raise ValueError('Choose one-second separate-track activity')
    tracks = raw.get('tracks')
    if not isinstance(tracks,list) or len(tracks) > 32:
        raise ValueError('Too many activity tracks')
    result = []
    for track in tracks:
        identity = role(track.get('role'))
        values = track.get('db')
        if identity not in ROLES-{'unknown'} or not isinstance(values,list) or len(values) > 21600:
            raise ValueError('Invalid instrument activity')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not -120 <= v <= 24 for v in values):
            raise ValueError('Invalid activity level')
        result.append({'role':identity, 'db':values})
    return {'source':'separate_tracks','interval_sec':1,'tracks':result}


def activity_for(activity, start, end):
    if not activity or activity.get('source') != 'separate_tracks':
        return None, None
    known, levels = set(), {}
    for track in activity.get('tracks',[]):
        values = track.get('db') or []
        identity = role(track.get('role'))
        a, b = max(0,int(start)), math.ceil(end)
        # Partial/out-of-window evidence cannot rule anybody out.
        if identity not in ROLES-{'unknown'} or a >= len(values) or b > len(values) or b <= a:
            continue
        known.add(identity)
        reference = sorted(values)[min(len(values)-1,int(len(values)*.9))]
        threshold = max(-60., reference-22.)
        clip = values[a:b]
        if sum(v > threshold for v in clip) >= len(clip)*.5:
            score = sum(10**((v-reference)/20) for v in clip)/len(clip)
            levels[identity] = max(levels.get(identity,0.),score)
    if not known:
        return None, None
    ordered = sorted(levels,key=levels.get,reverse=True)
    lead = ordered[0] if ordered and (len(ordered)==1 or levels[ordered[0]] > max(1.35, levels[ordered[1]]*1.6)) else None
    return {'known':known, 'active':set(levels)}, lead


def choose(people, index, history, *, active=None, lead=None):
    eligible = [p for p in people if active is None or role(p.get('role')) not in active['known']
                or role(p.get('role')) in active['active']]
    # General every four static takes, independent of changing audio activity.
    if not eligible or index % 4 == 0:
        return None
    candidates = [p for p in eligible if not history or p['id'] != history[-1]] or eligible
    leads = [p for p in candidates if lead and role(p.get('role')) == lead]
    if leads and index % 4 in (1,3):
        return leads[0]
    counts = {p['id']:history[-24:].count(p['id']) for p in candidates}
    options = [p for p in candidates if counts[p['id']] == min(counts.values())]
    return options[(index//4) % len(options)]


def apply(segment, people, index, history, activity=None):
    start = float(segment.get('master_start_sec') or 0)
    active, lead = activity_for(activity,start,start+float(segment.get('duration_sec') or 0))
    target = choose(people,index,history,active=active,lead=lead)
    segment['stage_performers'] = people
    segment['performer_view'] = target['id'] if target else 'general'
    segment['flat_framing'] = {k:target[k] for k in ('zoom','x','y')} if target else {'zoom':1.,'x':.5,'y':.5}
    segment.pop('motion',None)
    segment.pop('operator_avoidance',None)
    if target:
        history.append(target['id'])
        segment['editorial_subject'] = target.get('role','unknown')
    segment['performer_evidence'] = 'stem_activity_hint' if active is not None else 'visual_rotation_only'
