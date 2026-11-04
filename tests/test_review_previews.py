import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core import review_previews
from core.project import create_project

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg/ffprobe not available")


def _video(path: Path, size: str, seconds: float = 3.0) -> str:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,nb_frames:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    return {**data["streams"][0], **data["format"]}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    return create_project("Previews", str(tmp_path / "previews.zuckervid"))


def test_flat_preview_is_small_short_and_cached(project, tmp_path):
    source = _video(tmp_path / "camera.mp4", "640x360")
    segment = {"source_path": source, "clip_path": source, "clip_start_sec": 0.5, "duration_sec": 2.0,
               "motion": {"type": "ken_burns", "zoom_start": 1.0, "zoom_end": 1.06, "library_version": 1}}
    output = review_previews.render_preview(project, 0, segment)
    info = _probe(output)
    assert (int(info["width"]), int(info["height"])) == (review_previews.PREVIEW_WIDTH, review_previews.PREVIEW_HEIGHT)
    assert abs(float(info["duration"]) - 2.0) < 0.2
    assert output.stat().st_size < 400_000
    mtime = output.stat().st_mtime_ns
    assert review_previews.render_preview(project, 0, segment).stat().st_mtime_ns == mtime


def test_preview_identity_follows_the_planned_shot(project, tmp_path):
    source = _video(tmp_path / "camera.mp4", "320x180", 1.0)
    base = {"source_path": source, "clip_start_sec": 0.0, "duration_sec": 1.0}
    first = review_previews.preview_path(project, 3, {**base, "spherical_shot": {"type": "singer", "yaw": 10}})
    second = review_previews.preview_path(project, 3, {**base, "spherical_shot": {"type": "singer", "yaw": 40}})
    third = review_previews.preview_path(project, 3, {**base, "duration_sec": 2.0})
    assert len({first, second, third}) == 3


def test_moving_360_preview_uses_the_export_reprojection_and_moves(project, tmp_path):
    source = _video(tmp_path / "sphere.mp4", "960x480", 2.0)
    segment = {"source_path": source, "clip_path": source, "projection": "equirect", "clip_start_sec": 0.0,
               "duration_sec": 1.6, "spherical_shot": {"type": "singer", "yaw": 0.0, "pitch": 0.0, "fov": 80.0,
                                                       "movement": "pan_left", "runtime_motion_enabled": True}}
    assert review_previews._native_motion(segment)
    output = review_previews.render_preview(project, 1, segment)
    info = _probe(output)
    assert (int(info["width"]), int(info["height"])) == (review_previews.PREVIEW_WIDTH, review_previews.PREVIEW_HEIGHT)
    assert abs(float(info["duration"]) - 1.6) < 0.2


def test_export_defaults_of_the_reprojection_are_unchanged():
    import inspect
    from core import spherical_motion
    signature = inspect.signature(spherical_motion.run_reprojected_command)
    assert signature.parameters["output_size"].default == (1920, 1080)
    assert signature.parameters["fps"].default == 30
    assert signature.parameters["decode_size"].default is None


def test_proxy_target_matches_the_export_proxy(project, tmp_path, monkeypatch):
    from core.stages import export
    source = _video(tmp_path / "sphere.mp4", "320x160", 1.0)
    info = {"source_path": source, "probe": {"projection": "equirect", "duration": 1.0}}
    target = export._spherical_export_proxy_target(project, info, {})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"proxy")
    resolved, used_proxy = export._spherical_export_source_info(project, info, {})
    assert used_proxy and Path(resolved["source_path"]) == target


def test_preview_endpoint_never_competes_with_the_render(project, tmp_path, monkeypatch):
    from server import api as api_module
    plan_segment = {"source_path": str(tmp_path / "missing.mp4"), "clip_start_sec": 0.0, "duration_sec": 1.0}
    monkeypatch.setattr("core.shot_review._review_segments", lambda project: [plan_segment])
    client = api_module.create_app(project_path=str(project.folder)).test_client()
    assert client.get("/api/v1/wizard/review/preview/0").status_code == 409
    cached = review_previews.preview_path(project, 0, plan_segment)
    cached.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    assert client.get("/api/v1/wizard/review/preview/0").status_code == 200
    assert client.get("/api/v1/wizard/review/preview/9").status_code == 404


