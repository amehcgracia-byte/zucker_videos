import json
import shutil
import subprocess
from concurrent.futures import Future
from pathlib import Path

import pytest

from core import media_integrity
from core.media_integrity import damaged_spans, looks_like_damage

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                  reason="ffmpeg/ffprobe not available")


def _damaged_clip(tmp_path: Path, zero_from: float, zero_to: float) -> Path:
    path = tmp_path / "C0027.MP4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=8",
                    "-c:v", "libx264", "-g", "12", "-pix_fmt", "yuv420p", str(path)], check=True)
    packets = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,pos,size", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout)["packets"]
    with open(path, "r+b") as handle:  # what a failed SD-card write leaves behind
        for packet in packets:
            if zero_from <= float(packet["pts_time"]) <= zero_to:
                handle.seek(int(packet["pos"]))
                handle.write(b"\x00" * int(packet["size"]))
    return path


@needs_ffmpeg
def test_finds_zero_filled_frames_only_where_they_are(tmp_path):
    media_integrity._damaged_spans_cached.cache_clear()
    clip = _damaged_clip(tmp_path, 4.0, 4.6)
    spans = damaged_spans(clip, 4.2, 4.5)
    assert spans and spans[0][0] <= 4.2 and spans[-1][1] >= 4.4
    assert damaged_spans(clip, 1.0, 2.0) == []
    # One second of reference frames before the window is checked too.
    assert damaged_spans(clip, 4.9, 5.4)


def test_other_containers_are_not_guessed(tmp_path):
    assert damaged_spans(tmp_path / "clip.mkv", 0, 1) == []


def test_damage_is_recognised_in_ffmpeg_errors():
    assert looks_like_damage("[h264 @ 0x1] Invalid NAL unit size (0 > 149618).")
    assert looks_like_damage("Decode error rate 0.863636 exceeds maximum 0.666667")
    assert not looks_like_damage("No such file or directory")


def test_reel_shot_moves_off_damage_to_a_readable_window(monkeypatch):
    from core.stages import edit
    damage = [(44.0, 47.0)]

    def fake_spans(path, start, end):
        return [(max(a, start - 1.0), min(b, end)) for a, b in damage if a < end and b > start - 1.0]

    monkeypatch.setattr("core.media_integrity.damaged_spans", fake_spans)
    monkeypatch.setattr(edit, "director_quality_for_segment", lambda source, a, b: {"score": 0.9 if a < 40 else 0.95})
    source = {"source_path": "/v/C0027.MP4", "duration_sec": 60.0, "director_quality": {"windows": [
        {"clip_start_sec": start, "clip_end_sec": start + 2.0} for start in (10.0, 20.0, 46.0)]}}
    blocked: list = []
    start = edit._reel_quality_start(source, 0.4, [], 46.0, blocked)
    assert start == 46.0  # the best-scored window, before any check
    start, found = edit._readable_reel_start(source, 0.4, [], start, blocked)
    assert found and start in (10.0, 20.0)
    assert not edit._overlaps_blocked(start, 0.4, blocked)


def test_one_failed_review_photo_does_not_stop_the_edit(caplog):
    from core.shot_review import ReviewFramePrefetch, ThumbnailRenderError
    prefetch = ReviewFramePrefetch.__new__(ReviewFramePrefetch)
    ok, failed = Future(), Future()
    ok.set_result(None)
    failed.set_exception(ThumbnailRenderError(media_integrity.DAMAGE_MESSAGE))
    prefetch.futures = [ok, failed]
    progress = []
    prefetch.finish(lambda percent, detail: progress.append(percent))
    assert progress[-1] == 100
