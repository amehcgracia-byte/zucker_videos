import pytest

from core.music_direction import direction_at, energy_timeline
from core.stages.edit import _youtube_multicam_plan, _spherical_motion_profile
from core.fixed_camera_moves import fixed_camera_motion
from core.spherical_motion import motion_pose
from core.stages.export import _ken_burns_filter


def test_music_boundaries_and_repeated_run_variation_keep_full_coverage():
    beats = {"bars_sec": list(range(0, 121, 2)), "tempo": 120,
             "energy_by_bar": [.1]*20 + [.7]*20 + [1.]*20}
    sources = [{"path": f"/tmp/camera-{i}.mp4", "camera_id": str(i),
                "duration_sec": 120, "role": "fixed_rear"} for i in range(3)]
    coverage = {"platform": "youtube", "window": {"start_sec": 0, "duration_sec": 120}, "sources": sources}
    def plan(seed):
        return _youtube_multicam_plan(coverage, beats, {"wizard": {"variation_seed": seed}})["segments"]
    a, b = plan("first"), plan("second")
    assert a == plan("first")
    assert [(s["master_start_sec"],s["clip_path"]) for s in a] != [(s["master_start_sec"],s["clip_path"]) for s in b]
    for segments in (a, b):
        assert sum(s["duration_sec"] for s in segments) == pytest.approx(120)
        assert segments[0]["master_start_sec"] == 0
        assert segments[-1]["master_start_sec"]+segments[-1]["duration_sec"] == pytest.approx(120)
        assert {s["music_direction"]["group"] for s in segments} == {"tranquilo","animado","frenetico"}
        quiet = [s["duration_sec"] for s in segments if s["master_start_sec"] < 34]
        peak = [s["duration_sec"] for s in segments if 88 < s["master_start_sec"] < 114]
        assert min(quiet) >= 5 and max(peak) <= 2
        assert all(s["duration_sec"] >= 1 for s in segments)


def test_energy_timeline_is_not_shifted_by_inserted_cuts():
    timeline = energy_timeline({"bars_sec": [10,14,18,22], "energy_by_bar": [.1,.1,1.]})
    assert direction_at(timeline, 12, "run", 20)["group"] == "tranquilo"
    assert direction_at(timeline, 19, "run", 21)["group"] == "frenetico"


def test_expressive_moves_preserve_safe_zoom_and_render_acceleration():
    for style in ("tranquilo", "animado", "frenetico"):
        move = fixed_camera_motion(2, duration=2, seed="run", preferred="zoom_in", style=style)
        assert max(move["zoom_start"],move["zoom_end"]) <= 1.38
        if style != "tranquilo":
            assert "pow(" in _ken_burns_filter(move, "youtube", 2)
    quiet = fixed_camera_motion(2, duration=2, seed="run", preferred="zoom_in")
    active = fixed_camera_motion(2, duration=2, seed="run", preferred="zoom_in", style="animado")
    assert active["zoom_end"]-active["zoom_start"] > quiet["zoom_end"]-quiet["zoom_start"]
    unknown = fixed_camera_motion(2, confidence=0, style="frenetico")
    assert unknown["subject_fallback"] and unknown["easing"] == "smooth"


def test_native_spherical_motion_can_move_in_short_peak_cut():
    shot = _spherical_motion_profile({"yaw":200,"pitch":0,"fov":70}, 0, True, style="frenetico")
    shot["movement"] = "push_in"
    assert motion_pose(shot,1.5,0) != motion_pose(shot,1.5,1.4)
    assert motion_pose(shot,1.5,1.4)[2] >= 55
