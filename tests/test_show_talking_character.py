"""A character's eyes, mouth and voice together: the render moves both, the studio previews
them with the voice heard (the same drawing code), and a recording's silences can be cut."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core import show_proposals, show_scenes
from core.project import create_project
from server.api import create_app

FFMPEG = shutil.which("ffmpeg")


def test_eyes_and_mouth_move_together():
    from core.gag_render import Sticker, talking_pixels
    from core.show_pixel_layers import LookingPixels
    pixels = np.zeros((100, 100, 4), dtype=np.float32)
    pixels[..., 3] = 1.0
    pixels[20:30, 30:40, :3] = 1.0                                   # an eye
    pixels[60:80, 40:60, 0] = 1.0                                    # a red mouth
    sticker = Sticker(pixels=pixels, at=0.0, duration=2.0, x=0.5, y=0.5, width_px=100, effect="none", from_box=None, path=[], hops=0)
    sticker.eyes = LookingPixels(pixels, [{"rect": [0.3, 0.2, 0.1, 0.1]}], 0.3)
    mouth = np.zeros((100, 100), dtype=bool)
    mouth[60:80, 40:60] = True
    sticker.mouth, sticker.mouth_drop, sticker.fps = mouth, 8.0, 25.0
    sticker.envelope = np.ones(50, dtype=np.float32)
    frame = talking_pixels(sticker, 0.6)                             # mid glance, mouth open
    eyes_only = sticker.eyes.frame(0.3)
    assert not np.array_equal(frame[20:30], pixels[20:30])            # the eye moved…
    assert not np.array_equal(frame[60:90], eyes_only[60:90])         # …and so did the mouth


def test_a_talking_character_glances_too(tmp_path):
    folder = tmp_path / "Characters" / "Me"
    folder.mkdir(parents=True)
    Image.new("RGBA", (40, 60), (200, 100, 50, 255)).save(folder / "cutout.png")
    (folder / "character.json").write_text(json.dumps({"name": "Me", "mouth": [0.4, 0.6, 0.2, 0.1], "eyes": [{"rect": [0.3, 0.2, 0.1, 0.1]}],
                                                       "pivot": [0.5, 1], "voice": None}))
    overlay = {"id": "a", "asset": str(folder / "cutout.png"), "x": 0, "y": 0, "width": 30, "at": 4, "duration": 3, "action": "talk"}
    items = show_scenes.overlay_items({"scenes": [{"id": "s", "start": 0, "end": 10, "decision": "accepted", "overlays": [overlay]}]})
    assert items[0]["eyes"] and items[0]["mouth"]


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
def test_silence_is_trimmed_and_the_preview_has_the_voice(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Talk", str(tmp_path / "Talk.zuckervid"))
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 10, "shots": [], "words": []}))
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [], resources=[])
    voices = project.folder / "assets" / "show-voices"
    voices.mkdir(parents=True)
    wav = voices / "take.wav"   # 1.5 s silence, 1 s tone, 1.5 s silence
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1.5", "-f", "lavfi", "-i", "sine=frequency=300:duration=1:sample_rate=48000",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1.5", "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", str(wav)], check=True)
    (voices / "voices.json").write_text(json.dumps([{"source": str(wav), "start": 0, "length": 4.0, "text": "Voice-over"}]))
    photos = project.folder / "photos"
    photos.mkdir()
    Image.new("RGB", (80, 120), (180, 120, 90)).save(photos / "me.png")
    (photos / "photos.json").write_text(json.dumps([{"name": "Me", "photo": str(photos / "me.png")}]))
    masks = project.cache_dir / "show_cutouts"
    masks.mkdir(parents=True, exist_ok=True)
    mask = np.zeros((120, 80), dtype=np.float32)
    mask[10:110, 10:70] = 1
    np.save(masks / "abc123.npy", mask)
    client = create_app(str(project.folder), dev=True).test_client()

    trimmed = client.post("/api/v1/show/voices/trim", json={"source": str(wav), "start": 0, "length": 4.0}).get_json()
    assert 1.2 <= trimmed["start"] <= 1.5 and 1.0 <= trimmed["length"] <= 1.5

    made = client.post("/api/v1/show/characters/talk-preview", json={"photo": str(photos / "me.png"), "mask": "abc123", "eyes": [{"rect": [0.3, 0.2, 0.2, 0.1]}],
                                                                    "mouth": {"rect": [0.35, 0.7, 0.3, 0.1]}, "voice": [str(wav), trimmed["start"], trimmed["length"]]})
    assert made.status_code == 200, made.get_json()
    from urllib.parse import unquote
    video = Path(unquote(made.get_json()["url"].split("path=", 1)[1]))
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(video)], capture_output=True, text=True, check=True).stdout)
    assert {stream["codec_type"] for stream in probe["streams"]} == {"video", "audio"}   # the voice is heard
    volume = subprocess.run([FFMPEG, "-nostats", "-i", str(video), "-af", "volumedetect", "-vn", "-f", "null", "-"], capture_output=True, text=True).stderr
    assert "max_volume: -91" not in volume
    assert client.post("/api/v1/show/characters/talk-preview", json={"photo": str(photos / "me.png"), "mask": "abc123", "voice": None}).status_code == 400
