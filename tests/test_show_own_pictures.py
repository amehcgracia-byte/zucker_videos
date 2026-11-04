"""A picture dropped from the computer is kept in the project's library and can go
straight onto a scene; a plain photo can have its subject cut out."""
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from core import show_proposals, show_scenes
from core.project import create_project
from server.api import create_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Own", str(tmp_path / "Own.zuckervid"))
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 10, "shots": [], "words": []}))
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [])
    show_scenes.save(project.artifacts_dir / "show_scenes.json", {"identity": {}, "scenes": [
        {"id": "scene-1", "name": "Scene 1", "text": "hi", "start": 0, "end": 10, "effects": [], "overlays": [], "decision": "pending", "proposal": ""}]})
    return project, create_app(str(project.folder), dev=True).test_client()


def _png(image, kind="PNG"):
    buffer = io.BytesIO()
    image.save(buffer, kind)
    return buffer.getvalue()


def test_a_dropped_picture_is_kept_and_placed_and_a_photo_can_be_cut_out(tmp_path, monkeypatch):
    project, client = _client(tmp_path, monkeypatch)
    cut = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    cut.paste((250, 40, 40, 255), (50, 20, 150, 80))
    sent = client.post("/api/v1/show/library/pictures", data={"file": (io.BytesIO(_png(cut)), "my_potato.png")}).get_json()
    assert sent["cutout"] is True and sent["name"] == "My potato" and (sent["width"], sent["height"]) == (200, 100)
    assert Path(sent["path"]).parent == (project.folder / "assets" / "show-library" / "yours").resolve()
    again = client.post("/api/v1/show/library/pictures", data={"file": (io.BytesIO(_png(cut)), "other name.png")}).get_json()
    assert again["path"] == sent["path"]                                        # the same picture is not kept twice
    library = {item["path"]: item for item in client.get("/api/v1/show/library").get_json()["items"]}
    assert library[sent["path"]]["origin"] == "Your picture" and not library[sent["path"]]["retired"]

    from core import show_characters
    mask = np.zeros((120, 160), np.float32)
    mask[30:90, 40:120] = 1.0
    worked = []
    monkeypatch.setattr(show_characters, "auto_mask", lambda path, box=None: worked.append(path) or (mask, "macos-vision"))
    photo = Image.new("RGB", (160, 120), (30, 90, 200))
    plain = client.post("/api/v1/show/library/pictures", data={"file": (io.BytesIO(_png(photo, "JPEG")), "beach.jpg")}).get_json()
    assert plain["cutout"] is False
    subject = client.post("/api/v1/show/library/pictures/cutout", json={"path": plain["path"]}).get_json()
    assert subject["cutout"] is True and (subject["width"], subject["height"]) == (80, 60) and subject["method"] == "macos-vision"
    assert worked == [plain["path"]]                                            # worked out once, when the photo arrived
    library = {item["path"]: item for item in client.get("/api/v1/show/library").get_json()["items"]}
    assert library[subject["path"]]["origin"] == "Your picture · cut out by macOS"
    assert library[plain["path"]]["origin"] == "Your picture"                   # the photo itself stays as it was

    placed = client.post("/api/v1/show/scenes/scene-1/overlays", json={"overlays": [
        {"id": "a", "asset": subject["path"], "x": 10, "y": 10, "width": 22, "at": 2, "duration": 3, "effect": "pop"}]})
    assert placed.status_code == 200 and placed.get_json()["overlays"][0]["asset"] == subject["path"]

    assert client.post("/api/v1/show/library/pictures", data={"file": (io.BytesIO(b"hello"), "notes.txt")}).status_code == 400
    assert client.post("/api/v1/show/library/pictures", data={"file": (io.BytesIO(b"not a picture"), "fake.png")}).status_code == 400
    assert client.post("/api/v1/show/library/pictures/cutout", json={"path": "/etc/hosts"}).status_code == 404


def test_green_import_keeps_original_and_removal_can_keep_canvas(tmp_path, monkeypatch):
    project,client=_client(tmp_path,monkeypatch)
    photo=Image.new('RGB',(120,100),(0,230,0));photo.paste((230,20,30),(30,20,90,80))
    response=client.post('/api/v1/show/library/pictures',data={'file':(io.BytesIO(_png(photo)),'green.png')})
    assert response.status_code==200,response.get_json()
    made=response.get_json();assert made['cutout'] and Path(made['original']).is_file()
    pixels=np.asarray(Image.open(made['path']))
    assert pixels.shape==(100,120,4) and pixels[0,0,3]==0 and pixels[40,40,3]==255
    removed=client.post('/api/v1/show/library/pictures/cutout',json={'path':made['original'],'keep_canvas':True})
    assert removed.status_code==200,removed.get_json()
    assert (removed.get_json()['width'],removed.get_json()['height'])==(120,100)


def test_oversized_picture_keeps_offscreen_position(tmp_path,monkeypatch):
    project,client=_client(tmp_path,monkeypatch)
    picture=Path(client.post('/api/v1/show/library/pictures',data={'file':(io.BytesIO(_png(Image.new('RGBA',(20,20),'red'))),'big.png')}).get_json()['path'])
    item=dict(id='large',asset=str(picture),x=-130,y=-40,width=360,at=0,duration=3)
    response=client.post('/api/v1/show/scenes/scene-1/overlays',json={'overlays':[item]})
    assert response.status_code==200,response.get_json()
    saved=response.get_json()['overlays'][0]
    assert (saved['x'],saved['y'],saved['width'])==(-130,-40,360)


def test_edited_library_copy_preserves_original(tmp_path,monkeypatch):
    project,client=_client(tmp_path,monkeypatch)
    original=client.post('/api/v1/show/library/pictures',data={'file':(io.BytesIO(_png(Image.new('RGBA',(120,80),'red'))),'red.png')}).get_json()['path']
    before=Path(original).read_bytes()
    response=client.post('/api/v1/show/library/pictures/edited-copy',json={'path':original,'crop':[.25,0,.25,0],'rotate':90})
    assert response.status_code==201,response.get_json()
    assert Image.open(response.get_json()['path']).size==(80,60)
    assert Path(original).read_bytes()==before
    assert client.post('/api/v1/show/library/pictures/edited-copy',json={'path':'/etc/hosts'}).status_code==400


def test_library_file_accepts_equivalent_path_but_not_unlisted_file(tmp_path, monkeypatch):
    project, client = _client(tmp_path, monkeypatch)
    folder = project.folder / "assets" / "show-library"
    folder.mkdir(parents=True, exist_ok=True)
    picture = folder / "example.png"
    Image.new("RGB", (20, 20), "red").save(picture)
    equivalent = str(folder / ".." / "show-library" / "example.png")
    assert client.get("/api/v1/show/library/file", query_string={"path": equivalent}).status_code == 200
    outside = tmp_path / "private.png"
    Image.new("RGB", (20, 20), "blue").save(outside)
    assert client.get("/api/v1/show/library/file", query_string={"path": str(outside)}).status_code == 404
