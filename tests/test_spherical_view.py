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
