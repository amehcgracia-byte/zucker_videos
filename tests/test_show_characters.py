"""Characters from photos: cut out with their own pixels, rigged once, reused; talking and moving."""
import subprocess
from pathlib import Path
import sys

import numpy as np
import pytest
from PIL import Image

from core import show_characters
from core.ffmpeg import ffprobe, tool_status
from core.gag_render import load_sticker, render_gags, talking_pixels

W, H, FPS = 320, 180, 30


def _ffmpeg() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    return ffmpeg


def _photo(path):
    """A yellow figure with a face on a dark green background."""
    image = Image.new("RGB", (400, 300), (20, 60, 30))
    pixels = image.load()
    for x in range(150, 250):
        for y in range(60, 260):
            pixels[x, y] = (230, 200, 40) if (x + y) % 7 else (200, 170, 20)      # textured: its own pixels matter
    for x in range(185, 215):
        for y in range(150, 162):
            pixels[x, y] = (120, 30, 30)                                             # the mouth
    image.save(path)


def test_a_cut_out_keeps_the_photo_s_own_pixels_and_the_brush_fixes_its_edge(tmp_path):
    _photo(tmp_path / "toy.png")
    mask, method = show_characters.auto_mask(tmp_path / "toy.png", box=[0.3, 0.15, 0.4, 0.75])
    assert method == "opencv-grabcut"
    assert mask[160, 200] > 0.5 and mask[20, 20] < 0.5
    erased = show_characters.apply_strokes(mask, [{"mode": "erase", "points": [[0.5, 0.3]], "radius": 0.03}])
    assert erased[90, 200] == 0 and erased[160, 200] > 0.5
    kept = show_characters.apply_strokes(erased, [{"mode": "keep", "points": [[0.05, 0.05]], "radius": 0.02}])
    assert kept[15, 20] == 1.0
    picture, box = show_characters.cutout(tmp_path / "toy.png", mask)
    original = np.asarray(Image.open(tmp_path / "toy.png").convert("RGB"))
    inside = np.asarray(picture)
    ys, xs = np.nonzero(inside[..., 3] == 255)
    assert np.array_equal(inside[ys, xs, :3], original[ys + box[1], xs + box[0]])     # untouched pixels
    with pytest.raises(ValueError):
        show_characters.cutout(tmp_path / "toy.png", np.zeros_like(mask))


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS subject lifting")
def test_macos_subject_lifting_answers_with_a_mask_of_the_photo_size_or_nothing(tmp_path):
    _photo(tmp_path / "toy.png")
    mask = show_characters.vision_mask(tmp_path / "toy.png")
    assert mask is None or mask.shape == (300, 400)


def test_a_character_is_rigged_once_and_found_in_the_library(tmp_path):
    _photo(tmp_path / "toy.png")
    mask, method = show_characters.auto_mask(tmp_path / "toy.png", box=[0.3, 0.15, 0.4, 0.75])
    folder = show_characters.save_character("Yellow toy", tmp_path / "toy.png", mask, method=method, root=tmp_path / "Characters",
                                            provenance={"video": "C0272.MP4", "second": 78.0})
    record = show_characters.rig(folder, mouth={"rect": [0.3, 0.42, 0.4, 0.1]}, pivot=[0.5, 1.0], voice=["C0274.MP4", 268.9, 6.0])
    assert record["mouth"]["rect"][1] == 0.42 and record["voice"][1] == 268.9
    found = show_characters.list_characters(tmp_path / "Characters")
    assert [item["name"] for item in found] == ["Yellow toy"] and found[0]["provenance"]["second"] == 78.0
    assert Image.open(found[0]["cutout"]).mode == "RGBA"


def test_a_cut_out_mouth_drops_with_its_voice_inside_the_cut_out_only(tmp_path):
    image = Image.new("RGBA", (100, 200), (0, 0, 0, 0))
    for x in range(10, 90):
        for y in range(10, 190):
            image.putpixel((x, y), (230, 200, 40, 255))
    image.save(tmp_path / "toy.png")
    sticker = load_sticker({"image": str(tmp_path / "toy.png"), "at": 0.0, "duration": 1.0, "width": 0.3,
                            "mouth": {"rect": [0.2, 0.5, 0.6, 0.1]}, "mouth_open": 0.1}, 1000, 1000)
    sticker.envelope = np.array([0.0] * 15 + [1.0] * 15, dtype=np.float32)
    quiet, loud = talking_pixels(sticker, 0.1), talking_pixels(sticker, 0.8)
    assert np.array_equal(quiet, sticker.pixels)                                 # silent: as it is
    h = sticker.pixels.shape[0]
    top = int(0.5 * h) + 2
    assert loud[top, int(0.5 * loud.shape[1]), :3].max() == 0 and loud[top, int(0.5 * loud.shape[1]), 3] > 0.9   # black, still opaque
    assert np.array_equal(loud[..., 3] > 0, sticker.pixels[..., 3] > 0)        # nothing appears outside the cut-out


