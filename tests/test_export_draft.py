from pathlib import Path

import pytest

from core.project import create_project, file_record
from core.stages import export

MOVE = {"type": "singer", "yaw": 10, "movement": "pan_left"}
HOLD = {"type": "singer", "yaw": 10}


@pytest.fixture
def project_and_segment(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Draft", str(tmp_path / "Draft.zuckervid"))
    source = tmp_path / "360.mp4"
    source.write_bytes(b"360")
    record = file_record(str(source))
    record["probe"] = {"valid_video": True, "projection": "equirect", "bit_depth": 8}
    record["cache_key"] = "source-key"
    project.data["inputs"]["videos"] = [record]
    segment = {"clip_path": str(source), "source_path": str(source), "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 3}
    return project, segment


def _key(project, segment, draft):
    project.data["settings"].setdefault("export", {})["draft"] = draft
    return export.cached_segment_path(project, segment, "youtube", 18_000_000, {}, {}, False, False)


def test_draft_moves_get_their_own_cache_key(project_and_segment):
    project, segment = project_and_segment
    move = {**segment, "spherical_shot": MOVE}
    assert _key(project, move, True) != _key(project, move, False)


def test_draft_shares_cache_for_segments_rendered_identically(project_and_segment):
    project, segment = project_and_segment
    hold = {**segment, "spherical_shot": HOLD}
    assert _key(project, hold, True) == _key(project, hold, False)
    assert _key(project, segment, True) == _key(project, segment, False)


def test_draft_moves_read_the_export_proxy_instead_of_the_original(project_and_segment):
    project, segment = project_and_segment
    move = {**segment, "spherical_shot": MOVE}
    info = export._segment_source_info(project, move)
    assert export._spherical_uses_original_motion(info, move)
    assert not export._spherical_uses_original_motion(info, move, draft=True)


def test_draft_export_is_named_and_recorded_as_draft(project_and_segment):
    project, _ = project_and_segment
    assert "-draft" not in export._output_path(project, "youtube", "run").name
    project.data["settings"].setdefault("export", {})["draft"] = True
    assert export._output_path(project, "youtube", "run").name == "Draft-youtube-draft-run.mp4"


def test_run_options_store_draft_and_seed_on_the_rendering_project(project_and_segment):
    from server.api import _apply_run_options
    project, _ = project_and_segment
    _apply_run_options(project, {"variation_seed": "seed-1", "draft_export": True})
    assert project.data["settings"]["export"]["draft"] is True
    assert project.data["settings"]["wizard"]["variation_seed"] == "seed-1"
    _apply_run_options(project, {"variation_seed": "seed-2"})
    assert project.data["settings"]["export"]["draft"] is False


def test_frontend_sends_and_restores_the_draft_choice():
    source = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    assert 'draft_export: document.querySelector("#draftExport").checked' in source
    assert 'document.querySelector("#draftExport").checked = Boolean(project.settings?.export?.draft)' in source
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="draftExport"' in html
