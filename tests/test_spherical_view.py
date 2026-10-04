from core.spherical_view import paired_flat_fov, view_parameters


def test_preview_and_export_share_normal_landmark_pose_conversion():
    for shot_type, yaw, pitch, fov in (
        ("singer", 336.8, -28.8, 74.8),
        ("audience", 175.0, -12.9, 111.4),
        ("audience_stage_wide", 75.251, -15.051, 110.0),
    ):
        preview = view_parameters(yaw, pitch, fov, 480 / 270, shot_type)
        render = view_parameters(yaw, pitch, fov, 16 / 9, shot_type)
        assert preview["yaw"] == render["yaw"]
        assert preview["pitch"] == render["pitch"]
        assert preview["h_fov"] == render["h_fov"]
        assert preview["v_fov"] == render["v_fov"]
        assert preview["projection"] == render["projection"] == "flat"


def test_short_path_yaw_and_aspect_pairing_are_canonical():
    assert view_parameters(349.0, 0.0, 90.0, 16 / 9, "singer")["yaw"] == -11.0
    assert paired_flat_fov(90.0, 16 / 9)[1] < 90.0



def test_external_reframe_keyframe_round_trips_projection_and_horizon():
    keyframe = {
        "yaw": 171.2,
        "pitch": -36.5,
        "fov": 100.0,
        "roll": 0.0,
        "projection_preset": "DEWARP",
        "projection_control": 0.40,
    }
    preview = view_parameters(
        keyframe["yaw"], keyframe["pitch"], keyframe["fov"], 16 / 9, "singer",
        projection_preset=keyframe["projection_preset"],
        roll=keyframe["roll"],
        projection_control=keyframe["projection_control"],
    )
    render = view_parameters(
        keyframe["yaw"], keyframe["pitch"], keyframe["fov"], 16 / 9, "singer",
        projection_preset=keyframe["projection_preset"],
        roll=keyframe["roll"],
        projection_control=keyframe["projection_control"],
    )
    assert preview == render
    assert preview["projection"] == "flat"
    assert preview["projection_preset"] == "dewarp"
    assert preview["pitch"] == -36.5
    assert preview["h_fov"] == 100.0
    assert preview["projection_control"] == 0.4


def test_dewarp_rejects_extreme_fov_that_would_distort_the_subject():
    view = view_parameters(
        0.0, 0.0, 150.0, 16 / 9, "singer",
        projection_preset="dewarp",
    )
    assert view["projection"] == "flat"
    assert view["h_fov"] == 100.0
