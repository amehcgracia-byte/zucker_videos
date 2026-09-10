from __future__ import annotations

from pathlib import Path

import pytest

from core.project import create_project, file_record
from core.retention import build_storage_report, execute_cleanup
from core.stages.base import artifact_path, write_artifact_json
from core.stages.edit import EditStage
from core.stages.export import (
    ExportStage,
    _reel_letterbox_filter,
    _reel_overlay_items,
    _segment_filtergraph,
    cached_segment_path,
)


def _coverage(platform: str) -> dict:
    return {
        "platform": platform,
        "window": {"title": "Test", "start_sec": 0.0, "duration_sec": 8.0},
        "sources": [
            {"path": "/tmp/a.mp4", "filename": "a.mp4", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 9.0},
            {"path": "/tmp/b.mp4", "filename": "b.mp4", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 8.0},
        ],
    }


def _run_edit(project, monkeypatch, platform: str) -> None:
    project.data["settings"]["wizard"] = {"platform": platform, "reel_duration_sec": 8.0}
    write_artifact_json(artifact_path(project, "coverage.json"), _coverage(platform))
    monkeypatch.setattr("core.stages.edit._load_or_analyze_beats", lambda *args: {"bars_sec": [0.0, 4.0, 8.0], "sections_sec": []})
    monkeypatch.setattr("core.stages.edit.validate_plan_camera_source_consistency", lambda _plan: None)
    EditStage().run(project, lambda *_args: None)


def test_reel_execution_does_not_call_youtube_planner(tmp_path, monkeypatch):
    project = create_project("Reel isolation", str(tmp_path / "Reel.zuckervid"))
    monkeypatch.setattr("core.stages.edit._youtube_multicam_plan", lambda *args, **kwargs: pytest.fail("YouTube planner called by Reel"))
    _run_edit(project, monkeypatch, "reel")


def test_youtube_execution_does_not_call_reel_planner(tmp_path, monkeypatch):
    project = create_project("YouTube isolation", str(tmp_path / "YouTube.zuckervid"))
    monkeypatch.setattr("core.stages.edit._reel_promo_plan", lambda *args, **kwargs: pytest.fail("Reel planner called by YouTube"))
    _run_edit(project, monkeypatch, "youtube")


def test_backstage_stage_selection_does_not_construct_common_stages(tmp_path, monkeypatch):
    from core.engine import PipelineEngine

    project = create_project("Backstage isolation", str(tmp_path / "Backstage.zuckervid"))
    project.data["settings"]["wizard"] = {"platform": "backstage"}
    engine = PipelineEngine()
    monkeypatch.setattr("core.engine.CutStage", lambda: pytest.fail("CutStage constructed for Backstage"))
    monkeypatch.setattr("core.engine.EditStage", lambda: pytest.fail("EditStage constructed for Backstage"))
    monkeypatch.setattr("core.engine.ExportStage", lambda: pytest.fail("ExportStage constructed for Backstage"))
    assert engine._stage_for_project("cut", project).__class__.__name__ == "BackstageAnalysisStage"
    assert engine._stage_for_project("edit", project).__class__.__name__ == "BackstageEditStage"
    assert engine._stage_for_project("export", project).__class__.__name__ == "BackstageExportStage"
    engine.shutdown()


def test_reel_letterbox_and_overlays_are_noops_outside_reel(tmp_path):
    project = create_project("Renderer guards", str(tmp_path / "Renderer.zuckervid"))
    project.data["settings"]["wizard"] = {"platform": "reel", "reel_aspect": "mix"}
    segment = {"source_path": "/tmp/source.mp4", "duration_sec": 2.0, "reel_mix_treatment": "horizontal"}
    assert _reel_letterbox_filter(project, segment, "youtube") is None
    assert _reel_letterbox_filter(project, segment, "360") is None
    config = {"reel_images": [{"path": str(tmp_path / "overlay.png"), "start_sec": 0.0}]}
    assert _reel_overlay_items(segment, config, "youtube", tmp_path) == []
    assert _reel_overlay_items(segment, config, "360", tmp_path) == []
    from PIL import Image

    overlay = tmp_path / "overlay.png"
    Image.new("RGBA", (32, 32), (255, 0, 0, 255)).save(overlay)
    config["reel_images"][0]["path"] = str(overlay)
    assert _reel_overlay_items(segment, config, "reel", tmp_path)


