"""Show Studio served by Zucker for the open project; its work saved in the project."""
import io

import pytest

import server.api as api_module
from core.project import create_project


def _client(tmp_path, monkeypatch, origins=""):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("ZUCKER_STUDIO_ORIGINS", origins)
    project = create_project("Show", str(tmp_path / "Studio.zuckervid"))
    return project, api_module.create_app(project_path=str(project.folder)).test_client()


def _upload(client, name, data, origin=None):
    headers = {"Origin": origin} if origin else {}
    response = client.post("/api/v1/library/import", data={"file": (io.BytesIO(data), name), "origin": "Show pilot excerpt"},
                           content_type="multipart/form-data", headers=headers)
    assert response.status_code == 201, response.get_json()
    return response


RECIPE = {"version": 2, "mode": "mixed", "events": [
    {"id": "base", "type": "video", "start": 0, "end": 44, "source": "original", "trackId": "base"},
    {"id": "finger-a", "type": "finger", "start": 3.2, "end": 5.0, "parent": "base"}]}


def test_the_editor_is_served_with_its_project_store_and_public_samples(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    page = client.get("/show-studio/").get_data(as_text=True)
    assert "window.ZUCKER_STUDIO={project:true}" in page and page.index("studio-store.js") < page.index("paint-tools.js")
    assert client.get("/show-studio/timeline-model.js").status_code == 200
    assert client.get("/show-studio/backgrounds/manifest.json").status_code == 200
    assert client.get("/show-studio/../../app.py").status_code == 404
    assert client.get("/show-studio/original.mp4").status_code == 404                 # nothing prepared yet: said, not faked


def test_the_project_keeps_the_recipe_the_sources_and_the_tracking(tmp_path, monkeypatch):
    project, client = _client(tmp_path, monkeypatch)
    assert client.get("/api/v1/show-studio/state").get_json()["composition"] is None
    video = _upload(client, "original.mp4", b"fake mp4 bytes for the pilot").get_json()
    assert client.put("/api/v1/show-studio/sources", json={"sources": {"original": video["id"]}}).status_code == 200
    served = client.get("/show-studio/original.mp4", headers={"Range": "bytes=0-3"})
    assert served.status_code == 206 and served.data == b"fake"
    thumb = _upload(client, "coffee.jpg", b"\xff\xd8 coffee frame").get_json()
    client.put("/api/v1/show-studio/sources", json={"sources": {"thumb-coffee": thumb["id"]}})
    assert client.get("/show-studio/scene-thumbs/coffee.jpg").data == b"\xff\xd8 coffee frame"
    assert client.get("/show-studio/scene-thumbs/../project.json").status_code == 404
    assert client.put("/api/v1/show-studio/analysis/finger-track", json=[[0, 0.5, 0.5]]).status_code == 200
    assert client.get("/show-studio/finger-track.json").get_json() == [[0, 0.5, 0.5]]
    saved = client.put("/api/v1/show-studio/composition", json={"recipe": RECIPE, "revision": 0}).get_json()
    assert saved["revision"] == 1
    stale = client.put("/api/v1/show-studio/composition", json={"recipe": RECIPE, "revision": 0})
    assert stale.status_code == 409 and stale.get_json()["error"]["code"] == "stale_revision"
    state = client.get("/api/v1/show-studio/state").get_json()
    assert state["composition"]["events"][1]["id"] == "finger-a" and state["source_urls"]["original"].endswith(video["id"])
    assert (project.folder / "show_studio" / "composition.json").is_file()


def test_own_masks_reference_library_files(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    photo = _upload(client, "bill.jpg", b"\xff\xd8 a photo").get_json()
    saved = client.put("/api/v1/show-studio/materials", json={"materials": [{"id": "m-1", "kind": "mask", "title": "Bill", "asset": photo["id"]}]})
    assert saved.status_code == 200 and saved.get_json()["material_urls"]["m-1"] == f"/api/v1/library/assets/{photo['id']}"
    assert client.get(f"/api/v1/library/assets/{photo['id']}").data == b"\xff\xd8 a photo"
    assert client.put("/api/v1/show-studio/materials", json={"materials": [{"id": "m-2", "kind": "mask", "asset": "a" * 64}]}).status_code == 400


@pytest.mark.parametrize("origins, origin, allowed", [("", "http://127.0.0.1:8792", False),
                                                      ("http://127.0.0.1:8792", "http://127.0.0.1:8792", True),
                                                      ("http://127.0.0.1:8792", "http://evil.example", False)])
def test_only_the_listed_prototype_address_may_copy_its_work_in(tmp_path, monkeypatch, origins, origin, allowed):
    _, client = _client(tmp_path, monkeypatch, origins)
    preflight = client.open("/api/v1/show-studio/composition", method="OPTIONS", headers={"Origin": origin, "Access-Control-Request-Method": "PUT"})
    assert (preflight.status_code == 204) is allowed
    response = client.put("/api/v1/show-studio/composition", json={"recipe": RECIPE, "revision": 0}, headers={"Origin": origin})
    assert (response.headers.get("Access-Control-Allow-Origin") == origin) is allowed
    other = client.get("/api/v1/projects", headers={"Origin": origin})
    assert "Access-Control-Allow-Origin" not in other.headers                        # never the rest of the app


def test_a_website_cannot_post_files_into_the_open_project(tmp_path, monkeypatch):
    project, client = _client(tmp_path, monkeypatch, "http://127.0.0.1:8792")
    forged = client.post("/api/v1/library/import", data={"file": (io.BytesIO(b"junk"), "x.png")}, content_type="multipart/form-data",
                         headers={"Origin": "https://some-website.example"})
    assert forged.status_code == 403 and not (project.folder / "library" / "manifest.json").exists()
    own = client.post("/api/v1/library/import", data={"file": (io.BytesIO(b"mine"), "x.png")}, content_type="multipart/form-data",
                      headers={"Origin": "http://localhost"})
    assert own.status_code == 201                                                   # the app's own page


def test_in_development_the_studio_policy_is_not_widened(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("ZUCKER_STUDIO_ORIGINS", "http://127.0.0.1:8796")
    project = create_project("Show", str(tmp_path / "Studio.zuckervid"))
    client = api_module.create_app(project_path=str(project.folder), dev=True).test_client()
    preflight = client.open("/api/v1/show-studio/composition", method="OPTIONS",
                            headers={"Origin": "http://127.0.0.1:8796", "Access-Control-Request-Method": "PUT"})
    assert preflight.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:8796" and "PUT" in preflight.headers["Access-Control-Allow-Methods"]
