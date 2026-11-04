from __future__ import annotations

import json
import pytest

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


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_atomic_write_retries_temporary_windows_sharing_denial(tmp_path, monkeypatch, winerror):
    import core.project as project_module
    path = tmp_path / "project.json"
    path.write_text('{"value":"old"}', encoding="utf-8")
    replace = project_module.os.replace
    calls, delays = [], []

    def busy_then_replace(source, destination):
        calls.append(source)
        if len(calls) < 3:
            assert json.loads(path.read_text(encoding="utf-8")) == {"value": "old"}
            error = PermissionError("Destination is being read")
            error.winerror = winerror
            raise error
        replace(source, destination)

    monkeypatch.setattr(project_module.os, "replace", busy_then_replace)
    monkeypatch.setattr(project_module.time, "sleep", delays.append)
    atomic_write_json(path, {"value": "Simón 日本"})
    assert len(calls) == 3 and len(set(calls)) == 1
    assert delays == [.025, .05]
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": "Simón 日本"}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("winerror,attempts", [(5, 6), (None, 1), (112, 1)])
def test_atomic_write_reports_persistent_or_unrelated_denial_without_losing_old_file(tmp_path, monkeypatch, winerror, attempts):
    import core.project as project_module
    path = tmp_path / "project.json"
    original = b'{"value":"old"}'
    path.write_bytes(original)
    calls, delays = [], []
    error = PermissionError("Cannot replace destination")
    if winerror is not None:
        error.winerror = winerror

    def deny(source, destination):
        calls.append(source)
        raise error

    monkeypatch.setattr(project_module.os, "replace", deny)
    monkeypatch.setattr(project_module.time, "sleep", delays.append)
    with pytest.raises(PermissionError) as caught:
        atomic_write_json(path, {"value": "new"})
    assert caught.value is error
    assert len(calls) == attempts and len(delays) == attempts - 1
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
