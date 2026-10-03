import math
import numpy as np
from core.spherical_motion import motion_pose, reproject_maps
from core.spherical_view import view_parameters
from core.stages.edit import _available_spherical_shots, _youtube_multicam_plan


def test_wide_projection_keeps_edge_scale_finite_and_aspect_paired():
    wide = view_parameters(190, -15, 160, 16/9, 'full_stage', projection_preset='megaview')
    assert wide['projection'] == 'sg'
    assert math.isclose(math.tan(math.radians(wide['h_fov'])/4) / math.tan(math.radians(wide['v_fov'])/4), 16/9)
    assert view_parameters(0, 0, 160, 16/9, projection_preset='linear')['h_fov'] == 110


def test_zoom_reverses_smoothly_and_respects_authored_centre():
    shot = dict(yaw=185, pitch=-5, fov=65, runtime_motion_enabled=True)
    for movement in ('push_in', 'pull_out'):
        poses = [motion_pose(dict(shot, movement=movement), 4, i/30) for i in range(120)]
        assert all(pose[:2] == (185, -5) for pose in poses)
        differences = np.diff([pose[2] for pose in poses])
        assert np.max(np.abs(differences)) < .14
        assert np.all(differences <= 0) if movement == 'push_in' else np.all(differences >= 0)
    assert motion_pose(dict(shot, runtime_motion_enabled=False), 4, 3) == (185, -5, 65)


def test_longitude_seam_interpolation_does_not_blend_opposite_views():
    shot = dict(yaw=180, pitch=0, fov=90, projection_preset='linear')
    coarse = reproject_maps((3840,1920), (640,360), shot)
    exact = reproject_maps((3840,1920), (640,360), shot, exact=True)
    longitude_error = np.abs((coarse[0] - exact[0] + 1920) % 3840 - 1920)
    assert np.quantile(longitude_error, .99) < .5
    assert np.quantile(np.abs(coarse[1] - exact[1]), .99) < .5


def test_automatic_plan_restores_zero_weight_performers_and_opens_on_singer():
    landmarks = {kind: dict(yaw=yaw, weight=0, subject=kind, fov=65)
                 for kind,yaw in [('singer',185),('pianist',245),('guitarist',165),('bassist',220)]}
    # The known landmark keys are left/right, with explicit musician identities.
    landmarks['left'] = landmarks.pop('guitarist'); landmarks['right'] = landmarks.pop('bassist')
    landmarks.update(drummer=dict(yaw=125,weight=5),full_stage=dict(yaw=190,weight=1))
    available = _available_spherical_shots(landmarks, balanced_performers=True)
    assert {'singer','pianist','left','right'} <= {shot['type'] for shot in available}
    source = dict(path='/tmp/sphere.mp4', source_path='/tmp/sphere.mp4', camera_role='360', projection='equirect', duration_sec=90)
    plan = _youtube_multicam_plan(dict(platform='youtube',window=dict(duration_sec=90),sources=[source]),
        dict(bars_sec=list(range(0,91,3))), dict(spherical_landmarks=landmarks,
            edit=dict(spherical_motion=True,spherical_hold_motion='subtle')))
    segments = plan['segments']
    assert segments[0]['editorial_subject'] == 'singer'
    subjects = [segment['editorial_subject'] for segment in segments]
    for index,subject in enumerate(subjects): assert subject not in subjects[max(0,index-4):index]
    assert len({segment['spherical_shot'].get('movement') for segment in segments}) == 8


def test_review_uses_original_source_profile_instead_of_proxy_fallback(tmp_path):
    from core.project import Project
    from core.shot_review import _review_segment, _review_signature
    profile = dict(singer=dict(yaw=165,pitch=-10,fov=60))
    project = Project(tmp_path, dict(settings=dict(spherical_landmarks_by_source={"/original/sphere.mp4": profile},
                                                 spherical_landmarks=dict(singer=dict(yaw=113,pitch=0,fov=82)))))
    segment = dict(source_path="/original/sphere.mp4", spherical_source_path="/cache/sphere.mp4",
                   spherical_shot=dict(type="singer", yaw=113,pitch=0,fov=82))
    first = _review_segment(project, segment)
    assert first["spherical_shot"]["yaw"] == 165
    profile["singer"]["yaw"] = 190
    second = _review_segment(project, segment)
    assert second["spherical_shot"]["yaw"] == 190
    assert _review_signature([first]) != _review_signature([second])


def test_explicitly_disabled_and_audience_views_stay_out_of_automatic_rotation():
    landmarks = dict(singer=dict(yaw=165,weight=1),drummer=dict(yaw=125,weight=5),
                     pianist=dict(yaw=245,weight=0,enabled=False),right=dict(yaw=220,subject="audience",weight=1))
    shots = _available_spherical_shots(landmarks, balanced_performers=True)
    assert not {"pianist","right"} & {shot["type"] for shot in shots}
    from server.api import _sanitize_spherical_landmarks
    saved = _sanitize_spherical_landmarks(landmarks)
    assert saved["pianist"]["enabled"] is False
    assert saved["right"]["subject"] == "audience"


def test_save_angles_preserves_authored_subject_and_explicit_exclusion(tmp_path, monkeypatch):
    from core.project import create_project
    from server.api import create_app
    project = create_project('Angles', str(tmp_path / 'Angles.zuckervid'))
    app = create_app(str(project.folder), dev=True)
    monkeypatch.setattr('server.api.load_global_config', lambda: {})
    monkeypatch.setattr('server.api.save_global_config', lambda config: None)
    client = app.test_client()
    first = client.post('/api/v1/settings/spherical-landmarks', json=dict(
        spherical_source_path='/original/sphere.mp4',
        spherical_landmarks=dict(left=dict(yaw=185,subject='left',enabled=False))))
    assert first.status_code == 200
    saved = first.get_json()['spherical_landmarks']['left']
    assert saved['subject'] == 'left' and saved['enabled'] is False
    second = client.post('/api/v1/settings/spherical-landmarks', json=dict(
        spherical_source_path='/original/sphere.mp4',spherical_landmarks=dict(left=dict(yaw=190))))
    assert second.status_code == 200
    saved = second.get_json()['spherical_landmarks']['left']
    assert saved['subject'] == 'left' and saved['enabled'] is False


def test_export_overlays_saved_wide_projection_and_horizon_without_flat_clamping(tmp_path):
    from core.project import Project
    from core.stages.export import _apply_saved_spherical_landmarks, _export_source_filter
    profile = dict(full_stage=dict(yaw=190,pitch=-15,fov=160,roll=11,projection_preset='megaview'))
    project = Project(tmp_path,dict(settings=dict(spherical_landmarks_by_source={'/original/sphere.mp4':profile})))
    stale = dict(source_path='/original/sphere.mp4',spherical_source_path='/cache/sphere.mp4',
                 spherical_shot=dict(type='full_stage',yaw=100,pitch=0,fov=110,roll=0,projection_preset='linear'))
    corrected = _apply_saved_spherical_landmarks(project,[stale])[0]['spherical_shot']
    assert corrected['fov'] == 160 and corrected['roll'] == 11
    assert corrected['projection_preset'] == 'megaview'
    graph = _export_source_filter(dict(projection='equirect'),corrected,duration=4)
    assert 'output=sg' in graph and 'h_fov=160.000' in graph and 'roll=11.000' in graph
