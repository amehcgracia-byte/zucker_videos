from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from core.project import create_project
from core.stages.export import _apply_saved_spherical_landmarks
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
        "shot_id": "singer", "yaw": 123.0, "pitch": -12.0, "fov": 71.0,
        "type": "singer", "label": "Cantante", "weight": 1.0,
    }

