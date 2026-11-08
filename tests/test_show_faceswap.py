"""Put my face on a clip: the swapper's identity matrix is read without extra packages, the
models are downloaded only when the person agrees, and the clip gets the face version
(the original stays, to go back to)."""
import json
import shutil
import struct
import time
from pathlib import Path

import numpy as np
from PIL import Image

from core import show_faceswap, show_proposals, show_scenes
from core.project import create_project
from server.api import create_app


def _varint(value: int) -> bytes:
    out = b""
    while True:
        byte, value = value & 0x7F, value >> 7
        out += bytes([byte | (0x80 if value else 0)])
        if not value:
            return out


def _field(number: int, payload: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def test_the_identity_matrix_is_the_models_last_initializer(tmp_path):
    first = np.arange(6, dtype="<f4").reshape(2, 3)
    last = np.array([[1.5, -2.0], [0.25, 8.0]], dtype="<f4")
    tensor_a = _varint(1 << 3 | 0) + _varint(2) + _varint(1 << 3 | 0) + _varint(3) + _field(8, b"a") + _field(9, first.tobytes())
    tensor_b = _field(1, _varint(2) + _varint(2)) + _varint(2 << 3 | 0) + _varint(1) + _field(8, b"emap") + _field(9, last.tobytes())
    graph = _field(1, b"node") + _field(5, tensor_a) + _field(5, tensor_b) + _field(11, b"input")
    model = _varint(1 << 3 | 0) + _varint(8) + _field(2, b"producer") + _field(7, graph) + _varint(8 << 3 | 0) + _varint(1)
    path = tmp_path / "model.onnx"
    path.write_bytes(model)
    assert np.array_equal(show_faceswap.last_initializer(path), last)


def _project(tmp_path):
    project = create_project("Face", str(tmp_path / "Face.zuckervid"))
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 10, "shots": [], "words": []}))
    clip = project.folder / "assets" / "clips" / "clip-abc.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"original clip")
    item = show_proposals.proposal("clip", 4.0, 1.2, reason="Clip", clip=str(clip), clip_start=0.0, clip_length=1.2)
    item["status"] = "approved"
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [item], resources=[])
    show_scenes.save(project.artifacts_dir / "show_scenes.json", {"identity": {}, "scenes": [
        {"id": "scene-1", "name": "Scene 1", "text": "x", "start": 0, "end": 10, "effects": [item["id"]], "overlays": [], "decision": "accepted", "proposal": ""}]})
    me = project.folder / "assets" / "show-library" / "yours" / "me.png"
    me.parent.mkdir(parents=True)
    Image.new("RGB", (64, 64), (200, 150, 120)).save(me)
    return project, item, clip, me


def _wait(client, proposal_id):
    for _ in range(200):
        job = client.get(f"/api/v1/show/faceswap/{proposal_id}").get_json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError("never finished")


def test_my_face_goes_on_the_clip_after_asking_for_the_models(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project, item, clip, me = _project(tmp_path)
    downloaded, swapped = [], []
    monkeypatch.setattr(show_faceswap, "missing", lambda: [] if downloaded else ["inswapper_128.onnx"])
    monkeypatch.setattr(show_faceswap, "download", lambda progress=None: downloaded.append(True) or (progress and progress(1.0)))

    def fake_swap(photo, source, out, ffmpeg, progress=None):
        swapped.append((Path(photo), Path(source)))
        shutil.copy(source, out)
        out.write_bytes(b"clip with my face")
        return 46, 51
    monkeypatch.setattr(show_faceswap, "swap_clip", fake_swap)
    client = create_app(str(project.folder), dev=True).test_client()

    assert client.get("/api/v1/show/faceswap/models").get_json() == {"missing": True, "size_mb": show_faceswap.SIZE_MB}
    asked = client.post("/api/v1/show/faceswap", json={"proposal_id": item["id"], "photo": str(me.resolve())})
    assert asked.status_code == 409 and asked.get_json()["error"]["code"] == "models_needed" and not downloaded   # never without asking
    assert client.post("/api/v1/show/faceswap", json={"proposal_id": "nope", "photo": str(me.resolve())}).status_code == 404
    assert client.post("/api/v1/show/faceswap", json={"proposal_id": item["id"], "photo": "/etc/hosts", "download_models": True}).status_code == 404

    started = client.post("/api/v1/show/faceswap", json={"proposal_id": item["id"], "photo": str(me.resolve()), "download_models": True})
    assert started.status_code == 202
    job = _wait(client, item["id"])
    assert job["status"] == "done" and downloaded and (job["changed"], job["total"]) == (46, 51)
    assert swapped == [(me.resolve(), clip)]
    params = job["proposal"]["params"]
    assert params["clip_original"] == str(clip) and Path(params["clip"]).read_bytes() == b"clip with my face"
    assert params["face_frames"] == [46, 51]
    stored = show_proposals.load(project.artifacts_dir / "show_proposals.json")
    assert any(resource["path"] == params["clip"] for resource in stored["resources"])
    assert show_scenes.load(project.artifacts_dir / "show_scenes.json")["scenes"][0]["decision"] == "modified"

    back = client.post(f"/api/v1/show/faceswap/{item['id']}/original", json={}).get_json()
    assert back["params"]["clip"] == str(clip) and "clip_original" not in back["params"]
    assert Path(params["clip"]).is_file()                                     # the face version is kept
