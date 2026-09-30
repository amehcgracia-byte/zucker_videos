from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from captions.burn import burn
from captions.model import Cue, CueTrack
from captions.styles import get_style

from core.project import create_project
from core.stages.edit import (
    _available_spherical_shots,
    _bars_for_segment,
    _flat_camera_motion_enabled,
    _gentle_fixed_camera_motion,
    _source_role,
)
from core.shot_review import _review_candidate_pool, _spherical_review_poses
from core.stages.export import _apply_saved_spherical_landmarks, _motion_filter
from server.api import _compose_visual_overlays, _validate_composition_output


def _video(path: Path, duration: float = 2.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={duration}", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


def test_opaque_flyer_does_not_destroy_base_after_interval(tmp_path: Path) -> None:
    base = tmp_path / "base.mp4"
    output = tmp_path / "composed.mp4"
    flyer = tmp_path / "flyer.png"
    _video(base)
    image = Image.new("RGBA", (640, 360), (0, 0, 0, 255))
    ImageDraw.Draw(image).text((250, 170), "FLYER", fill=(255, 255, 255, 255))
    image.save(flyer)
    _compose_visual_overlays(
        base,
        {"images": [{"path": str(flyer), "start_sec": 0, "duration_sec": 0.5, "x": 0.5, "y": 0.5, "width": 1.0}]},
        output,
        lambda _value: None,
    )
    validation = _validate_composition_output(base, output, {"images": []})
    assert validation["ok"], validation
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=duration", "-of", "json", str(output)], check=True, capture_output=True, text=True)
    durations = [float(stream["duration"]) for stream in json.loads(probe.stdout)["streams"] if stream.get("duration")]
    assert max(durations) >= 1.9
    frame = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "1.5", "-i", str(output), "-frames:v", "1", "-vf", "scale=1:1,format=gray", "-f", "rawvideo", "-"], check=True, capture_output=True)
    assert frame.stdout and max(frame.stdout) > 1


def test_saved_360_profile_roundtrip_uses_original_source_over_proxy(tmp_path: Path) -> None:
    project = create_project("360 roundtrip", str(tmp_path / "project.zuckervid"))
    source = tmp_path / "BATTERY.mp4"
    proxy = tmp_path / "proxy.mp4"
    project.data["settings"]["spherical_landmarks_by_source"] = {
        str(source): {"singer": {"yaw": 123.0, "pitch": -12.0, "fov": 71.0, "weight": 1.0}}
    }
    segments = [{
        "source_path": str(source),
        "spherical_source_path": str(proxy),
        "spherical_shot": {"shot_id": "singer", "yaw": 0.0, "pitch": 0.0, "fov": 95.0},
    }]
    result = _apply_saved_spherical_landmarks(project, segments)
    assert result[0]["spherical_shot"] == {
        "shot_id": "singer", "yaw": 123.0, "pitch": -12.0, "fov": 82.0,
        "type": "singer", "label": "Cantante", "weight": 1.0,
    }




def test_incomplete_360_profile_restores_authored_landmarks() -> None:
    landmarks = {
        "full_stage": {"yaw": 10.0, "pitch": 0.0, "fov": 110.0, "weight": 0.0},
        "singer": {"yaw": 120.0, "pitch": 0.0, "fov": 95.0, "weight": 0.0},
        "drummer": {"yaw": 240.0, "pitch": 0.0, "fov": 95.0, "weight": 5.0},
    }
    shots = _available_spherical_shots(landmarks)
    assert {shot["type"] for shot in shots} == {"full_stage", "singer", "drummer"}


def test_360_review_pool_has_twenty_reserve_views_across_proxy_aliases(tmp_path: Path) -> None:
    source = tmp_path / "camera-360.mp4"
    proxy = tmp_path / "proxy-camera-360.mp4"
    segment = {
        "clip_path": str(proxy),
        "source_path": str(source),
        "camera_id": "360-camera",
        "master_start_sec": 2.0,
        "duration_sec": 3.0,
        "spherical_shot": {"type": "drummer", "shot_id": "drummer", "label": "Bateria", "yaw": 240.0, "pitch": 0.0, "fov": 95.0},
    }
    coverage = {
        "platform": "youtube",
        "sources": [{"path": str(proxy), "source_path": str(source), "camera_id": "360-camera", "offset_sec": 0.0, "duration_sec": 20.0}],
    }
    poses = _spherical_review_poses(segment, [segment])
    pool, _origin = _review_candidate_pool(coverage, [segment], segment, "youtube")
    assert len(poses) >= 20
    assert len(pool) >= 20
    assert len({round(float(item["spherical_shot"]["yaw"]), 3) for item in pool}) >= 20



def test_edit_cadence_uses_long_holds_until_music_is_very_intense() -> None:
    bars = [float(index) for index in range(20)]
    assert _bars_for_segment(0, bars, [], 0, [0.15]) == 7
    assert _bars_for_segment(0, bars, [], 0, [0.80]) == 4
    assert _bars_for_segment(0, bars, [], 0, [0.90]) == 3
    assert _bars_for_segment(0, bars, [], 0, [0.99]) == 2



def test_360_review_reserve_stays_near_authored_landmark() -> None:
    segment = {
        "clip_path": "/tmp/camera-360.mp4",
        "source_path": "/tmp/camera-360.mp4",
        "camera_id": "360-camera",
        "spherical_shot": {"type": "drummer", "shot_id": "drummer", "yaw": 240.0, "pitch": 0.0, "fov": 95.0},
    }
    poses = _spherical_review_poses(segment, [segment])
    assert len(poses) >= 20
    for pose in poses:
        yaw = float(pose["yaw"])
        distance = abs((yaw - 240.0 + 180.0) % 360.0 - 180.0)
        assert distance <= 40.001