def test_filtergraphs_keep_mode_specific_operations_scoped(tmp_path):
    project = create_project("Filter isolation", str(tmp_path / "Filter.zuckervid"))
    project.data["settings"]["wizard"] = {"platform": "reel", "reel_aspect": "mix"}
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    record = file_record(str(source))
    record["probe"] = {"width": 1920, "height": 1080, "valid_video": True}
    record["cache_key"] = "filter-source"
    project.data["inputs"]["videos"] = [record]
    segment = {"source_path": str(source), "reel_subject_center": {"x": 0.5, "y": 0.5}, "reel_mix_treatment": "horizontal"}
    reel_letterbox = _reel_letterbox_filter(project, segment, "reel")
    reel = _segment_filtergraph("reel", 2.0, {}, {}, segment=segment, reel_letterbox_filter=reel_letterbox)
    youtube = _segment_filtergraph("youtube", 2.0, {}, {}, segment=segment)
    spherical = _segment_filtergraph("360", 2.0, {}, {}, source_filter="v360=input=equirect:output=flat", segment={})
    assert "split=" in reel and "gblur=" in reel
    assert "1080:1920" in reel
    assert "gblur=" not in youtube and "reel_blur" not in youtube and "1080:1920" not in youtube
    assert "v360=input=equirect" in spherical
    assert "reel_blur" not in spherical and "gblur=" not in spherical


def test_360_export_dispatches_only_to_spherical_renderer(tmp_path, monkeypatch):
    from core.stages import export as export_module

    project = create_project("360 dispatch", str(tmp_path / "360.zuckervid"))
    source = tmp_path / "360.mp4"
    master = tmp_path / "master.wav"
    source.write_bytes(b"source")
    master.write_bytes(b"master")
    project.data["settings"]["wizard"] = {"platform": "360"}
    project.data["inputs"]["videos"] = [{"path": str(source), "probe": {"valid_video": True}}]
    project.data["inputs"]["master"] = {"path": str(master)}
    plan = {
        "platform": "360",
        "window": {"start_sec": 0.0, "duration_sec": 2.0},
        "segments": [{"source_path": str(source), "clip_path": str(source), "duration_sec": 2.0}],
        "cut_count": 0,
    }
    calls: list[str] = []
    monkeypatch.setattr(export_module, "load_edit_plan", lambda _project: plan)
    monkeypatch.setattr(export_module, "_missing_project_sources", lambda *_args: [])
    monkeypatch.setattr(export_module, "_frame_normalized_segments", lambda segments: segments)
    monkeypatch.setattr(export_module, "_plan_duration", lambda _segments: 2.0)
    monkeypatch.setattr(export_module, "_bitrate_for_duration", lambda _duration: {"video_bitrate": 1, "warning": None})
    monkeypatch.setattr(export_module, "_required_export_space_bytes", lambda *_args: 0)
    monkeypatch.setattr(export_module, "_check_export_disk_space", lambda *_args: None)
    output = tmp_path / "result.mp4"
    monkeypatch.setattr(export_module, "_output_path", lambda *_args: output)
    monkeypatch.setattr(export_module, "_media_duration", lambda _path: 2.0)

    def spherical(*_args, **_kwargs):
        calls.append("360")
        output.write_bytes(b"rendered")

    monkeypatch.setattr(export_module, "_render_360_plan", spherical)
    monkeypatch.setattr(export_module, "_render_plan", lambda *_args, **_kwargs: pytest.fail("flat renderer called for 360"))
    ExportStage().run(project, lambda *_args: None)
    assert calls == ["360"]


