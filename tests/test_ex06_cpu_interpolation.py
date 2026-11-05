from unittest.mock import patch

import pytest

from core import spherical_motion as sm


def test_cubic_remains_default():
    calls = []
    with patch.object(sm, "_run_reprojected_command",
                      side_effect=lambda *a, **kw: calls.append(kw)):
        sm.run_reprojected_command(
            ["ffmpeg"], "fixture.mp4", (640, 320), 0, 30, {},
            hardware_decode=False)
    assert calls[0]["cpu_interpolation"] == "cubic"


def test_linear_is_explicit_opt_in():
    calls = []
    with patch.object(sm, "_run_reprojected_command",
                      side_effect=lambda *a, **kw: calls.append(kw)):
        sm.run_reprojected_command(
            ["ffmpeg"], "fixture.mp4", (640, 320), 0, 30, {},
            hardware_decode=False, cpu_interpolation="linear")
    assert calls[0]["cpu_interpolation"] == "linear"


def test_invalid_interpolation_rejected():
    with pytest.raises(ValueError, match="cpu_interpolation"):
        sm.run_reprojected_command(
            ["ffmpeg"], "fixture.mp4", (640, 320), 0, 30, {},
            cpu_interpolation="nearest")


def test_hardware_fallback_preserves_interpolation(tmp_path, monkeypatch):
    source = tmp_path / "sphere.mp4"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(sm.sys, "platform", "darwin")
    monkeypatch.setattr(
        sm, "_verified_hardware_format",
        lambda *args: "yuvj420p")

    calls = []

    def renderer(*args, **kwargs):
        calls.append(kwargs)
        if kwargs.get("decoder_pixel_format"):
            raise sm.HardwareDecodeError("simulated failure")

    with patch.object(sm, "_run_reprojected_command",
                      side_effect=renderer):
        sm.run_reprojected_command(
            ["ffmpeg"], str(source), (640, 320), 0, 30, {},
            cpu_interpolation="linear")

    assert len(calls) == 2
    assert all(
        call["cpu_interpolation"] == "linear"
        for call in calls
    )
    assert "decoder_pixel_format" not in calls[1]
