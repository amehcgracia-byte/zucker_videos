from __future__ import annotations

from core.stages.export import MAX_EXPORT_BYTES, MIN_ACCEPTABLE_VIDEO_BITRATE, _bitrate_for_duration, _run_ffmpeg_progress


class FakeProcess:
    def __init__(self) -> None:
        self.stdout = iter(["out_time_ms=1000000\n", "out_time_ms=2000000\n", "progress=end\n"])
        self.stderr = ""
        self.returncode = 0

    def communicate(self) -> tuple[str, str]:
        return "", ""


def test_export_ffmpeg_progress_reports_percent(monkeypatch):
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr("core.stages.export.subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    _run_ffmpeg_progress(["ffmpeg"], 4.0, "clip.mp4", lambda percent, detail: calls.append((percent, detail)))

    assert calls[0][0] == 25
    assert calls[1][0] == 50
    assert calls[-1] == (100, "clip.mp4 — 100%")


def test_export_bitrate_caps_long_outputs_under_telegram_budget():
    duration = (MAX_EXPORT_BYTES * 8) / (MIN_ACCEPTABLE_VIDEO_BITRATE / 2)

    result = _bitrate_for_duration(duration)

    assert result["video_bitrate"] < MIN_ACCEPTABLE_VIDEO_BITRATE
    assert "1.9 GB" in result["warning"]
