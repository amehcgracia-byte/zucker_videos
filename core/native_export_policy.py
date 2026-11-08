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
        # Hold the camera's saved base framing. Automatic Ken Burns may be held
        # still in this optional slot; required shots and their movement stay intact.
        # Do not propagate an insert's grading or guess a camera's sync offset.
        for shot in segments:
            path = str(shot.get('source_path') or shot.get('clip_path'))
            motion=shot.get('motion') or {}
            if path in fillers or path not in keys or shot.get('spherical_shot') or (motion and motion.get('type')!='ken_burns') or shot.get('projection') in ('equirect', 'raw_insv'):
                continue
            clip = next((c for c in clips.values() if path in (c.get('source_path'), c.get('path'))), None)
            if not clip or any(clip.get(k) for k in ('error', 'no_audio', 'low_confidence', 'unstable_sync', 'offset_ranges', 'manual_override_ranges')):
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


def omit_missing_optional(project, plan):
    """Materialize a separate export snapshot; never rewrite the authored edit."""
    from pathlib import Path
    from core.fillers import filler_paths
    optional=filler_paths(project)
    shots=plan.get('segments') or []
    missing=[i for i,s in enumerate(shots) if str(s.get('source_path') or s.get('clip_path')) in optional
             and not Path(str(s.get('source_path') or s.get('clip_path'))).is_file()]
    if not missing:
        return plan
    records=project.data.get('inputs',{}).get('videos',[])
    alternatives=optional_fallbacks(project,shots,records)
    result=copy.deepcopy(plan)
    result['optional_omissions']=[]
    result['warnings']=list(result.get('warnings') or [])
    for i in missing:
        source=str(shots[i].get('source_path') or shots[i].get('clip_path'))
        candidate=next((c for c in alternatives.get(str(i),[]) if Path(records[c['source']]['path']).is_file()),None)
        if candidate is None:
            raise ValueError(f'Shot {i+1}: optional insert {Path(source).name} is unavailable and no synchronized camera covers its interval. Reconnect that insert.')
        path=str(records[candidate['source']]['path'])
        replacement={k:copy.deepcopy(v) for k,v in candidate.items() if k!='source'}
        replacement.update(source_path=path,clip_path=path,filename=Path(path).name)
        result['segments'][i]=replacement
        result['optional_omissions'].append({'shot':i+1,'source_path':source,'replacement_path':path,
                                             'master_start_sec':candidate['master_start_sec'],'duration_sec':candidate['duration_sec']})
        result['warnings'].append(f'Shot {i+1}: optional insert {Path(source).name} omitted; synchronized camera {Path(path).name} covers the same interval.')
    return result
