"""Gags drawn onto a video: stickers where and when planned, warps, audio kept."""
import subprocess

import numpy as np
import pytest
from PIL import Image

from core.ffmpeg import ffprobe, tool_status
from core.gag_render import Sticker, load_sticker, render_gags, sticker_pose

W, H, FPS = 320, 180, 30


def _ffmpeg() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    return ffmpeg


def _grey_video(ffmpeg: str, path, seconds: float = 2.0):
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s={W}x{H}:r={FPS}:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "10", "-c:a", "aac", "-shortest", str(path)], check=True)


def _frames(ffmpeg: str, path) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W, 3)


def _red_square(path):
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(path)


def test_a_sticker_shows_only_during_its_time_at_its_place(tmp_path):
    ffmpeg = _ffmpeg()
    _grey_video(ffmpeg, tmp_path / "in.mp4")
    _red_square(tmp_path / "red.png")
    gag = {"image": str(tmp_path / "red.png"), "at": 0.5, "duration": 1.0, "x": 0.25, "y": 0.5, "width": 0.2, "effect": "jelly"}
    render_gags(tmp_path / "in.mp4", [gag], tmp_path / "out.mp4", ffmpeg=ffmpeg)
    frames = _frames(ffmpeg, tmp_path / "out.mp4")
    assert len(frames) == 2 * FPS
    centre = (H // 2, W // 4)
    assert frames[int(0.3 * FPS)][centre][0] < 160            # before: grey
    assert frames[int(1.0 * FPS)][centre][0] > 200            # during: red
    assert frames[int(1.0 * FPS)][H // 2, W * 3 // 4][0] < 160  # elsewhere: grey
    assert frames[int(1.7 * FPS)][centre][0] < 160            # after: grey again
    assert any(stream.get("codec_type") == "audio" for stream in ffprobe(str(tmp_path / "out.mp4"))["streams"])


def test_a_warp_bends_the_picture_while_it_lasts(tmp_path):
    ffmpeg = _ffmpeg()
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "10", str(tmp_path / "in.mp4")], check=True)
    render_gags(tmp_path / "in.mp4", [{"warp": "swirl", "at": 0.5, "duration": 1.0, "strength": 1.0}], tmp_path / "out.mp4", ffmpeg=ffmpeg)
    before, after = _frames(ffmpeg, tmp_path / "in.mp4"), _frames(ffmpeg, tmp_path / "out.mp4")
    difference = lambda i: np.abs(before[i].astype(int) - after[i].astype(int)).mean()  # noqa: E731
    assert difference(int(1.0 * FPS)) > 10   # mid-warp: clearly bent
    assert difference(int(0.2 * FPS)) < 3    # before: untouched (re-encoding noise only)


def test_peeling_scene_cut_out_starts_in_place_and_ends_at_its_spot(tmp_path):
    _red_square(tmp_path / "red.png")
    sticker = load_sticker({"image": str(tmp_path / "red.png"), "at": 0.0, "duration": 3.0, "x": 0.2, "y": 0.4,
                            "width": 0.3, "effect": "none", "from_box": [0.8, 0.1, 0.1, 0.1]}, 1920, 1080)
    x, y, sx, _, _ = sticker_pose(sticker, 0.0, 1920, 1080)
    assert (round(x), round(y)) == (round(0.85 * 1920), round(0.15 * 1080))
    assert sx == pytest.approx(0.1 / 0.3, rel=0.01)           # same size as the cut-out
    x, y, sx, _, _ = sticker_pose(sticker, 1.5, 1920, 1080)
    assert (round(x), round(y), round(sx, 2)) == (round(0.2 * 1920), round(0.4 * 1080), 1.0)


