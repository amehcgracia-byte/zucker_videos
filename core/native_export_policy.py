"""Optional inserts preserve the master clock, including when no camera covers a gap."""
import copy
import math


def optional_paths(project, segments):
    """Recover authored filler provenance even when old library settings changed.

    A filename or repetition pattern is never evidence that a camera is optional.
    Mixed uses remain required unless the user explicitly classified the source.
    """
    from core.fillers import filler_paths
    aliases={}
    for record in project.data.get('inputs',{}).get('videos',[]):
        original=str(record.get('path') or '')
        if original:
            aliases[original]=original
            normalized=str((record.get('normalized') or {}).get('path') or '')
            if normalized:aliases[normalized]=original
    def canonical(path):
        return aliases.get(str(path),str(path))
    by_source={}
    for shot in segments:
        path=str(shot.get('source_path') or shot.get('clip_path') or '')
        by_source.setdefault(canonical(path),[]).append(shot)
    selected={canonical(p) for p in filler_paths(project)} | {
        path for path,shots in by_source.items() if all(s.get('filler') is True for s in shots)}
    # Original/normalized aliases are authoritative registered identities. A
    # required appearance through either alias prevents inferred optionality.
    return selected | {path for path,original in aliases.items() if original in selected}



def optional_fallbacks(project, segments, records):
    from core.video_light import settings as light_settings
    from core.stages.sync import load_sync_map, sync_map_is_current, SyncStage
    fillers = optional_paths(project, segments)
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
    optional=optional_paths(project, plan.get("segments") or [])
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
            replacement={'placeholder':'black', 'master_start_sec':shots[i]['master_start_sec'],
                         'duration_sec':shots[i]['duration_sec'], 'clip_start_sec':0, 'playback_speed':1.0}
            path=None
        else:
            path=str(records[candidate['source']]['path'])
            replacement={k:copy.deepcopy(v) for k,v in candidate.items() if k!='source'}
            replacement.update(source_path=path,clip_path=path,filename=Path(path).name)
        result['segments'][i]=replacement
        result['optional_omissions'].append({'shot':i+1,'source_path':source,'replacement_path':path,
                                             'placeholder':replacement.get('placeholder'),
                                             'master_start_sec':shots[i]['master_start_sec'],'duration_sec':shots[i]['duration_sec']})
    for source in sorted({row['source_path'] for row in result['optional_omissions']}):
        rows=[row for row in result['optional_omissions'] if row['source_path']==source]
        black=sum(row['placeholder']=='black' for row in rows)
        detail=f'{black} interval(s) use black. ' if black else ''
        if len(rows)>black:detail+='Verified synchronized cameras cover the other intervals. '
        result['warnings'].append(f"Optional insert {Path(source).name} omitted in {len(rows)} shot(s). "
                                  +detail+"Duration and master audio are unchanged.")
    return result


def materialize_placeholders(project, plan):
    """Generate a lossless neutral visual; no proxy, arbitrary path or sync guess.

    Only the export snapshot changes. Original authored slots remain on disk.
    """
    from pathlib import Path
    import subprocess
    from core.ffmpeg import tool_status, ffprobe
    from core.media_validation import validate_camera_video_metadata
    result=copy.deepcopy(plan)
    for shot in result.get('segments', []):
        if 'placeholder' not in shot:
            continue
        duration=float(shot['duration_sec'])
        if shot['placeholder']!='black' or not math.isfinite(duration) or not 1/60<=duration<=21600:
            raise ValueError('Invalid optional insert placeholder')
        # Frame-aligned length covers fractional slots without truncation.
        frames=math.ceil(duration*30)+1
        root=Path(project.cache_dir)/'optional-placeholders';root.mkdir(parents=True,exist_ok=True)
        path=root/f'black-v1-{frames}.mkv'
        if not path.is_file():
            temporary=path.with_suffix('.tmp.mkv')
            subprocess.run([tool_status()['ffmpeg_path'],'-v','error','-y','-f','lavfi','-i',
                'color=c=black:s=1920x1080:r=30','-frames:v',str(frames),'-an','-c:v','ffv1',
                '-threads','1',str(temporary)],check=True,capture_output=True)
            temporary.replace(path)
        shot.update(source_path=str(path),clip_path=str(path),clip_start_sec=0,playback_speed=1.0,
                    probe=validate_camera_video_metadata(ffprobe(str(path))).summary)
    return result
