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


# Typefaces a caption can use: on the Mac and on Windows alike.
FONTS = ('Arial Bold', 'Arial Black', 'Impact', 'Georgia', 'Verdana', 'Trebuchet MS', 'Courier New', 'Comic Sans MS')


def _words(text, start, end):
    """Words of a corrected caption spread over its time by length (the heard words' timings
    belong to what was heard)."""
    words = text.split()
    total = sum(len(word) + 1 for word in words) or 1
    out, at = [], float(start)
    for word in words:
        length = (float(end) - float(start)) * (len(word) + 1) / total
        out.append({'text': word, 'start': round(at, 3), 'end': round(at + length, 3)})
        at += length
    return out


def apply(cues, layout):
    """The cues with the person's changes: position, size, typeface, colour and the words
    corrected (a caption keeps its identity, the words it was made from, when corrected)."""
    result = []
    for cue in cues:
        key = hashlib.sha256(json.dumps([cue['lines'], cue['start'], cue['end']], ensure_ascii=False).encode()).hexdigest()[:20]
        override = {**cue.get('style_override', {}), **layout.get('cues', {}).get(key, {})}
        text = override.pop('text', None)
        changed = {'lines': [text], 'words': _words(text, cue['start'], cue['end']), 'heard': ' '.join(cue['lines'])} if text else {}
        result.append({**cue, **changed, 'id': key, 'style_override': override})
    return result


def save(project, key, changes, revision, cues):
    data = load(project)
    if int(revision) != data['revision']:
        raise ValueError('Captions changed in another window; reopen the scene and try again')
    if key not in {cue['id'] for cue in cues}:
        raise ValueError('That caption is no longer in this edit')
    allowed = {'horizontal': (0, 100), 'vertical': (0, 100), 'size': (12, 400)}
    values, cleared = {}, []
    for name, value in changes.items():
        if name == 'text':
            text = ' '.join(str(value or '').split())
            if len(text) > 240:
                raise ValueError('A caption holds 240 letters at most')
            if text:
                values['text'] = text
            else:
                cleared.append('text')                  # back to the words that were heard
            continue
        if name == 'font':
            if value not in FONTS:
                raise ValueError('Choose one of the caption typefaces')
            values['font'] = value
            continue
        if name == 'color':
            if not isinstance(value, str) or len(value) != 7 or not value.startswith('#') or any(c not in '0123456789abcdefABCDEF' for c in value[1:]):
                raise ValueError('A caption colour is #rrggbb')
            values['color'] = value.lower()
            continue
        if name == 'italic':
            values['italic'] = bool(value)
            continue
        if name not in allowed or isinstance(value, bool):
            raise ValueError('Choose caption words, typeface, colour, position or size')
        number = float(value)
        lo, hi = allowed[name]
        if not math.isfinite(number) or not lo <= number <= hi:
            raise ValueError(f'Caption {name} must be between {lo} and {hi}')
        values[name] = number
    kept = {name: value for name, value in data['cues'].get(key, {}).items() if name not in cleared}
    data['cues'][key] = {**kept, **values}
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