def test_a_character_talks_over_the_frozen_host_with_its_own_voice(tmp_path):
    ffmpeg = _ffmpeg()
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(tmp_path / "host.mp4")], check=True)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*1000*t)':s=48000:d=1",
                    str(tmp_path / "voice.wav")], check=True)
    Image.new("RGBA", (60, 120), (250, 250, 0, 255)).save(tmp_path / "toy.png")
    insert = {"insert": "character", "at": 1.0, "source": "host", "zoom": 1.0, "zoom_sec": 0.2, "voice": [str(tmp_path / "voice.wav"), 0.0, 1.0],
              "overlays": [{"image": str(tmp_path / "toy.png"), "x": 0.8, "y": 0.6, "width": 0.15, "effect": "none",
                            "mouth": {"rect": [0.2, 0.5, 0.6, 0.12]}, "mouth_open": 0.1}]}
    render_gags(tmp_path / "host.mp4", [insert], tmp_path / "out.mp4", ffmpeg=ffmpeg)
    assert float(ffprobe(str(tmp_path / "out.mp4"))["format"]["duration"]) == pytest.approx(3.2, abs=0.08)
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(tmp_path / "out.mp4"), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W, 3)
    during = frames[int(1.6 * FPS)].astype(int)
    assert during[int(0.6 * H) - 10, int(0.8 * W)][2] < 80                     # the yellow character is there
    a, b = frames[int(1.5 * FPS)].astype(int), frames[int(1.6 * FPS)].astype(int)
    assert np.abs(a[:, :int(0.5 * W)] - b[:, :int(0.5 * W)]).mean() < 3       # the host is frozen meanwhile


def test_photos_of_every_steady_moment_of_a_video(tmp_path):
    ffmpeg = _ffmpeg()
    video = tmp_path / "closeup.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=25:d=12", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    resource = {"path": str(video), "kind": "video", "intervals": [{"start": 0.5, "end": 9.5, "kind": "still"},
                                                                  {"start": 10.0, "end": 11.0, "kind": "moving"}]}
    photos = show_characters.library_photos(resource, tmp_path / "photos", ffmpeg, every=4.0)
    # A photo every 4 s of a long steady stretch, at least one of a short one, each at the middle of its part.
    assert [item["second"] for item in photos] == [2.75, 7.25, 10.5]
    assert all(Path(item["photo"]).is_file() and item["video"] == str(video) for item in photos)
    assert Image.open(photos[0]["photo"]).size == (W, H)                                    # full size
    again = show_characters.library_photos(resource, tmp_path / "photos", ffmpeg, every=4.0)
    assert [item["photo"] for item in again] == [item["photo"] for item in photos]          # taken once


def test_photos_and_characters_through_the_api(tmp_path, monkeypatch):
    import json

    import server.api as api_module
    from core import show_proposals
    from core.project import create_project
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    ffmpeg = _ffmpeg()
    project = create_project("Show", str(tmp_path / "Chars.zuckervid"))
    video = tmp_path / "closeup.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=25:d=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [], source=str(video), resources=[{"path": str(video)}])
    client = api_module.create_app(project_path=str(project.folder)).test_client()
    assert client.post("/api/v1/show/photos", json={"source": str(tmp_path / "other.mp4"), "second": 1}).status_code == 400
    taken = client.post("/api/v1/show/photos", json={"source": str(video), "second": 1.0, "name": "test card"}).get_json()
    assert taken["second"] == 1.0 and (project.folder / "photos" / "photos.json").is_file()
    photos = client.get("/api/v1/show/photos").get_json()["items"]
    assert len(photos) == 1 and client.get(photos[0]["url"]).status_code == 200
    _photo(tmp_path / "outside.png")
    assert client.post("/api/v1/show/characters/cutout", json={"photo": str(tmp_path / "outside.png")}).status_code == 400
    cut = client.post("/api/v1/show/characters/cutout", json={"photo": taken["photo"], "box": [0.2, 0.2, 0.6, 0.6],
                                                              "strokes": [{"mode": "keep", "points": [[0.5, 0.5]], "radius": 0.05}]}).get_json()
    assert cut["method"] == "opencv-grabcut" and client.get(cut["url"]).status_code in (200, 206)
    saved = client.post("/api/v1/show/characters", json={"photo": taken["photo"], "mask": cut["mask"], "name": "Test card",
                                                         "mouth": {"rect": [0.3, 0.6, 0.4, 0.1]}, "pivot": [0.5, 1.0]}).get_json()
    assert saved["name"] == "Test card" and saved["provenance"]["second"] == 1.0
    listed = client.get("/api/v1/show/characters").get_json()["items"]
    assert [item["name"] for item in listed] == ["Test card"] and client.get(listed[0]["url"]).status_code == 200
    library = client.post("/api/v1/show/photos/library", json={"source": str(video)}).get_json()
    assert library["added"] >= 1 and len(client.get("/api/v1/show/photos").get_json()["items"]) == 1 + library["added"]
    assert client.post("/api/v1/show/photos/library", json={"source": str(tmp_path / "other.mp4")}).status_code == 400
    rigged = client.patch("/api/v1/show/characters", json={"folder": listed[0]["folder"], "voice": [str(video), 0.0, 1.0]}).get_json()
    assert rigged["voice"][2] == 1.0
    assert client.patch("/api/v1/show/characters", json={"folder": str(tmp_path), "voice": None}).status_code == 404
