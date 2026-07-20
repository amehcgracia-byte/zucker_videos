from __future__ import annotations

import threading
import time

from core.project import create_project, file_record
from core.stages.ingest import prepare_videos


def test_prepare_videos_runs_two_workers_and_aggregates_progress(tmp_path, monkeypatch):
    project = create_project("Parallel", str(tmp_path / "Parallel.zuckervid"))
    project.data["settings"]["ingest"]["proxy_workers"] = 2
    files = []
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        path = tmp_path / name
        path.write_bytes(b"video")
        files.append(path)
    records = [file_record(str(path)) for path in files]
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_normalize(project, record, progress):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        progress(40, "working")
        time.sleep(0.01)
        progress(100, "done")
        with lock:
            active -= 1
        return record.setdefault("normalized", {"path": record["path"]})

    monkeypatch.setattr("core.stages.ingest.normalize_video_record", fake_normalize)
    calls = []

    prepare_videos(project, records, lambda percent, detail: calls.append((percent, detail)))

    assert max_active == 2
    assert calls[-1][0] == 95
    assert any("Preparing 3 videos..." in detail and "a.mp4" in detail for _, detail in calls)
