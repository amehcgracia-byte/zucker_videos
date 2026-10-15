"""Show Studio state kept in the project: the editor's recipe, the person's own
backgrounds and masks, the source media and tracking data; never in the browser only."""
import io
import json

import pytest

from core.project import create_project
from core.show_studio_store import StaleRevision, StudioStore


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Show", str(tmp_path / "Studio.zuckervid"))
    return project, StudioStore(project.folder)


def _recipe(*events):
    return {"version": 2, "mode": "mixed", "duration": 44.0, "events": list(events) or [
        {"id": "base", "type": "video", "start": 0, "end": 44, "source": "original", "trackId": "base"},
        {"id": "finger-a", "type": "finger", "start": 3.2, "end": 5.0, "parent": "base"},
        {"id": "cut-1", "type": "cut", "start": 10, "end": 12.5, "parent": "base"}]}


def test_the_recipe_is_saved_in_the_project_with_a_revision_and_a_stale_save_is_refused(tmp_path, monkeypatch):
    project, store = _store(tmp_path, monkeypatch)
    assert store.state()["composition"] is None and store.state()["revision"] == 0
    first = store.save_composition(_recipe(), revision=0)
    assert first == 1 and (project.folder / "show_studio" / "composition.json").is_file()
    state = StudioStore(project.folder).state()                                   # read back from disk
    assert [event["id"] for event in state["composition"]["events"]] == ["base", "finger-a", "cut-1"] and state["revision"] == 1
    with pytest.raises(StaleRevision):                                            # another window saved meanwhile
        store.save_composition(_recipe(), revision=0)
    assert store.save_composition(_recipe(), revision=1) == 2


@pytest.mark.parametrize("bad", [
    {"version": 1, "events": []},                                                  # an old recipe
    {"version": 2, "events": [{"id": "x", "type": "cut", "start": 5, "end": 2}]},   # ends before it starts
    {"version": 2, "events": [{"type": "cut", "start": 1, "end": 2}]},             # no id
    {"version": 2, "events": [{"id": "x", "type": "cut", "start": float("nan"), "end": 2}]},
    {"version": 2, "events": [{"id": "bg", "type": "image", "start": 0, "end": 1, "background": {"asset": "f" * 64}}]},  # unknown file
])
def test_a_broken_recipe_is_refused_and_nothing_is_written(tmp_path, monkeypatch, bad):
    project, store = _store(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        store.save_composition(bad, revision=0)
    assert not (project.folder / "show_studio" / "composition.json").exists()


def test_own_backgrounds_and_masks_point_at_verified_library_files(tmp_path, monkeypatch):
    project, store = _store(tmp_path, monkeypatch)
    photo = store.library.import_stream(io.BytesIO(b"\x89PNG fake picture"), "plaza.png", origin="User reference photo")
    materials = [{"id": "m-1", "kind": "mask", "title": "Bill", "asset": photo["id"], "polygon": [[0, 0], [1, 0], [1, 1]]},
                 {"id": "b-1", "kind": "background", "title": "Plaza", "asset": photo["id"]}]
    store.save_materials(materials)
    state = StudioStore(project.folder).state()
    assert [item["id"] for item in state["materials"]] == ["m-1", "b-1"] and state["materials"][0]["polygon"][2] == [1, 1]
    assert store.asset_path(photo["id"]).read_bytes() == b"\x89PNG fake picture"
    with pytest.raises(ValueError):
        store.save_materials([{"id": "m-2", "kind": "mask", "asset": "0" * 64}])  # not in the library
    with pytest.raises(ValueError):
        store.save_materials([{"id": "m-3", "kind": "hat", "asset": photo["id"]}])
    edited_sample = {"id": "stained-glass", "kind": "mask", "builtin": "stained-glass.png", "polygon": [[0, 0], [1, 0], [0.5, 1]]}
    assert store.save_materials([edited_sample])[0]["builtin"] == "stained-glass.png"   # a sample's own outline, edited
    with pytest.raises(ValueError):
        store.save_materials([{**edited_sample, "builtin": "../../project.json"}])
    with pytest.raises(ValueError):
        store.asset_path("../../project.json")


def test_sources_and_tracking_belong_to_the_project(tmp_path, monkeypatch):
    project, store = _store(tmp_path, monkeypatch)
    video = store.library.import_stream(io.BytesIO(b"fake mp4 bytes"), "original.mp4", origin="Show pilot excerpt")
    store.save_sources({"original": video["id"]})
    assert store.state()["sources"]["original"] == video["id"]
    with pytest.raises(ValueError):
        store.save_sources({"../x": video["id"]})
    store.save_analysis("finger-track", {"fps": 25, "points": [[0, 0.5, 0.5]]})
    assert store.load_analysis("finger-track")["fps"] == 25 and store.state()["analysis"] == ["finger-track"]
    store.save_analysis("captions", [[0.0, 1.2, "hello"]])                       # a list stays a list
    assert store.load_analysis("captions") == [[0.0, 1.2, "hello"]]
    with pytest.raises(ValueError):
        store.save_analysis("secrets", {"x": 1})
    with pytest.raises(ValueError):
        store.save_analysis("captions", {"x": "y" * (9 * 1024 * 1024)})            # too big for tracking data


def test_each_source_section_keeps_its_own_edit_sources_and_tracking(tmp_path, monkeypatch):
    project, store = _store(tmp_path, monkeypatch)
    opening = StudioStore(project.folder, section="opening")
    video = store.library.import_stream(io.BytesIO(b"opening video"), "original.mp4")
    assert opening.save_composition(_recipe(), revision=0) == 1
    assert store.state()["composition"] is None                                   # the workshop is untouched
    opening.save_sources({"original": video["id"]})
    opening.save_analysis("captions", {"captions": []})
    assert store.state()["sources"] == {} and store.state()["analysis"] == []
    assert (project.folder / "show_studio" / "sections" / "opening" / "composition.json").is_file()
    store.save_materials([{"id": "b", "kind": "background", "asset": video["id"]}])
    assert opening.state()["materials"][0]["id"] == "b"                           # own pictures serve every section
    with pytest.raises(ValueError):
        StudioStore(project.folder, section="../evil")