def test_frontend_previews_on_hover_only():
    source = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    assert 'data-preview="${escapeHtml(item.preview || "")}"' in source
    assert "function startReviewPreview(button)" in source and "function stopReviewPreview()" in source
    assert 'video.removeAttribute("src")' in source


def test_status_identity_never_measures_light(project, tmp_path, monkeypatch):
    from core import video_light
    project.data['settings']['edit']['video_light'] = {'enabled': True}
    monkeypatch.setattr(video_light, 'resolved_profile', lambda *a: pytest.fail('status measured cameras'))
    segment = {'source_path': str(tmp_path/'camera.mp4'), 'duration_sec': 6}
    assert review_previews.preview_path(project, 0, segment).suffix == '.mp4'


def test_prefetch_finishes_groups_of_three_before_next_thumbnails(project, monkeypatch):
    import threading
    from core import shot_review
    segments = [{'duration_sec': 1, 'slot': i} for i in range(7)]
    calls = []
    monkeypatch.setattr(shot_review, '_review_segments', lambda p: segments)
    monkeypatch.setattr(shot_review, 'render_review_thumbnails', lambda p, indices, **kw: calls.append(('photos', sorted(indices))))
    monkeypatch.setattr(review_previews, 'render_preview', lambda p, index, s: calls.append(('preview', index)))
    review_previews._Prefetcher._run(project, threading.Event())
    assert calls == [('photos', [0,1,2]), ('preview', 0), ('preview', 1), ('preview', 2),
                     ('photos', [3,4,5]), ('preview', 3), ('preview', 4), ('preview', 5),
                     ('photos', [6]), ('preview', 6)]


def test_resolved_light_single_flight_and_plan_invalidation(project, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from core import video_light
    from core.stages import export
    from core.project import atomic_write_json
    project.data['settings']['edit']['video_light'] = {'enabled': True}
    segment = {'clip_path': str(tmp_path/'camera.mp4'), 'duration_sec': 1}
    atomic_write_json(project.artifacts_dir/'edit_plan.json', {'segments': [segment]})
    calls = []
    monkeypatch.setattr(export, '_color_profiles_for_segments', lambda *a: calls.append(1) or {segment['clip_path']: {'light_anchor': .2}})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: video_light.resolved_profile(project, segment), range(20)))
    assert len(calls) == 1 and all(r['light_anchor'] == .2 for r in results)
    segment['duration_sec'] = 2
    atomic_write_json(project.artifacts_dir/'edit_plan.json', {'segments': [segment]})
    video_light.resolved_profile(project, segment)
    assert len(calls) == 2


@pytest.mark.parametrize('layout,treatment',[('9:16',None),('mix',None),('mix_vertical_horizontal','horizontal'),('mix_vertical_horizontal','vertical')])
def test_reel_preview_uses_vertical_export_canvas_and_thumbnail(project,tmp_path,layout,treatment):
    from core.project import atomic_write_json
    from core.shot_review import review_items
    source=_video(tmp_path/'wide.mp4','640x360',1)
    project.data['settings'].setdefault('wizard',{}).update(platform='reel',reel_aspect=layout)
    segment={'source_path':source,'clip_path':source,'clip_start_sec':0,'duration_sec':.6,
             'master_start_sec':0,'reel_mix_treatment':treatment,'flat_framing':{'zoom':1.2,'x':.8,'y':.5}}
    atomic_write_json(project.artifacts_dir/'edit_plan.json',{'platform':'reel','segments':[segment]})
    items=review_items(project)
    assert items[0]['thumbnail'] and items[0]['preview_ready']
    from core.shot_review import _review_segments
    output=review_previews.preview_path(project,0,_review_segments(project)[0])
    info=_probe(output)
    assert (int(info['width']),int(info['height']))==(270,480)
    assert output.is_file()
