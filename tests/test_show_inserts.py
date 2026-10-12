"""Character interventions (frozen close-up speaking with its own voice) and rainbow steam."""
import subprocess

import numpy as np
import pytest
from PIL import Image

from core.ffmpeg import ffprobe, tool_status
from core.gag_render import render_gags
from core.show_inserts import Steam, convex_hull, draw_steam, load_still, region_polygon, shift

W, H, FPS = 320, 180, 30


def _ffmpeg() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    return ffmpeg


def _frames(ffmpeg: str, path) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W, 3)


def _sound(ffmpeg: str, path) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _pitch(samples: np.ndarray) -> float:
    if np.sqrt(np.mean(samples ** 2)) < 0.01:
        return 0.0
    spectrum = np.abs(np.fft.rfft(samples))
    return float(np.argmax(spectrum) * 8000 / len(samples))


def _host(ffmpeg: str, path, seconds: float = 2.0):
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x303030:s={W}x{H}:r={FPS}:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)], check=True)


def _closeup(ffmpeg: str, path):
    # A bright textured close-up; its "voice" (1000 Hz) speaks from 0.5 s to 1.5 s.
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=25:d=3",
                    "-f", "lavfi", "-i", "aevalsrc='if(between(t,0.5,1.5),0.5*sin(2*PI*1000*t),0)':s=48000:d=3",
                    "-c:v", "libx264", "-crf", "8", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(path)], check=True)


def test_a_character_interrupts_the_host_with_its_own_voice_and_pixels(tmp_path):
    ffmpeg = _ffmpeg()
    _host(ffmpeg, tmp_path / "host.mp4")
    _closeup(ffmpeg, tmp_path / "closeup.mp4")
    insert = {"insert": "character", "at": 1.0, "source": str(tmp_path / "closeup.mp4"), "still_time": 1.0,
              "zoom": 1.5, "zoom_sec": 0.4, "voice": [str(tmp_path / "closeup.mp4"), 0.5, 1.0],
              "region": {"rect": [0.4, 0.5, 0.2, 0.15]}, "action": "mouth", "amount": 0.06}
    render_gags(tmp_path / "host.mp4", [insert], tmp_path / "out.mp4", ffmpeg=ffmpeg)
    duration = float(ffprobe(str(tmp_path / "out.mp4"))["format"]["duration"])
    assert duration == pytest.approx(3.4, abs=0.08)               # 2 s host + 0.4 s push-in + 1 s voice
    frames = _frames(ffmpeg, tmp_path / "out.mp4")
    assert frames[15].mean() < 70 and frames[80].mean() < 70     # the host before and after
    assert frames[36].mean() > 80                                 # the frozen close-up
    still_a, still_b = frames[50].astype(int), frames[51].astype(int)
    # Frozen: between two voice frames only the jaw region changes.
    assert np.abs(still_a[:, :int(0.3 * W)] - still_b[:, :int(0.3 * W)]).mean() < 3
    sound = _sound(ffmpeg, tmp_path / "out.mp4")
    assert _pitch(sound[1000:7000]) == pytest.approx(440, abs=20)       # host talking
    assert np.sqrt(np.mean(sound[8400:10800] ** 2)) < 0.02               # host paused during the push-in
    assert _pitch(sound[11500:18500]) == pytest.approx(1000, abs=30)    # the character's own voice
    assert _pitch(sound[20000:26000]) == pytest.approx(440, abs=20)     # the host carries on


