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