def test_cache_fingerprints_and_segment_paths_are_platform_specific(tmp_path, monkeypatch):
    project = create_project("Cache isolation", str(tmp_path / "Cache.zuckervid"))
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    record = file_record(str(source))
    record["cache_key"] = "source-key"
    record["probe"] = {"valid_video": True}
    project.data["inputs"]["videos"] = [record]
    segment = {"source_path": str(source), "clip_path": str(source), "clip_start_sec": 0.0, "duration_sec": 3.0}

    fingerprints = {}
    for platform in ("youtube", "reel", "360"):
        project.data["settings"]["wizard"] = {"platform": platform}
        write_artifact_json(artifact_path(project, "coverage.json"), _coverage(platform))
        fingerprints[platform] = EditStage().inputs_fingerprint(project)
    assert len(set(fingerprints.values())) == 3
    assert cached_segment_path(project, segment, "youtube", 4_000_000, {}, {}, False, False) != cached_segment_path(project, segment, "reel", 4_000_000, {}, {}, False, False)

    project.data["settings"]["wizard"] = {"platform": "youtube"}
    before = EditStage().inputs_fingerprint(project)
    monkeypatch.setattr("core.stages.edit.REEL_PLAN_VERSION", 999)
    assert EditStage().inputs_fingerprint(project) == before

    project.data["settings"]["wizard"] = {"platform": "reel"}
    before = EditStage().inputs_fingerprint(project)
    monkeypatch.setattr("core.stages.edit.YOUTUBE_CAMERA_SELECTION_VERSION", 999)
    assert EditStage().inputs_fingerprint(project) == before


def test_spherical_version_does_not_invalidate_reel_fingerprint(tmp_path, monkeypatch):
    project = create_project("Reel spherical cache", str(tmp_path / "Reel.zuckervid"))
    project.data["settings"]["wizard"] = {"platform": "reel"}
    write_artifact_json(artifact_path(project, "coverage.json"), _coverage("reel"))
    before = EditStage().inputs_fingerprint(project)
    monkeypatch.setattr("core.stages.edit.SPHERICAL_MOTION_PLAN_VERSION", 999)
    assert EditStage().inputs_fingerprint(project) == before


@pytest.mark.xfail(strict=False, reason="Known debt: load_edit_plan does not reject an artifact whose platform differs from the active project")
def test_edit_artifact_rejects_wrong_platform(tmp_path):
    project = create_project("Wrong platform", str(tmp_path / "Wrong.zuckervid"))
    project.data["settings"]["wizard"] = {"platform": "youtube"}
    write_artifact_json(artifact_path(project, "edit_plan.json"), {"platform": "reel", "segments": []})
    from core.stages.edit import load_edit_plan

    with pytest.raises(ValueError, match="platform"):
        load_edit_plan(project)


def test_retention_protects_current_exports_across_modes(tmp_path):
    root = tmp_path / "ZuckerVideos"
    projects = root / "Projects"
    projects.mkdir(parents=True)
    current_paths = []
    for name, platform in (("YouTubeReal", "youtube"), ("ReelReal", "reel")):
        folder = projects / f"{name}.zuckervid"
        (folder / "exports").mkdir(parents=True)
        current = folder / "exports" / f"{name}-{platform}.mp4"
        old = folder / "exports" / f"{name}-{platform}-old.mp4"
        current.write_bytes(b"current")
        old.write_bytes(b"old")
        write_artifact_json(folder / "artifacts" / "export_manifest.json", {"exports": [{"path": str(current)}]})
        current_paths.append(current)
    real = projects / "My Real Project.zuckervid"
    (real / "exports").mkdir(parents=True)
    (real / "exports" / "result.mp4").write_bytes(b"real")

    report = build_storage_report(root=root, repo_root=tmp_path / "repo", huggingface_root=tmp_path / "hf")
    result = execute_cleanup(report, trash_root=tmp_path / "Trash")
    moved = {item["path"] for item in result["moved"]}
    assert all(str(path) not in moved for path in current_paths)
    assert str(real) not in moved
