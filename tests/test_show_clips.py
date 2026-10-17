"""A video reference's chosen seconds are downloaded into the project and play on their
words as a clip, with their own sound, while the host pauses."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from core import show_clips, show_proposals, show_scenes
from core.project import create_project
from server.api import create_app

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")


def _video(path: Path, seconds: float = 1.5, size: str = "320x240") -> Path:
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)], check=True)
    return path


def test_only_youtube_or_vimeo_links_over_https():
    assert show_clips.check_link("https://www.youtube.com/watch?v=DB4SkfgvQ98").startswith("https://www.youtube.com/")
    for bad in ("http://www.youtube.com/watch?v=x", "https://example.com/v.mp4", "https://user:pw@youtube.com/x", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            show_clips.check_link(bad)


def test_a_clip_plays_whole_with_its_own_sound(tmp_path):
    from core.show_inserts import AUDIO_RATE, insert_frame, load_insert, splice_audio
    clip = _video(tmp_path / "clip.mp4", 1.0)
    insert = load_insert({"insert": "clip", "at": 2.0, "source": str(clip), "clip_start": 0.0, "clip_length": 1.0}, 160, 90, 30.0, FFMPEG)
    assert insert.frames == 30 and insert.zoom_frames == 0
    first, last = insert_frame(insert, 0), insert_frame(insert, 29)
    assert first.shape == (90, 160, 3) and not np.array_equal(first, last)           # it moves
    assert insert.voice is not None and np.abs(insert.voice).max() > 0.05          # its own sound
    host = np.zeros((AUDIO_RATE * 4, 2), dtype=np.float32)
    sound = splice_audio(host, [insert])
    assert len(sound) == AUDIO_RATE * 5                                             # the host pauses for it
    assert np.abs(sound[AUDIO_RATE * 2 + 100:AUDIO_RATE * 3 - 100]).max() > 0.05


def test_downloading_a_reference_puts_the_clip_on_its_words(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Clips", str(tmp_path / "Clips.zuckervid"))
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 10, "shots": [], "words": []}))
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [], resources=[])
    show_scenes.save(project.artifacts_dir / "show_scenes.json", {"identity": {}, "scenes": [
        {"id": "scene-1", "name": "Scene 1", "text": "x", "start": 0, "end": 10, "effects": [], "overlays": [], "decision": "pending", "proposal": ""}]})
    source = _video(tmp_path / "youtube.mp4", 3.0)
    asked = []

    class FakeYoutubeDL:                       # yt-dlp: only the chosen seconds, into the given template
        def __init__(self, options):
            self.options = options
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def download(self, urls):
            asked.append((urls, self.options["download_ranges"]({"duration": 3.0}, None)))
            target = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
            subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "0.5", "-t", "1.2", "-i", str(source), "-c", "copy", str(target)], check=True)

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    client = create_app(str(project.folder), dev=True).test_client()
    ref = client.post("/api/v1/show/references/video", json={"project_id": str(project.folder), "scene_id": "scene-1", "selected_text": "I don't know",
                                                               "at": 4.2, "url": "https://www.youtube.com/watch?v=DB4SkfgvQ98", "start": 0, "end": 1.2}).get_json()
    made = client.post(f"/api/v1/show/references/video/{ref['id']}/download", json={})
    assert made.status_code == 201, made.get_json()
    result = made.get_json()
    clip = Path(result["proposal"]["params"]["clip"])
    assert clip.parent == project.folder / "assets" / "clips" and clip.is_file()
    assert list(asked[0][1]) == [{"start_time": 0.0, "end_time": 1.2}]            # only those seconds
    assert result["proposal"]["kind"] == "clip" and result["proposal"]["status"] == "approved" and result["proposal"]["at"] == 4.2
    assert result["reference"]["status"] == "downloaded"
    scene = show_scenes.load(project.artifacts_dir / "show_scenes.json")["scenes"][0]
    assert result["proposal"]["id"] in scene["effects"] and scene["decision"] == "modified"
    stored = show_proposals.load(project.artifacts_dir / "show_proposals.json")
    assert any(entry["path"] == str(clip) for entry in stored["resources"])        # its frames can be shown
    _, items, _ = show_proposals.render_inputs(stored["proposals"])
    assert items == [{"proposal": result["proposal"]["id"], "insert": "clip", "at": 4.2, "source": str(clip), "clip_start": 0.0,
                      "clip_length": result["proposal"]["params"]["clip_length"]}]
    again = client.post(f"/api/v1/show/references/video/{ref['id']}/download", json={}).get_json()
    assert again["proposal"]["id"] == result["proposal"]["id"] and len(asked) == 1  # not downloaded or placed twice
    assert client.post("/api/v1/show/references/video/nope/download", json={}).status_code == 404


def test_a_clip_can_play_its_picture_over_the_hosts_voice():
    clip = show_proposals.proposal("clip", 4.0, 1.2, reason="Clip", clip="/x/clip.mp4", clip_start=0.0, clip_length=1.2, clip_audio=False)
    clip["status"] = "approved"
    cutaways, items, _ = show_proposals.render_inputs([clip])
    assert items == [] and cutaways == [{"proposal": clip["id"], "at": 4.0, "duration": 1.2, "source": "/x/clip.mp4", "source_start": 0.0,
                                         "zoom": 1.0, "focus": [0.5, 0.5]}]
