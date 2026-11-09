"""Exercise the real Stage API, persisted edit plan and FFmpeg review worker."""
import json
import subprocess
import time

from core.project import atomic_write_json, create_project
from core.review_previews import PREFETCH
from core.shot_continuity import continuous_views
from server.api import create_app


def test_reopened_stage_regenerates_only_the_edited_shot(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    video = tmp_path / "camera.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=s=320x180:r=24:d=4", "-c:v", "libx264", "-threads", "1", str(video),
    ], check=True)
    project = create_project("Review", str(tmp_path / "Review.zuckervid"))
    project.data["settings"]["wizard"] = {"platform": "youtube"}
    project.data["stages"]["edit"]["status"] = "done"
    project.data["settings"]["edit"] = {"video_light": {"enabled": True}}
    project.save()
    shots = continuous_views([
        {
            "clip_path": str(video), "source_path": str(video), "clip_start_sec": i,
            "master_start_sec": i, "duration_sec": 1,
            "motion": {"type": "ken_burns", "target_x": .5, "target_y": .5},
        }
        for i in range(3)
    ])
    assert shots[1]["motion"]["continuity_offset_sec"] == 1
    plan = project.artifacts_dir / "edit_plan.json"
    atomic_write_json(plan, {"platform": "youtube", "segments": shots})

    def wait_ready(client):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            response = client.get("/api/v1/wizard/review?render=0")
            assert response.status_code == 200, response.json
            items = response.json["items"]
            assert not any(i["preview_error"] or i["thumbnail_error"] for i in items), items
            if all(i["thumbnail"] and i["preview_ready"] for i in items):
                return items
            time.sleep(.1)
        raise AssertionError(items)

    try:
        client = create_app(str(project.folder), dev=True).test_client()
        assert client.get("/api/v1/wizard/status").json["status"] == "waiting_review"
        assert client.get("/api/v1/wizard/review?render=1").status_code == 200
        before = wait_ready(client)

        # A fresh Flask process has no in-memory wizard job.
        client = create_app(str(project.folder), dev=True).test_client()
        doc = client.get("/api/v1/edit/shot?index=1").json
        response = client.patch("/api/v1/edit/shot", json={
            "project_id": doc["project_id"], "revision": doc["revision"], "index": 1,
            "choice": 0, "edit_component": "framing", "apply_to_source": False,
            "movement": False, "flat_framing": {"zoom": 1.25, "x": .5, "y": .5},
        })
        assert response.status_code == 200, response.json
        saved = json.loads(plan.read_text())["segments"]
        assert saved[0] == shots[0] and saved[2] == shots[2]
        assert client.get("/api/v1/edit/shot?index=1").json["movement"] is False
        assert client.get("/api/v1/wizard/review?render=1&shot=1").status_code == 200
        after = wait_ready(client)
        assert not (project.cache_dir / "color_profiles.json").exists()
        assert after[0] == before[0] and after[2] == before[2]
        assert after[1]["preview"] != before[1]["preview"]
        assert after[1]["thumbnail"] != before[1]["thumbnail"]
        preview = client.get(after[1]["preview"])
        assert preview.status_code == 200 and b"ftyp" in preview.data[:16]
    finally:
        PREFETCH.stop()