def test_phone_metadata_wins_over_inferred_projection() -> None:
    assert _source_role({"camera_type": "iphone", "projection": "equirect"}) == "fixed_rear"
    assert _source_role({"raw_360": True, "camera_type": "iphone"}) == "360"


def test_flat_phone_sources_are_motion_eligible() -> None:
    assert _flat_camera_motion_enabled({"camera_type": "iphone"})
    assert _flat_camera_motion_enabled({"filename": "IMG_4098.MOV"})
    assert not _flat_camera_motion_enabled({"projection": "equirect"})


def test_gentle_fixed_camera_motion_reaches_endpoint() -> None:
    motion = _gentle_fixed_camera_motion(0)
    rendered = _motion_filter({"motion": motion}, "youtube", 5.5)
    assert motion["speed_factor"] == 1.0
    assert motion["zoom_start"] != motion["zoom_end"]
    assert rendered and "eval=frame" in rendered



def test_caption_burn_reports_process_lifecycle(tmp_path: Path) -> None:
    source = tmp_path / "caption-source.mp4"
    output = tmp_path / "caption-output.mp4"
    _video(source)
    events: list[str] = []
    burn(
        source,
        CueTrack((Cue(("TEST CAPTION",), 0.0, 1.0),)),
        get_style("clean_bottom"),
        output_path=output,
        progress_callback=lambda _seconds: None,
        process_callback=lambda process: events.append("started" if process is not None else "finished"),
    )
    assert output.is_file()
    assert events == ["started", "finished"]



def test_malformed_composition_is_rejected_without_raising(tmp_path: Path) -> None:
    base = tmp_path / "valid-base.mp4"
    corrupt = tmp_path / "corrupt.mp4"
    _video(base)
    corrupt.write_bytes(b"not an mp4")
    validation = _validate_composition_output(base, corrupt, {"images": [], "videos": []})
    assert validation["ok"] is False
    assert "probe_failed" in validation["reason"]



def test_youtube_longform_policy_has_no_caption_or_flyer_pass() -> None:
    # This is an architectural contract test marker: the API must gate both
    # saved overlay specs and caption burning when the render platform is YouTube.
    api = Path("server/api.py").read_text(encoding="utf-8")
    assert "youtube_longform = render_platform in" in api
    assert "needs_caption_pass = (not youtube_longform)" in api
    app = Path("web/app.js").read_text(encoding="utf-8")
    assert "function youtubeSkipsComposition" in app
    assert "youtubeSkipsComposition(latestResult.platform || status.platform)" in app


def test_normal_spherical_views_never_auto_switch_to_stereographic():
    from core.spherical_view import view_parameters
    assert view_parameters(0, 0, 220, 16 / 9, "full_stage")["projection"] == "flat"
    assert view_parameters(0, 0, 220, 16 / 9, "planet")["projection"] == "sg"


def test_gentle_fixed_camera_motion_applies_subject_target():
    from core.stages.edit import _gentle_fixed_camera_motion
    motion = _gentle_fixed_camera_motion(0, 0.25, 0.50)
    assert motion["zoom_end"] > motion["zoom_start"]
    assert motion["pan_x_end"] < 0.5


def test_transition_defaults_are_off_but_explicit_choices_remain_supported():
    from core.stages.export import TRANSITION_PROFILES, _transition_boundaries, _transition_types_for_boundaries
    segments = [{}, {}, {}]
    profile = TRANSITION_PROFILES["youtube"]
    assert profile["type"] == "none"
    assert _transition_boundaries(segments, profile) == []
    segments[0]["transition_type"] = "auto"
    assert _transition_boundaries(segments, profile) == []
    segments[0]["transition_type"] = "crossfade"
    boundaries = _transition_boundaries(segments, profile)
    assert boundaries == [0]
    assert _transition_types_for_boundaries(segments, boundaries, profile) == ["crossfade"]

def test_transition_api_accepts_native_auto_mode():
    from server.api import TRANSITION_LIBRARY

    allowed = {"auto", "none"} | set(TRANSITION_LIBRARY)
    assert "auto" in allowed
    assert "crossfade" in allowed


def test_preview_and_export_share_canonical_360_pose():
    from core.spherical_view import effective_pitch, view_parameters

    authored = view_parameters(123.0, 80.0, 20.0, 16 / 9, "drummer")
    exported = view_parameters(123.0, effective_pitch(80.0, "drummer"), 20.0, 16 / 9, "drummer")
    assert authored == exported
    assert authored["pitch"] == 25.0
    assert authored["h_fov"] == 82.0


def test_safe_360_framing_limits_zoom_and_pitch():
    from core.spherical_view import NORMAL_FOV_MIN, view_parameters
    from core.stages.edit import _available_spherical_shots

    shots = _available_spherical_shots({
        "singer": {"yaw": 120.0, "pitch": 60.0, "fov": 20.0, "weight": 1.0},
    })
    assert shots[0]["pitch"] == 18.0
    assert shots[0]["fov"] >= NORMAL_FOV_MIN
    assert view_parameters(0, 0, 20, 16 / 9, "singer")["h_fov"] >= NORMAL_FOV_MIN


def test_nikon_is_the_preferred_color_camera_kind():
    from core.stages.export import _color_camera_kind, _color_reference_record

    nikon = {"filename": "NIKON_D850_001.MOV"}
    iphone = {"filename": "IMG_4098.MOV"}
    assert _color_camera_kind(nikon) == "nikon"
    assert _color_reference_record([iphone, nikon]) is nikon
