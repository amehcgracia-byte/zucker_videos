import importlib
import os
import subprocess
import sys
from pathlib import Path

def test_caption_package_has_no_mode_imports() -> None:
    forbidden_modules = {
        "core.stages.edit",
        "core.stages.export",
        "server.wizard",
        "core.stages.backstage",
        "core.backstage_transcription",
    }
    script = (
        "import importlib, sys; "
        "[importlib.import_module('captions.' + name) for name in "
        "('align', 'burn', 'layout', 'model', 'render', 'sources', 'styles', 'timing')]; "
        "forbidden = " + repr(sorted(forbidden_modules)) + "; "
        "bad = sorted(set(sys.modules) & set(forbidden)); "
        "print(bad); raise SystemExit(1 if bad else 0)"
    )
    env = {**os.environ, "PYTHONPATH": str(Path.cwd())}
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_backstage_builds_its_own_subtitle_filtergraph(tmp_path) -> None:
    from core.stages.backstage import _backstage_single_pass_filtergraph

    ass_path = tmp_path / "backstage.ass"
    ass_path.write_text("[Script Info]\n", encoding="utf-8")
    _inputs, graph, _label, _duration, _muted = _backstage_single_pass_filtergraph(
        [{"source_path": "/tmp/clip.mp4", "duration_sec": 2.0}],
        ass_path=ass_path,
    )
    assert "subtitles=filename=" in graph
    assert "captions.burn" not in graph


def test_backstage_subtitle_path_does_not_call_common_burn(monkeypatch, tmp_path) -> None:
    from core.stages.backstage import _backstage_single_pass_filtergraph

    calls: list[tuple] = []

    def common_burn(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Backstage must not call captions.burn.burn")

    burn_module = importlib.import_module("captions.burn")
    monkeypatch.setattr(burn_module, "burn", common_burn)
    ass_path = tmp_path / "backstage.ass"
    ass_path.write_text("[Script Info]\n", encoding="utf-8")
    _backstage_single_pass_filtergraph(
        [{"source_path": "/tmp/clip.mp4", "duration_sec": 2.0}],
        ass_path=ass_path,
    )
    assert calls == []


def test_backstage_burns_subtitles_in_a_project_named_with_an_apostrophe(tmp_path) -> None:
    import pytest
    from core.ffmpeg import tool_status
    from core.stages.backstage import _backstage_single_pass_filtergraph, _run_backstage_ffmpeg, _write_backstage_ass

    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout if ffmpeg else ""
    if " subtitles " not in filters:
        pytest.skip("FFmpeg without libass")
    cache = tmp_path / "Singer's Live [take 1].zuckervid" / "cache" / "zucker-backstage-x"
    cache.mkdir(parents=True)
    clip = cache.parent / "clip.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=navy:s=320x240:d=2:r=30", "-f", "lavfi", "-i", "sine=d=2",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(clip)], check=True)
    ass_path = cache / "backstage-en.ass"
    _write_backstage_ass(ass_path, [(0.0, 1.8, "HELLO THERE")])
    inputs, graph, _label, duration, _muted = _backstage_single_pass_filtergraph(
        [{"source_path": str(clip), "duration_sec": 2.0}], ass_path=ass_path, width=320, height=240, fps=30)
    assert "subtitles=filename=backstage-en.ass" in graph
    output = cache.parent / "out.mp4"
    _run_backstage_ffmpeg([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-progress", "pipe:1", *inputs,
                           "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", "-t", f"{duration:.3f}", str(output)], cwd=cache)
    frame = subprocess.run([ffmpeg, "-v", "error", "-ss", "1", "-i", str(output), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                           check=True, capture_output=True).stdout
    assert sum(1 for r, g, b in zip(frame[::3], frame[1::3], frame[2::3]) if min(r, g, b) > 210) > 20
