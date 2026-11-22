"""Regression: cold Stage hover never blocks the private HTTP request.

These tests do not create a real FFmpeg process or touch any user project.
The existing ffmpeg-backed review tests remain responsible for codec parity.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from core import review_previews
from core.project import create_project
from core.shot_review import review_segment_for_index


def test_hover_queue_is_singleflight_bounded_and_returns_before_render(tmp_path, monkeypatch):
    project = create_project("Hover", str(tmp_path/"hover.zuckervid"))
    output = tmp_path/"cached-preview.mp4"
    started = threading.Event()
    release = threading.Event()
    called = []

    def slow_render(p, index, segment):
        called.append((index,segment["name"]))
        started.set()
        assert release.wait(3), "background test render should be released"
        output.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return output

    monkeypatch.setattr(review_previews, "render_preview", slow_render)
    jobs = review_previews.HoverPreviewQueue(max_waiting=1)
    try:
        beginning = time.perf_counter()
        assert jobs.request(project, 0, {"name":"shot 0"}, output) == "preparing"
        assert time.perf_counter() - beginning < .5, "HTTP thread must not await render"
        assert started.wait(1)
        assert jobs.request(project, 0, {"name":"shot 0"}, output) == "preparing"
        assert jobs.request(project, 1, {"name":"shot 1"}, tmp_path/"other.mp4") == "busy"
        assert len(called) == 1
        release.set()
        for _ in range(50):
            if jobs.request(project, 0, {"name":"shot 0"}, output) == "ready":
                break
            time.sleep(.01)
        assert jobs.request(project, 0, {"name":"shot 0"}, output) == "ready"
        assert len(called) == 1, "singleflight avoids duplicate encodes"
    finally:
        release.set()
        jobs.stop()
        jobs._pool.shutdown(wait=True)


def test_single_stage_segment_matches_full_review_and_skips_other_304(tmp_path, monkeypatch):
    from core import shot_review
    project = create_project("305 shots", str(tmp_path/"305.zuckervid"))
    project.data["settings"].setdefault("wizard",{})["platform"] = "youtube"
    segments = [{
        "source_path": str(tmp_path/"camera.mp4"),
        "clip_path": str(tmp_path/"camera.mp4"),
        "clip_start_sec": i,
        "master_start_sec": i,
        "duration_sec": 1.,
        "spherical_shot": {"type":"singer","yaw":45,"pitch":3,"fov":90} if i==158 else {},
        "manual_view": i==158,
    } for i in range(305)]
    (project.artifacts_dir/"edit_plan.json").write_text(
        json.dumps({"platform":"youtube","segments":segments}),encoding="utf8")
    expected = shot_review._review_segments(project)[158]
    monkeypatch.setattr(shot_review, "_review_segments",
                        lambda project: pytest.fail("single-shot lookup traversed 305 shots"))
    assert review_segment_for_index(project, 158) == expected
    with pytest.raises(IndexError):
        review_segment_for_index(project, 305)
    with pytest.raises(IndexError):
        review_segment_for_index(project, -1)


def test_background_preview_error_does_not_requeue_forever(tmp_path, monkeypatch):
    project=create_project("Bad clip",str(tmp_path/"bad.zuckervid"))
    attempts=[]
    def broken(*args):
        attempts.append(True)
        raise ValueError("synthetic corruption; no actual user media")
    monkeypatch.setattr(review_previews,"render_preview",broken)
    jobs=review_previews.HoverPreviewQueue()
    output=tmp_path/"missing.mp4"
    try:
        assert jobs.request(project,0,{"name":"bad"},output)=="preparing"
        for _ in range(50):
            result=jobs.request(project,0,{"name":"bad"},output)
            if result=="unavailable":break
            time.sleep(.01)
        assert result=="unavailable"
        assert len(attempts)==1
        assert jobs.request(project,0,{"name":"bad"},output)=="unavailable"
    finally:
        jobs.stop()
        jobs._pool.shutdown(wait=True)


def test_review_status_snapshot_cached_then_invalidated_by_plan_and_preview(tmp_path, monkeypatch):
    from core import shot_review
    project=create_project("Review cache",str(tmp_path/"review-cache.zuckervid"))
    plan=project.artifacts_dir/"edit_plan.json"
    plan.write_text('{"platform":"youtube","segments":[]}',encoding="utf8")
    calls=[]
    def fake_review(project,render_missing=True):
        assert render_missing is False
        calls.append(1)
        return [{"index":0,"thumbnail_status":"ready","version":len(calls)}]
    monkeypatch.setattr(shot_review,"review_items",fake_review)
    first=shot_review.cached_review_status(project)
    first[0]["version"]=-100  # never mutate cached storage
    assert shot_review.cached_review_status(project)[0]["version"]==1
    assert len(calls)==1
    plan.write_text('{"platform":"youtube","segments":[{}]}',encoding="utf8")
    assert shot_review.cached_review_status(project)[0]["version"]==2
    preview=project.cache_dir/"shot_review"/"previews-v1"
    preview.mkdir(parents=True,exist_ok=True)
    assert shot_review.cached_review_status(project)[0]["version"]==3
    (preview/"preview-0001.mp4").write_bytes(b"test")
    assert shot_review.cached_review_status(project)[0]["version"]==4
    project.save()
    assert shot_review.cached_review_status(project)[0]["version"]==5


def test_review_partial_status_only_targets_requested_shot(tmp_path, monkeypatch):
    from core import shot_review
    project=create_project("Review partial",str(tmp_path/"review-partial.zuckervid"))
    project.data["settings"].setdefault("wizard",{})["platform"]="youtube"
    project.save()
    plan=project.artifacts_dir/"edit_plan.json"
    plan.write_text(json.dumps({
        "platform":"youtube",
        "segments":[{
            "source_path":str(tmp_path/"shot.mp4"), "clip_path":str(tmp_path/"shot.mp4"),
            "master_start_sec":float(i),"clip_start_sec":float(i),
            "duration_sec":1.0
        } for i in range(305)]
    }),encoding="utf8")
    monkeypatch.setattr(shot_review,"load_coverage",lambda project:{"platform":"youtube","sources":[]})
    monkeypatch.setattr(shot_review,"_project_candidate_count",lambda *args,**kw:(1,"synthetic"))
    one=shot_review.review_items(project,render_missing=False,status_indices={158})
    assert len(one)==1 and one[0]["index"]==158
    assert one[0]["candidate_count"]==1
    with pytest.raises(ValueError):
        shot_review.review_items(project,render_missing=True,status_indices={158})


def test_305_shot_stage_does_not_prefetch_305_mp4_videos(tmp_path, monkeypatch):
    from core import shot_review
    project=create_project("Large review",str(tmp_path/"large.zuckervid"))
    segments=[{"index":i,"duration_sec":1} for i in range(305)]
    touched=[]
    monkeypatch.setattr(shot_review,"_review_segments",lambda project:segments)
    monkeypatch.setattr(shot_review,"render_review_thumbnails",
                        lambda project,indices,**kw:touched.append(("photo",len(indices))))
    monkeypatch.setattr(review_previews,"render_preview",
                        lambda project,index,seg:touched.append(("video",index)))
    review_previews._Prefetcher._run(project,threading.Event())
    assert sum(count for kind,count in touched if kind=="photo")==305
    assert not any(kind=="video" for kind,_ in touched)
    touched.clear()
    review_previews._Prefetcher._run(project,threading.Event(),priority=158,indices={158})
    assert ("video",158) in touched
