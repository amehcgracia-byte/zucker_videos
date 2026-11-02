"""Bounded SDR shadow recovery shared by review and final export."""
import math

VERSION = 1
DEFAULT = {'auto': True, 'light': 0.0, 'shadows': 0.0}


def validate(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise ValueError('Choose Auto, Light and Shadows')
    result = {**DEFAULT, **value}
    if not isinstance(result['auto'], bool):
        raise ValueError('Auto must be on or off')
    for key, low, high in [('light', -1, 1), ('shadows', -1, 1)]:
        v = result[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError('Choose light and shadows between -1 and 1')
        result[key] = float(v)
    return result


def settings(project, segment):
    config = project.data.get('settings', {}).get('edit', {}).get('video_light') or {}
    path = str(segment.get('source_path') or segment.get('clip_path') or '')
    value = segment.get('video_light') or (config.get('cameras') or {}).get(path)
    return validate(value or DEFAULT) if config.get('enabled') or value else None


def profile(project, segment, measured, target_high=80.0):
    spec = settings(project, segment)
    if spec is None:
        return {}
    luma = float(measured.get('luma') or 0)
    high = float(measured.get('luma_high') or luma*2)
    anchor = min(.55, max(.04, (high-16)/219))
    target = anchor
    if 2 < luma < 65 and high > 25:
        target = min(.65, anchor+.18, max(anchor, (target_high-16)/219))
    return {'video_light': spec, 'light_anchor': anchor, 'light_target': target, 'light_recipe': VERSION}


def filtergraph(color, override=None):
    spec = override or color.get('video_light')
    if spec is None:
        return ''
    spec = validate(spec)
    anchor = float(color.get('light_anchor') or .15)
    delta = float(color.get('light_target') or anchor)-anchor if spec['auto'] else 0.
    shadow = spec['shadows']
    light = spec['light']
    # One shared luminance anchor for the cameras. The curve keeps its black
    # toe and highlight shoulder; no gain chasing individual frames.
    knots = [(0, 0), (.02, .01), (.06, .07), (.15, .27), (.35, .52), (.65, .80), (1, 1)]
    # A bounded gamma curve cannot amplify the middle tones uncontrollably
    # when a stage's measured anchor is close to black.
    target_anchor = max(anchor, min(.95, anchor + delta))
    exponent = max(.65, min(1., math.log(target_anchor) / math.log(anchor))) if delta > 0 else 1.
    if all(abs(x-anchor)>.005 for x,_ in knots):
        knots=sorted([*knots,(anchor,anchor)])
    points = []
    previous = -1
    for x, target in knots:
        y = x**exponent + shadow * (target-x) + light * 0.30 * (4*x*(1-x))
        if x == .02 and delta > 0:
            y = min(y, .02 - min(.01, delta*.1))
        y = max(0., min(1., y))
        # Keep ordering even at extreme manual settings.
        y = max(previous + .0001, y) if x not in (0, 1) else x
        y = min(.9999, y) if x < 1 else 1.
        previous = y
        points.append(f'{x:.4f}/{y:.4f}')
    if not delta and not shadow and not light:
        return ''
    return "curves=master='" + ' '.join(points) + "'"


def resolved_profile(project, segment):
    """Use the final export's measurement and camera matching for review too."""
    if settings(project, segment) is None:
        return {}
    import json
    from core.stages.export import _color_profiles_for_segments
    try:
        segments = json.loads((project.artifacts_dir/'edit_plan.json').read_text()).get('segments') or [segment]
    except (OSError, ValueError):
        segments = [segment]
    profiles = _color_profiles_for_segments(project, segments, [])
    result = dict(profiles.get(str(segment.get('clip_path') or ''), {}))
    result['video_light'] = settings(project, segment)
    return result
