"""The narrator's track editor: zooms reframed, moved, extended and removed without changing the timing."""
import json
import subprocess

import numpy as np
import pytest

from core import show_narrator
from core.ffmpeg import tool_status
from core.project import create_project
from core.show_camera import camera_at
from core.show_framing import frame_centre
from core.show_render import kept_ranges
from server.api import create_app

FPS = 30


def _plan():
    words = [{"index": i, "text": f"w{i}", "start": i * 0.5 + 0.1, "end": i * 0.5 + 0.4, "removed": None} for i in range(12)]
    return {"fps": FPS, "source": "", "words": words, "shots": [
        {"start_frame": 0, "end_frame": 60, "start": 0.0, "end": 2.0, "zoom": 1.0, "focus": [0.5, 0.42], "words": [0, 1, 2, 3], "cut_before": "start"},
        {"start_frame": 60, "end_frame": 120, "start": 2.0, "end": 4.0, "zoom": 1.65, "focus": [0.4, 0.4], "face": [0.4, 0.3],
         "words": [4, 5, 6, 7], "cut_before": "pace"},
        {"start_frame": 150, "end_frame": 180, "start": 5.0, "end": 6.0, "zoom": 1.0, "focus": [0.5, 0.42], "words": [10, 11], "cut_before": "silence"},
    ]}


def test_the_track_lists_cuts_and_one_segment_per_zoom():
    track = show_narrator.track(_plan(), [{"id": "s1", "start": 0, "end": 5, "name": "Hello"}])
    assert track["duration"] == 5.0
    assert track["cuts"] == [{"at": 4.0, "removed": 1.0, "reason": "silence", "shot": 2}]
    assert [(s["start"], s["end"], s["zoom"]) for s in track["segments"]] == [(0.0, 2.0, 1.0), (2.0, 4.0, 1.65), (4.0, 5.0, 1.0)]
    assert track["shots"][1]["text"] == "w4 w5 w6 w7" and track["scenes"][0]["title"] == "Hello"


def test_framing_a_stretch_inside_shots_splits_them_and_keeps_the_timing():
    plan = _plan()
    ranges = kept_ranges(plan)
    show_narrator.frame(plan, 1.0, 3.0, zoom=1.3, face=[0.6, 0.35], move="glide")
    assert kept_ranges(plan) == ranges                                    # the same frames, in the same order
    assert [(s["start_frame"], s["end_frame"]) for s in plan["shots"]] == [(0, 30), (30, 60), (60, 90), (90, 120), (150, 180)]
    assert plan["shots"][1]["move"] == "glide" and "move" not in plan["shots"][2]
    assert plan["shots"][1]["zoom"] == plan["shots"][2]["zoom"] == 1.3 and plan["shots"][2]["aimed"]
    assert plan["shots"][3]["zoom"] == 1.65                               # after the stretch: as it was
    assert plan["shots"][0]["words"] == [0, 1] and plan["shots"][1]["words"] == [2, 3]
    segments = show_narrator.segments(plan)
    assert [(s["start"], s["end"], s["zoom"], s["move"]) for s in segments][:3] == [(0.0, 1.0, 1.0, "cut"), (1.0, 3.0, 1.3, "glide"), (3.0, 4.0, 1.65, "cut")]


def test_a_push_over_several_shots_keeps_one_pace():
    plan = _plan()
    show_narrator.frame(plan, 0.0, 4.0, zoom=1.0, zoom_end=1.6)
    zooms = [camera_at(plan, n)[0] for n in range(0, 120)]
    steps = np.diff(zooms)
    assert zooms[0] == pytest.approx(1.0) and zooms[-1] == pytest.approx(1.6, abs=1e-3)
    assert steps.min() > 0 and steps.max() - steps.min() < 1e-3


def test_a_zoom_moved_and_extended_takes_its_framing_and_leaves_wide():
    plan = _plan()
    show_narrator.move(plan, (2.0, 4.0), (0.5, 3.0))
    assert camera_at(plan, 20)[0] == 1.65 and camera_at(plan, 89)[0] == 1.65
    assert camera_at(plan, 95)[0] == 1.0 and camera_at(plan, 10)[0] == 1.0
    assert camera_at(plan, 20)[1:] == pytest.approx(frame_centre((0.4, 0.3), 1.65), abs=1e-4)   # the moved zoom's aim
    assert not any(shot.get("aimed") for shot in plan["shots"])          # found, not chosen by hand
    assert show_narrator.changed(_plan(), plan) == pytest.approx((0.5, 4.0))


def test_undo_and_redo(tmp_path):
    plan, stack = _plan(), tmp_path / "undo.json"
    before = json.loads(json.dumps(plan))
    show_narrator.remember(stack, before)
    show_narrator.frame(plan, 2.0, 4.0, zoom=1.0)
    assert show_narrator.step(stack, plan, "undo") and plan["shots"] == before["shots"]
    assert show_narrator.step(stack, plan, "redo") and plan["shots"][1]["zoom"] == 1.0
    assert not show_narrator.step(stack, plan, "redo")


def test_the_editor_api_frames_undoes_and_draws_the_filmstrip(tmp_path, monkeypatch):
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Narrator", str(tmp_path / "Narrator.zuckervid"))
    source = tmp_path / "talk.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=6", "-f", "lavfi", "-i", "sine=f=300:d=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
    plan = {**_plan(), "source": str(source)}
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps(plan))
    scenes = [{"id": "scene-01", "start": 0.0, "end": 2.5, "decision": "accepted", "effects": [], "overlays": []},
              {"id": "scene-02", "start": 2.5, "end": 5.0, "decision": "accepted", "effects": [], "overlays": []}]
    (project.artifacts_dir / "show_scenes.json").write_text(json.dumps({"version": 2, "identity": {}, "intro": None, "scenes": scenes}))
    (project.artifacts_dir / "show_proposals.json").write_text(json.dumps({"version": 1, "proposals": []}))
    client = create_app(str(project.folder)).test_client()
    track = client.get("/api/v1/show/narrator").get_json()
    assert len(track["segments"]) == 3 and not track["can_undo"]
    done = client.post("/api/v1/show/narrator/framing", json={"op": "frame", "start": 0.5, "end": 1.5, "zoom": 1.65, "face": [0.5, 0.3], "backup": True})
    assert done.status_code == 200, done.get_json()
    assert done.get_json()["can_undo"] and len(done.get_json()["segments"]) == 5
    assert done.get_json()["changed_scenes"] == ["scene-01"]              # only the scene whose picture changed
    decisions = {s["id"]: s["decision"] for s in json.loads((project.artifacts_dir / "show_scenes.json").read_text())["scenes"]}
    assert decisions == {"scene-01": "accepted", "scene-02": "accepted"}   # framing on purpose: still decided
    assert (project.artifacts_dir / "history").is_dir()
    undone = client.post("/api/v1/show/narrator/framing", json={"op": "undo"}).get_json()
    assert len(undone["segments"]) == 3
    assert client.post("/api/v1/show/narrator/framing", json={"op": "frame", "start": 3, "end": 3}).status_code == 400
    strip = client.get("/api/v1/show/filmstrip?start=0&end=5&count=5&h=36")
    assert strip.status_code == 200 and strip.mimetype == "image/jpeg"
    import cv2
    picture = cv2.imdecode(np.frombuffer(strip.data, np.uint8), cv2.IMREAD_COLOR)
    assert picture.shape[:2] == (36, 64 * 5)
    peaks = client.get("/api/v1/show/narrator/waveform?buckets=50").get_json()["peaks"]
    assert len(peaks) == 50 and max(peaks) == 1.0
