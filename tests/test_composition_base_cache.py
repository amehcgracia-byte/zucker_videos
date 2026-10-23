import json
import os
from server.api import _refresh_composition_base


def test_resume_cached_base_preserves_provenance(tmp_path):
    source = tmp_path / "original.mp4"
    cache = tmp_path / "composition-base.mp4"
    meta = tmp_path / "composition-base.json"
    source.write_bytes(b"clean original")
    assert not _refresh_composition_base(source, cache, meta)
    provenance = meta.read_bytes()
    assert _refresh_composition_base(cache, cache, meta)
    assert cache.read_bytes() == b"clean original"
    assert meta.read_bytes() == provenance
    alias = tmp_path / "alias.mp4"
    os.link(cache, alias)
    assert _refresh_composition_base(alias, cache, meta)
    assert meta.read_bytes() == provenance


def test_changed_base_and_broken_metadata_refresh(tmp_path):
    source = tmp_path / "original.mp4"
    cache = tmp_path / "composition-base.mp4"
    meta = tmp_path / "composition-base.json"
    source.write_bytes(b"first")
    assert not _refresh_composition_base(source, cache, meta)
    assert _refresh_composition_base(source, cache, meta)
    source.write_bytes(b"new longer edit")
    assert not _refresh_composition_base(source, cache, meta)
    assert cache.read_bytes() == source.read_bytes()
    meta.write_text("invalid json")
    assert not _refresh_composition_base(source, cache, meta)
    assert json.loads(meta.read_text())["source"] == str(source)


def test_a_new_render_wins_over_the_private_copy_of_an_old_one(tmp_path, monkeypatch):
    # «La prueba», 23 October: the final video was composed over 19 October's edit.
    from core.project import create_project
    from server.api import _composition_base_result
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Prueba", str(tmp_path / "Prueba.zuckervid"))
    old = project.exports_dir / "Prueba-show-20261019-025935.mp4"
    project.exports_dir.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"19 October edit")
    cache, meta = project.cache_dir / "composition-base.mp4", project.cache_dir / "composition-base.json"
    project.cache_dir.mkdir(parents=True, exist_ok=True)
    _refresh_composition_base(old, cache, meta)
    old.unlink()
    new = project.exports_dir / "Prueba-show-20261023-174200.mp4"
    new.write_bytes(b"23 October edit, longer")
    manifest = project.artifacts_dir / "export_manifest.json"

    def point_at(path):
        manifest.write_text(json.dumps({"platform": "show", "exports": [{"path": str(path), "platform": "show"}]}))
        project.data["stages"]["export"].update(status="done", outputs={"export_manifest": str(manifest)})
        project.save()

    point_at(new)
    assert _composition_base_result(project)["path"] == str(new)        # the new render, not the old copy
    assert not _refresh_composition_base(new, cache, meta) and cache.read_bytes() == new.read_bytes()
    composed = project.exports_dir / "Prueba-show-20261023-174200_composed-captions.mp4"
    composed.write_bytes(b"final with captions")
    new.unlink()
    point_at(composed)
    assert _composition_base_result(project)["path"] == str(cache)      # captions again: from its clean copy
