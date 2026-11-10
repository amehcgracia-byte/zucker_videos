from copy import deepcopy

from core.shot_continuity import continuous_views


def shot(i, yaw):
    return {
        'source_path': 'camera360.mp4', 'clip_path':'camera360.mp4',
        'master_start_sec': float(i*4), 'clip_start_sec':float(i*4),
        'duration_sec':4.,
        'spherical_shot':{'type':'manual','yaw':yaw,'pitch':0,'fov':95},
    }


def test_editing_one_shot_does_not_change_distant_shots_continuity_or_preview_cache():
    original = [shot(0,20), shot(1,20), shot(2,90), shot(3,45), shot(4,45)]
    baseline = continuous_views(original, preserve_authored=True)
    changed = deepcopy(original)
    changed[2]['manual_view'] = True
    changed[2]['motion_enabled'] = False
    result = continuous_views(changed, preserve_authored=True)
    assert result[:2] == baseline[:2]
    assert result[3:] == baseline[3:]
    assert result[2]['motion_enabled'] is False
    assert result[2]['spherical_shot'] == changed[2]['spherical_shot']
    assert original[2].get('motion_enabled') is None


def test_an_authored_shot_keeps_existing_motion_clock():
    original=[shot(0,10),shot(1,10),shot(2,80)]
    original[1]['manual_view']=True
    original[1]['spherical_shot']['continuity_duration_sec']=30
    original[1]['spherical_shot']['continuity_offset_sec']=10
    result=continuous_views(original,preserve_authored=True)
    assert result[1] == original[1]


def test_movement_checkbox_works_for_fixed_video_without_existing_crop():
    from core.stages.export import _motion_filter
    fixed={'source_path':'fixed.mp4','clip_path':'fixed.mp4', 'motion_enabled':True}
    animated=_motion_filter(fixed,'youtube',5.)
    assert animated and 'zoompan=' in animated
    assert _motion_filter({**fixed,'motion_enabled':False},'youtube',5.) is None
    # Legacy material with no explicit choice stays on its existing policy.
    assert _motion_filter({'source_path':'fixed.mp4'},'youtube',5.) is None


def test_movement_checkbox_respects_an_existing_flat_crop():
    from core.stages.export import _motion_filter
    fixed={'source_path':'fixed.mp4','flat_framing':{'zoom':1.15,'x':.5,'y':.5}}
    assert 'zoompan=' in _motion_filter({**fixed,'motion_enabled':True},'youtube',5.)
    assert _motion_filter({**fixed,'motion_enabled':False},'youtube',5.) is None


def test_stage_review_never_rederives_continuity_for_legacy_group():
    segments=[shot(0,30),shot(1,30),shot(2,30)]
    stage=continuous_views(segments,preserve_authored=True)
    assert stage==segments
    assert all('continuity_duration_sec' not in segment['spherical_shot'] for segment in stage)
    nonstage=continuous_views(segments,preserve_authored=False)
    assert nonstage[0]['spherical_shot']['continuity_duration_sec']==12.0
