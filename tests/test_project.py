from __future__ import annotations

import json

from core.project import atomic_write_json, create_project, load_project


def test_project_json_roundtrip(tmp_path):
    folder = tmp_path / "MyJam.zuckervid"
    project = create_project("MyJam", str(folder))
    project.data["settings"]["sync"]["confidence_threshold"] = 0.8
    project.save()

    loaded = load_project(str(folder))

    assert loaded.data["name"] == "MyJam"
    assert loaded.data["schema_version"] == 1
    assert loaded.data["settings"]["sync"]["confidence_threshold"] == 0.8
    assert (folder / "inputs" / "videos").is_dir()
    assert (folder / "cache" / "logs").is_dir()


def test_atomic_write_preserves_existing_file_when_replace_not_reached(tmp_path, monkeypatch):
    path = tmp_path / "project.json"
    path.write_text('{"value": "old"}\n', encoding="utf-8")

    def fail_replace(src, dst):
        raise RuntimeError("simulated crash before replace")

    monkeypatch.setattr("core.project.os.replace", fail_replace)

    try:
        atomic_write_json(path, {"value": "new"})
    except RuntimeError:
        pass

    assert json.loads(path.read_text(encoding="utf-8")) == {"value": "old"}
