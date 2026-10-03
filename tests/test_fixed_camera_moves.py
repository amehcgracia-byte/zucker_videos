import pytest
from core.fixed_camera_moves import CATALOG, fixed_camera_motion
from core.stages.edit import _valid_motion_recipe
from core.stages.export import _ken_burns_filter


def test_stable_seed_and_no_adjacent_repeat():
    previous = ""
    sequence = []
    for index in range(120):
        move = fixed_camera_motion(index, .68, .41, seed="project", previous=previous)
        assert move == fixed_camera_motion(index, .68, .41, seed="project", previous=previous)
        assert move["movement"] != previous
        assert _valid_motion_recipe(move)
        sequence.append(move["movement"])
        previous = move["movement"]
    assert set(sequence) == set(CATALOG)


@pytest.mark.parametrize("kind", CATALOG)
@pytest.mark.parametrize("target", [(.25, .35), (.5, .5), (.75, .65)])
def test_subject_stays_inside_safe_frame_for_whole_move(kind, target):
    move = fixed_camera_motion(9, *target, duration=7, preferred=kind)
    assert _valid_motion_recipe(move)
    for frame in range(101):
        t = frame/100
        z = move["zoom_start"]+(move["zoom_end"]-move["zoom_start"])*t
        assert 1 <= z <= 1.38
        for axis, anchor in zip(("x", "y"), target):
            pan = move[f"pan_{axis}_start"]+(move[f"pan_{axis}_end"]-move[f"pan_{axis}_start"])*t
            if move["lock_target"]:
                pan = min(1, max(0, (z*anchor-.5)/(z-1))) if z > 1.001 else .5
            position = z*anchor-(z-1)*pan
            assert .10 <= position <= .90


def test_unknown_subject_has_no_directional_pan():
    for index in range(100):
        move = fixed_camera_motion(index, .95, .02, confidence=0)
        assert move["subject_fallback"]
        assert move["target_x"] == move["target_y"] == .5
        assert move["movement"] in {"full_static", "zoom_in_very_slow", "zoom_out_very_slow"}
        assert max(move["zoom_start"], move["zoom_end"]) <= 1.04


def test_short_shot_limits_travel_and_renderer_respects_vertical_path():
    short = fixed_camera_motion(0, duration=2, preferred="diagonal_up_right")
    long = fixed_camera_motion(0, duration=7, preferred="diagonal_up_right")
    assert abs(short["pan_y_end"]-short["pan_y_start"]) < abs(long["pan_y_end"]-long["pan_y_start"])
    graph = _ken_burns_filter(short, "youtube", 2)
    assert '0.800000' not in graph


@pytest.mark.parametrize('tempo,intensity', [(70, 'low'), (110, 'medium'), (175, 'high')])
def test_pacing_never_leaves_a_one_second_tail(tempo, intensity):
    from core.stages.edit import _youtube_multicam_plan
    coverage = {'platform': 'youtube', 'window': {'start_sec': 0, 'duration_sec': 23.1},
                'sources': [{'path': '/tmp/camera.mp4', 'duration_sec': 23.1}]}
    plan = _youtube_multicam_plan(coverage, {'bars_sec': [0, 4, 8, 12, 16, 20, 23.1], 'tempo': tempo, 'intensity': intensity})
    assert all(2 <= s['duration_sec'] <= 7 for s in plan['segments'])
    assert sum(s['duration_sec'] for s in plan['segments']) == pytest.approx(23.1)


def test_old_default_transition_settings_do_not_enable_hidden_crossfades(tmp_path):
    from core.project import create_project
    from core.stages.export import _transition_profile
    project = create_project('Cuts', str(tmp_path/'cuts.zuckervid'))
    for platform in ('youtube', 'reel', '360'):
        assert _transition_profile(project, platform)['duration'] == 0
    project.data['settings']['export']['transitions']['youtube'] = {'duration': .12}
    assert _transition_profile(project, 'youtube')['duration'] == 0
    project.data['settings']['export']['transitions']['youtube']['enabled'] = True
    assert _transition_profile(project, 'youtube')['duration'] == .12


@pytest.mark.parametrize('value', [float('nan'), float('inf'), 'broken'])
def test_malformed_cached_recipe_is_rejected(value):
    move = fixed_camera_motion(0, preferred='zoom_in')
    move['zoom_start'] = value
    assert _ken_burns_filter(move, 'youtube', 4) is None


def test_spherical_and_flat_vid_files_are_distinct_physical_cameras():
    from core.stages.edit import _camera_id
    flat = {'source_path': '/tmp/Edited/VID_123.mp4'}
    spherical = {'source_path': '/tmp/Edited/VID_456.mp4', 'projection': 'equirect'}
    assert _camera_id(flat) != _camera_id(spherical)


def test_legacy_phone_roles_receive_motion_without_changing_quotas():
    from core.stages.edit import _youtube_multicam_plan
    coverage = {'platform':'youtube', 'window':{'start_sec':0, 'duration_sec':20},
                'sources':[{'path':'/tmp/Edited/IMG_0043.MOV', 'filename':'IMG_0043.MOV',
                            'camera_role':'handheld', 'duration_sec':20}]}
    plan = _youtube_multicam_plan(coverage, {'bars_sec':[0,4,8,12,16,20]})
    assert all(segment['motion']['subject_fallback'] for segment in plan['segments'])
    assert plan['camera_distribution'][0]['role'] == 'handheld'
    moves = [segment['motion']['movement'] for segment in plan['segments']]
    assert all(a != b for a,b in zip(moves,moves[1:]))
