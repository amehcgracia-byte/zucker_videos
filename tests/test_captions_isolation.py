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
