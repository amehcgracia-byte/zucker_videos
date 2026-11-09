"""One motion clock for adjacent, time-contiguous views of the same camera."""
from copy import deepcopy

VERSION = 1


def _key(segment):
    if segment.get('filler') or segment.get('motion_enabled') is False:
        return None
    source = segment.get('source_path') or segment.get('clip_path')
    shot = segment.get('spherical_shot') or {}
    if shot:
        if shot.get('type') in {'planet', 'recorded_move'}:
            return None
        return (source, 'sphere', *(shot.get(k) for k in
                ('type', 'yaw', 'pitch', 'fov', 'roll', 'projection_preset', 'projection_control')))
    motion = segment.get('motion') or {}
    if motion.get('type') != 'ken_burns' and not segment.get('flat_framing'):
        return None
    return (source, 'flat', repr(segment.get('flat_framing')), repr(segment.get('performer_view')),
            *(motion.get(k) for k in ('target_x', 'target_y', 'lock_target')))


def continuous_views(segments, *, preserve_authored=False):
    """Keep the editable cut boundaries, but never restart an identical view."""
    output = deepcopy(segments)
    # Stage plans already carry their motion clocks. A manual crop must
    # neither inherit its neighbour's clock nor re-time the neighbouring run.
    if preserve_authored and any(segment.get('manual_view') for segment in output):
        return output
    for segment in output:
        for field in ('spherical_shot', 'motion'):
            for key in ('continuity_duration_sec', 'continuity_offset_sec'):
                (segment.get(field) or {}).pop(key, None)
    runs = []
    for segment in output:
        key = _key(segment)
        previous = runs[-1][-1] if runs else None
        contiguous = previous and abs(float(previous.get('clip_start_sec', 0)) + float(previous.get('duration_sec', 0)) - float(segment.get('clip_start_sec', 0))) < .04 and abs(float(previous.get('master_start_sec', 0)) + float(previous.get('duration_sec', 0)) - float(segment.get('master_start_sec', 0))) < .04
        if key is not None and contiguous and key == _key(previous):
            runs[-1].append(segment)
        else:
            runs.append([segment])
    for run in runs:
        if len(run) < 2 or _key(run[0]) is None:
            continue
        field = 'spherical_shot' if run[0].get('spherical_shot') else 'motion'
        profile = deepcopy(run[0].get(field) or {})
        duration = sum(float(s['duration_sec']) for s in run)
        offset = 0.
        for segment in run:
            segment[field] = {**profile, 'continuity_duration_sec': duration, 'continuity_offset_sec': offset}
            offset += float(segment['duration_sec'])
    return output
