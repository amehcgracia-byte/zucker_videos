"""Conservative fallbacks for optional inserts; never close gaps on the master clock."""
import copy
import math


def optional_fallbacks(project, segments, records):
    from core.fillers import filler_paths
    from core.video_light import settings as light_settings
    from core.stages.sync import load_sync_map, sync_map_is_current, SyncStage
    fillers = filler_paths(project)
    if not fillers or not sync_map_is_current(project):
        return {}
    state=project.data.get('stages',{}).get('sync',{})
    if state.get('status') != 'done' or state.get('fingerprint') != SyncStage().inputs_fingerprint(project):
        return {}
    clips = (load_sync_map(project, missing_ok=True) or {}).get('clips', {})
    keys = {str(r['path']): i for i, r in enumerate(records)}
    result = {}
    for index, slot in enumerate(segments):
        if str(slot.get('source_path') or slot.get('clip_path')) not in fillers:
            continue
        alternatives = []
        # Only use an already authored, static flat camera framing. Do not invent
        # a view, propagate an insert's grading, or guess a camera's sync offset.
        for shot in segments:
            path = str(shot.get('source_path') or shot.get('clip_path'))
            if path in fillers or path not in keys or shot.get('spherical_shot') or shot.get('motion') or shot.get('projection') in ('equirect', 'raw_insv'):
                continue
            clip = next((c for c in clips.values() if path in (c.get('source_path'), c.get('path'))), None)
            if not clip or any(clip.get(k) for k in ('error', 'no_audio', 'low_confidence', 'unstable_sync')):
                continue
            try:
                start = float(slot['master_start_sec']) - float(clip['offset_sec'])
                length = float(slot['duration_sec'])
                duration = float(clip['duration_sec'])
                confidence = float(clip['confidence'])
                threshold = float(clip.get('threshold', 6))
                if not all(math.isfinite(v) for v in (start, length, duration, confidence, threshold)) or confidence < threshold or start < 0 or start + length > duration:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            item = {k: copy.deepcopy(shot[k]) for k in ('flat_framing', 'video_light', 'projection') if k in shot}
            light=light_settings(project,shot)
            if light is not None:item['video_light']=copy.deepcopy(light)
            item.update(source=keys[path], clip_start_sec=start, duration_sec=length, master_start_sec=slot['master_start_sec'], playback_speed=1.0)
            if item not in alternatives:
                alternatives.append(item)
        if alternatives:
            result[str(index)] = sorted(alternatives, key=lambda s: s['source'])
    return result
