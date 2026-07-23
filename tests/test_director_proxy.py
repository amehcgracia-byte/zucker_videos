from __future__ import annotations

import shutil
import subprocess

import pytest

from core.director_proxy import FFmpegError, _verify_proxy_output, ensure_director_proxy


def _make_clip(path, duration: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=64x32:rate=10:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_verify_proxy_output_accepts_a_complete_file(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    clip = tmp_path / "complete.mp4"
    _make_clip(clip, 2.0)

    _verify_proxy_output(clip, expected_duration=2.0, filename="complete.mp4")

    assert clip.exists()


def test_verify_proxy_output_rejects_truncated_duration(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    clip = tmp_path / "truncated.mp4"
    _make_clip(clip, 1.0)  # encoded a 1s clip but the caller expected 10s

    with pytest.raises(FFmpegError, match="truncated"):
        _verify_proxy_output(clip, expected_duration=10.0, filename="truncated.mp4")

    assert not clip.exists(), "a truncated proxy must not be left behind to be served as ready"


def test_verify_proxy_output_rejects_unreadable_file(tmp_path):
    clip = tmp_path / "garbage.mp4"
    clip.write_bytes(b"not a real video file at all")

    with pytest.raises(FFmpegError):
        _verify_proxy_output(clip, expected_duration=5.0, filename="garbage.mp4")

    assert not clip.exists()


def test_ensure_director_proxy_raises_and_leaves_no_partial_file_when_encode_is_truncated(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    source = tmp_path / "source.mp4"
    _make_clip(source, 5.0)

    record = {"path": str(source), "projection": "equirect", "probe": {"duration": 5.0, "projection": "equirect"}}

    def fake_run_proxy_command(command, duration, filename, progress):
        # Simulate an encode that exits cleanly but produced far less video
        # than the source actually has (e.g. a stalled/killed remux). The
        # destination path is always the last argument (see _proxy_command).
        _make_clip(command[-1], 1.0)

    monkeypatch.setattr("core.director_proxy._run_proxy_command", fake_run_proxy_command)

    with pytest.raises(FFmpegError, match="truncated"):
        ensure_director_proxy(record)

    from core.director_proxy import director_proxy_path

    output = director_proxy_path(record)
    assert not output.exists()
    assert not output.with_suffix(".tmp.mp4").exists()
