"""A clip's speed (10 %-200 %): slow motion is longer, picture and sound alike."""
import subprocess

import numpy as np

from core import show_proposals
from core.show_inserts import AUDIO_RATE, atempo, decode_voice, load_clip


def _clip(tmp_path):
    source = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-pix_fmt", "yuv420p", "-shortest", str(source)], check=True)
    return source


def test_tempo_filters_cover_ten_to_two_hundred_percent():
    assert atempo(0.1) == "atempo=0.5,atempo=0.5,atempo=0.5,atempo=0.800000"
    assert atempo(2.0) == "atempo=2.000000" and atempo(1.5) == "atempo=1.500000"


def test_half_speed_doubles_the_clip_picture_and_sound(tmp_path):
    source = _clip(tmp_path)
    normal = load_clip({"source": str(source), "at": 0, "clip_length": 1.0}, 160, 90, 10, "ffmpeg")
    slow = load_clip({"source": str(source), "at": 0, "clip_length": 1.0, "speed": 0.5}, 160, 90, 10, "ffmpeg")
    assert len(normal.clip) == 10 and len(slow.clip) == 20
    assert abs(len(slow.voice) / AUDIO_RATE - 2.0) < 0.1 and abs(len(normal.voice) / AUDIO_RATE - 1.0) < 0.1
    # Frame 2 of the slow clip is frame 1 of the normal one (each picture held twice as long).
    assert np.abs(np.array(slow.clip[2], int) - np.array(normal.clip[1], int)).mean() < 8
    fast = decode_voice(source, 0, 2.0, "ffmpeg", 2.0)
    assert abs(len(fast) / AUDIO_RATE - 1.0) < 0.1


def test_the_speed_reaches_every_way_a_clip_plays():
    clip = show_proposals.proposal("clip", 1.0, 2.0, reason="test", clip="c.mp4", clip_start=0, clip_length=2.0, speed=0.5)
    clip["status"] = "approved"
    mixed = show_proposals.proposal("clip", 5.0, 2.0, reason="test", clip="d.mp4", clip_start=0, clip_length=2.0, clip_audio=False, speed=1.5)
    mixed["status"] = "approved"
    cutaways, layer, _ = show_proposals.render_inputs([clip, mixed])
    assert layer[0]["insert"] == "clip" and layer[0]["speed"] == 0.5
    assert cutaways[0]["speed"] == 1.5


def test_changing_the_speed_keeps_the_same_seconds_of_the_clip(tmp_path, monkeypatch):
    import json
    from core.project import create_project
    from server.api import create_app
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Speed", str(tmp_path / "Speed.zuckervid"))
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 30, "shots": [], "words": [], "cuts": 0, "source": "x"}))
    clip = show_proposals.proposal("clip", 1.0, 2.0, reason="test", clip=str(_clip(tmp_path)), clip_start=0, clip_length=2.0)
    word = show_proposals.proposal("word", 5.0, 2.0, reason="test", kind="word", text="WOW")
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [clip, word], resources=[])
    client = create_app(str(project.folder)).test_client()
    slow = client.patch(f"/api/v1/show/review/{clip['id']}", json={"speed": 0.5}).get_json()
    assert slow["params"]["speed"] == 0.5 and slow["duration"] == 4.0          # twice as long in slow motion
    back = client.patch(f"/api/v1/show/review/{clip['id']}", json={"speed": 2.0}).get_json()
    assert back["duration"] == 1.0
    assert client.patch(f"/api/v1/show/review/{clip['id']}", json={"speed": 3}).status_code == 400
    assert client.patch(f"/api/v1/show/review/{word['id']}", json={"speed": 0.5}).status_code == 400
