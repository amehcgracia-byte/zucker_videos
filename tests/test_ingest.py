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


def test_reel_single_source_does_not_run_multicamera_analysis(monkeypatch, tmp_path):
    from core.stages.ingest import IngestStage

    project = create_project("Single Reel", str(tmp_path / "Single Reel.zuckervid"))
    source = tmp_path / "take.mp4"
    source.write_bytes(b"video")
    record = file_record(str(source))
    project.data["inputs"]["videos"] = [record]
    project.data["settings"].setdefault("wizard", {})["platform"] = "reel"

    monkeypatch.setattr("core.stages.ingest.ffprobe", lambda _path: {
        "format": {"duration": "30"},
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "r_frame_rate": "30/1", "pix_fmt": "yuv420p"}],
    })
    monkeypatch.setattr("core.stages.ingest.normalize_video_record", lambda _project, item, _progress: item.setdefault("normalized", {"path": item["path"]}))
    monkeypatch.setattr("core.stages.ingest.analyze_reel_framing_records", lambda *_args: (_ for _ in ()).throw(AssertionError("single-source framing should be skipped")))
    monkeypatch.setattr("core.stages.ingest.analyze_operator_presence", lambda *_args: (_ for _ in ()).throw(AssertionError("single-source operator pass should be skipped")))

    IngestStage().run(project, lambda *_args: None)


def test_analysis_overlaps_other_proxies_without_duplicate_passes(tmp_path, monkeypatch):
    project=create_project('Overlap',str(tmp_path/'overlap.zuckervid'))
    project.data['settings']['ingest']['proxy_workers']=2
    records=[{'path':str(tmp_path/f'{index}.mp4')} for index in range(2)]
    slow_proxy_started=threading.Event()
    analysis_started=threading.Event()
    observed=[]
    def normalize(project,record,callback):
        if record is records[1]:
            slow_proxy_started.set()
            assert analysis_started.wait(3),'Analysis waited for every proxy'
        else:
            assert slow_proxy_started.wait(3)
        record['normalized']={'path':record['path']}
        callback(100,'Proxy ready')
    def framing(items,callback):
        assert all('normalized' in item for item in items)
        observed.append(('framing',items[0]['path']))
        analysis_started.set()
    def operator(items,callback):
        observed.append(('operator',items[0]['path']))
    monkeypatch.setattr('core.stages.ingest.normalize_video_record',normalize)
    monkeypatch.setattr('core.stages.ingest.analyze_reel_framing_records',framing)
    monkeypatch.setattr('core.stages.ingest.analyze_operator_presence',operator)
    updates=[]
    prepare_videos(project,records,lambda percent,detail:updates.append((percent,detail)),analyze_ready=True)
    assert len(observed)==4 and len(set(observed))==4
    assert updates[-1][0]==95
    assert all(percent<95 for percent,detail in updates if detail.task['id'].startswith('proxy-'))