def test_the_jaw_opens_only_while_the_character_speaks(tmp_path):
    ffmpeg = _ffmpeg()
    _host(ffmpeg, tmp_path / "host.mp4", 1.0)
    _closeup(ffmpeg, tmp_path / "closeup.mp4")
    # Voice window 0.0-1.5 s of the close-up: silent first half second, then speech.
    insert = {"insert": "character", "at": 0.5, "source": str(tmp_path / "closeup.mp4"), "still_time": 1.0, "zoom": 1.0,
              "zoom_sec": 0.0, "voice": [str(tmp_path / "closeup.mp4"), 0.0, 1.5],
              "region": {"rect": [0.4, 0.5, 0.2, 0.15]}, "action": "mouth", "amount": 0.08}
    render_gags(tmp_path / "host.mp4", [insert], tmp_path / "out.mp4", ffmpeg=ffmpeg)
    frames = _frames(ffmpeg, tmp_path / "out.mp4")
    x0, y0, x1 = int(0.4 * W), int(0.5 * H), int(0.6 * W)
    silent, speaking = frames[15 + 6], frames[15 + 30]
    assert silent[y0 + 2:y0 + 8, x0:x1].mean() > 40            # closed: the original pixels
    assert speaking[y0 + 2:y0 + 8, x0:x1].mean() < 30          # open: black behind the jaw


def test_a_photo_has_no_voice_and_holds_for_its_time(tmp_path):
    ffmpeg = _ffmpeg()
    _host(ffmpeg, tmp_path / "host.mp4")
    Image.new("RGB", (400, 300), (200, 180, 40)).save(tmp_path / "photo.png")
    insert = {"insert": "character", "at": 1.0, "source": str(tmp_path / "photo.png"), "hold": 1.0, "zoom_sec": 0.5,
              "region": {"polygon": [[0.3, 0.3], [0.7, 0.3], [0.5, 0.5], [0.7, 0.7], [0.3, 0.7]]}, "action": "turn"}
    render_gags(tmp_path / "host.mp4", [insert], tmp_path / "out.mp4", ffmpeg=ffmpeg)
    assert float(ffprobe(str(tmp_path / "out.mp4"))["format"]["duration"]) == pytest.approx(3.5, abs=0.08)
    sound = _sound(ffmpeg, tmp_path / "out.mp4")
    assert np.sqrt(np.mean(sound[8400:19500] ** 2)) < 0.02      # no voice: the host waits in silence


def test_a_black_frame_is_refused_unless_allowed(tmp_path):
    ffmpeg = _ffmpeg()
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r=25:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "dark.mp4")], check=True)
    with pytest.raises(ValueError, match="black"):
        load_still(tmp_path / "dark.mp4", 0.5, ffmpeg)
    assert load_still(tmp_path / "dark.mp4", 0.5, ffmpeg, allow_dark=True).shape == (H, W, 3)


def test_point_selections_close_to_their_convex_outline():
    points = [(0.1, 0.1), (0.9, 0.1), (0.5, 0.3), (0.9, 0.9), (0.1, 0.9), (0.5, 0.5)]   # a dent and an inner point
    assert sorted(convex_hull(points)) == sorted([(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)])
    assert region_polygon({"rect": [0.2, 0.2, 0.1, 0.1]})[2] == pytest.approx((0.3, 0.3))
    with pytest.raises(ValueError):
        region_polygon({"polygon": [[0.1, 0.1], [0.2, 0.2]]})


def test_interventions_push_later_moments_back():
    spans = [(10.0, 3.0), (20.0, 2.0)]
    assert shift(5.0, spans) == 5.0 and shift(15.0, spans) == 18.0 and shift(25.0, spans) == 30.0


def test_rainbow_steam_rises_from_its_point_and_follows_the_mug():
    frame = np.full((H, W, 3), 40, dtype=np.uint8)
    steam = Steam(at=0.0, duration=4.0, x=160.0, y=150.0, scale=1.0)
    draw_steam(frame, steam, 1.0)
    changed = np.argwhere(np.abs(frame.astype(int) - 40).sum(axis=2) > 0)
    assert len(changed) and changed[:, 0].max() <= 152 and changed[:, 0].min() < 120   # above the point, rising
    rng = np.random.default_rng(3)
    scene = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)
    anchored = Steam(at=0.0, duration=4.0, x=160.0, y=120.0, scale=0.5, rect=[140.0, 120.0, 40.0, 30.0])
    draw_steam(scene.copy(), anchored, 1.0)                          # learns the mug
    draw_steam(np.roll(scene, 8, axis=1).copy(), anchored, 1.1)       # the mug moves right
    assert anchored.x == pytest.approx(168, abs=1) and not anchored.lost