def test_a_sticker_runs_along_its_path_and_shrinks_away_at_the_end(tmp_path):
    _red_square(tmp_path / "red.png")
    sticker = load_sticker({"image": str(tmp_path / "red.png"), "at": 1.0, "duration": 2.0, "width": 0.1, "effect": "none",
                            "path": [[0, 0.1, 0.5], [1, 0.9, 0.5]], "hops": 2}, 1000, 1000)
    xs = [sticker_pose(sticker, 1.0 + share * 2.0, 1000, 1000)[0] for share in (0.0, 0.5, 0.99)]
    assert xs[0] == pytest.approx(100) and xs[1] == pytest.approx(500) and xs[2] > 890
    assert sticker_pose(sticker, 1.25, 1000, 1000)[1] < 500   # mid-hop: in the air
    assert sticker_pose(sticker, 2.99, 1000, 1000)[2] < 0.3   # leaving: shrinking
    assert isinstance(sticker, Sticker)


def _still_with_voice(ffmpeg: str, folder, seconds: float = 2.0):
    """A textured still picture; silence for 1 s, then a loud voice."""
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=1:d=1", "-frames:v", "1",
                    str(folder / "still.png")], check=True)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(folder / "still.png"),
                    "-f", "lavfi", "-i", f"aevalsrc='if(lt(t,1),0,0.6*sin(2*PI*220*t))':s=48000:d={seconds}",
                    "-t", str(seconds), "-c:v", "libx264", "-crf", "8", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    str(folder / "in.mp4")], check=True)


def test_a_filmed_mouth_drops_with_the_voice_and_shows_black_behind(tmp_path):
    ffmpeg = _ffmpeg()
    _still_with_voice(ffmpeg, tmp_path)
    mouth = [0.4, 0.5, 0.2, 0.15]
    render_gags(tmp_path / "in.mp4", [{"mouth": mouth, "at": 0.0, "duration": 2.0, "open": 0.06}], tmp_path / "out.mp4", ffmpeg=ffmpeg)
    before, after = _frames(ffmpeg, tmp_path / "in.mp4"), _frames(ffmpeg, tmp_path / "out.mp4")
    x0, y0, x1, y1 = int(0.4 * W), int(0.5 * H), int(0.6 * W), int(0.65 * H)
    quiet, loud = int(0.5 * FPS), int(1.5 * FPS)
    assert np.abs(after[quiet].astype(int) - before[quiet].astype(int)).mean() < 3      # silent: untouched
    region_after, region_before = after[loud][:, x0:x1].astype(int), before[loud][:, x0:x1].astype(int)
    # The patch's own pixels, moved down by some drop; black above them.
    errors = {d: np.abs(region_after[y0 + d:y1 - 2] - region_before[y0:y1 - 2 - d]).mean() for d in range(3, 13)}
    drop = min(errors, key=errors.get)
    assert errors[drop] < 8 and drop >= 5
    assert region_after[y0 + 2:y0 + drop - 2].mean() < 30          # black behind (compression blurs its edges)
    assert np.abs(after[loud][:, :x0 - 2].astype(int) - before[loud][:, :x0 - 2].astype(int)).mean() < 3   # nothing else moves


def test_a_lost_mouth_stops_instead_of_moving_the_wrong_pixels(tmp_path):
    from core.gag_render import Mouth, move_mouth
    rng = np.random.default_rng(1)
    first = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)
    mouth = Mouth(at=0, duration=1, rect=[100.0, 80.0, 40.0, 20.0], open_px=10, envelope=np.ones(30, dtype=np.float32))
    move_mouth(first.copy(), mouth, 0.0, FPS)                     # learns the mouth's surroundings
    shifted = np.roll(first, 6, axis=1)                           # the camera moves a little: followed
    move_mouth(shifted.copy(), mouth, 1 / FPS, FPS)
    assert mouth.rect[0] == pytest.approx(106, abs=1) and not mouth.lost
    other = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)      # a different picture: lost
    untouched = other.copy()
    move_mouth(other, mouth, 2 / FPS, FPS)
    assert mouth.lost and np.array_equal(other, untouched)
