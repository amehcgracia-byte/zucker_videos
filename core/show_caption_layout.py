"""Project-local positions shared by live Show captions and their final cue track."""
import hashlib
import json
import math
from core.project import atomic_write_json


def path(project):
    return project.artifacts_dir / 'show_caption_layout.json'


def load(project):
    return json.loads(path(project).read_text()) if path(project).is_file() else {'revision': 0, 'cues': {}}


def enabled(project):
    """Speech captions are the person's choice: off until they turn them on."""
    return bool(load(project).get('enabled', False))


def set_enabled(project, on):
    data = load(project)
    data['enabled'] = bool(on)
    data['revision'] += 1
    atomic_write_json(path(project), data)
    return data


def apply(cues, layout):
    result = []
    for cue in cues:
        key = hashlib.sha256(json.dumps([cue['lines'], cue['start'], cue['end']], ensure_ascii=False).encode()).hexdigest()[:20]
        override = {**cue.get('style_override', {}), **layout.get('cues', {}).get(key, {})}
        result.append({**cue, 'id': key, 'style_override': override})
    return result


def save(project, key, changes, revision, cues):
    data = load(project)
    if int(revision) != data['revision']:
        raise ValueError('Captions changed in another window; reopen the scene and try again')
    if key not in {cue['id'] for cue in cues}:
        raise ValueError('That caption is no longer in this edit')
    allowed = {'horizontal': (0, 100), 'vertical': (0, 100), 'size': (12, 400)}
    values = {}
    for name, value in changes.items():
        if name not in allowed or isinstance(value, bool):
            raise ValueError('Choose caption position or size')
        number = float(value)
        lo, hi = allowed[name]
        if not math.isfinite(number) or not lo <= number <= hi:
            raise ValueError(f'Caption {name} must be between {lo} and {hi}')
        values[name] = number
    data['cues'][key] = {**data['cues'].get(key, {}), **values}
    data['revision'] += 1
    atomic_write_json(path(project), data)
    return data


def without_artwork(cues, proposals, applied_ids):
    """Keep plain captions outside the exact intervals covered by approved artwork."""
    result = []
    for cue in cues:
        spans = [(cue['start'], cue['end'])]
        for item in proposals:
            p = item.get('params') or {}
            if (item['id'] not in applied_ids or item['status'] != 'approved' or item['kind'] != 'word'
                    or not p.get('picture') or p.get('replaces_caption') != cue['id']
                    or p.get('text') != p.get('lettering_text')):
                continue
            lo, hi = item['at'], item['at'] + item['duration']
            pieces = []
            for a, b in spans:
                if hi <= a or lo >= b:
                    pieces.append((a, b))
                else:
                    if a < lo: pieces.append((a, lo))
                    if hi < b: pieces.append((hi, b))
            spans = pieces
        result.extend({**cue, 'start': a, 'end': b} for a, b in spans if b - a > .001)
    return result


def with_insertions(cues, spans):
    """Move narration captions around inserted footage, leaving its clock empty."""
    from core.show_inserts import shift
    points = sorted({float(at) for at, duration in spans if duration > 0})
    result = []
    for cue in cues:
        boundaries = [cue['start'], *[at for at in points if cue['start'] < at < cue['end']], cue['end']]
        for start, end in zip(boundaries, boundaries[1:]):
            words = []
            for word in cue.get('words', []):
                a, b = max(start, word['start']), min(end, word['end'])
                if b > a:
                    words.append({**word, 'start': round(shift(a, spans), 3),
                                  'end': round(shift(b - 1e-6, spans) + 1e-6, 3)})
            part = {**cue, 'start': round(shift(start, spans), 3),
                    'end': round(shift(end - 1e-6, spans) + 1e-6, 3), 'words': words}
            if len(boundaries) > 2 and words:
                part['lines'] = [' '.join(word['text'] for word in words)]
            result.append(part)
    return result
