import shutil
import subprocess
from pathlib import Path

import pytest

from core import shot_quality

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg/ffprobe not available")


def _clip(path: Path, gop: int, seconds: float = 6.0) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size=640x360:rate=25:duration={seconds}", "-c:v", "libx264", "-g", str(gop),
                    "-keyint_min", str(gop), "-sc_threshold", "0", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))


@pytest.fixture
def project(tmp_path):
    from core.project import create_project
    return create_project("Scoring", str(tmp_path / "scoring.zuckervid"))


def test_dense_keyframes_decode_only_keyframes(tmp_path, monkeypatch):
    clip = _clip(tmp_path / "sony-like.mp4", gop=12)
    assert shot_quality._keyframe_interval(clip) == pytest.approx(0.48, abs=0.02)
    commands = []
    real_popen = subprocess.Popen
    monkeypatch.setattr(shot_quality.subprocess, "Popen", lambda command, **kw: commands.append(command) or real_popen(command, **kw))
    frames, error = shot_quality._extract_quality_frames("ffmpeg", clip, 6.0, None)
    assert error is None and abs(len(frames) - 12) <= 1
    assert "-skip_frame" in next(command for command in commands if "-vf" in command)
    assert all(frame[:2] == b"\xff\xd8" and frame[-2:] == b"\xff\xd9" for frame in frames)


def test_sparse_keyframes_keep_the_full_decode(tmp_path, monkeypatch):
    clip = _clip(tmp_path / "long-gop.mp4", gop=250)
    commands = []
    real_popen = subprocess.Popen
    monkeypatch.setattr(shot_quality.subprocess, "Popen", lambda command, **kw: commands.append(command) or real_popen(command, **kw))
    frames, error = shot_quality._extract_quality_frames("ffmpeg", clip, 6.0, None)
    assert error is None and abs(len(frames) - 12) <= 1
    assert "-skip_frame" not in next(command for command in commands if "-vf" in command)


def test_scoring_reports_each_source_and_reuses_the_cache(tmp_path, monkeypatch, project):
    first, second = _clip(tmp_path / "C0001.MP4", 12, 4.0), _clip(tmp_path / "C0002.MP4", 12, 4.0)
    sources = [{"path": str(first), "filename": first.name, "duration_sec": 4.0},
               {"path": str(second), "filename": second.name, "duration_sec": 4.0}]
    reports = []
    scored = shot_quality.analyze_handheld_director_quality(project, sources, lambda fraction, detail: reports.append((fraction, detail)))
    assert all(source["director_quality"].get("windows") for source in scored)
    assert reports[0] == (0.0, "1/2 · C0001.MP4") and any(detail.startswith("2/2 · C0002.MP4") for _, detail in reports)
    assert [fraction for fraction, _ in reports] == sorted(fraction for fraction, _ in reports)
    monkeypatch.setattr(shot_quality.subprocess, "Popen", lambda *a, **k: pytest.fail("cached source re-extracted"))
    again = shot_quality.analyze_handheld_director_quality(project, sources)
    assert [s["director_quality"]["summary"] for s in again] == [s["director_quality"]["summary"] for s in scored]


def test_cancelling_during_scoring_stops_instead_of_skipping_the_clip(tmp_path, project):
    clip = _clip(tmp_path / "C0003.MP4", 12, 4.0)

    class Cancelled(Exception):
        pass

    def cancel(fraction, detail):
        raise Cancelled()

    with pytest.raises(Cancelled):
        shot_quality.analyze_handheld_director_quality(project, [{"path": str(clip), "filename": clip.name}], cancel)


def test_unreadable_clip_is_recorded_not_fatal(tmp_path, project):
    broken = tmp_path / "C0004.MP4"
    broken.write_bytes(b"not a video")
    scored = shot_quality.analyze_handheld_director_quality(project, [{"path": str(broken), "filename": broken.name}])
    assert scored[0]["director_quality"]["error"]
