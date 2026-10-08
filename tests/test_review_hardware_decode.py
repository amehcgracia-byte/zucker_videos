import subprocess
from pathlib import Path

from core import shot_review


def test_thumbnail_hardware_failure_retries_original_cpu_command(tmp_path, monkeypatch):
    monkeypatch.setattr(shot_review.sys,'platform','darwin')
    output = tmp_path/'frame.jpg'
    command = ['ffmpeg','-ss','13.731','-i','camera.mp4','-frames:v','1',str(output)]
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if '-hwaccel' in args:
            output.write_bytes(b'incomplete')
            raise subprocess.CalledProcessError(1,args,stderr='No hardware decoder')
        assert not output.exists()
        output.write_bytes(b'complete')
    monkeypatch.setattr(shot_review.subprocess,'run',run)
    shot_review._run_thumbnail_command(command)
    assert calls[0][calls[0].index('-hwaccel')+1] == 'videotoolbox'
    assert calls[1] == command
    assert command[command.index('-ss')+1] == '13.731'
    assert output.read_bytes() == b'complete'


def test_non_mac_thumbnail_keeps_cpu_path_and_propagates_failure(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(shot_review.sys,'platform','win32')
    calls = []
    command = ['ffmpeg','-i','camera.mp4',str(tmp_path/'frame.jpg')]
    def run(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(7,args,stderr='decoder failure')
    monkeypatch.setattr(shot_review.subprocess,'run',run)
    with pytest.raises(subprocess.CalledProcessError) as error:
        shot_review._run_thumbnail_command(command)
    assert error.value.returncode == 7
    assert calls == [command]


def review_project(tmp_path):
    import json
    from core.project import create_project
    from core.stages.base import artifact_path
    source = tmp_path/'camera.mp4'
    source.write_bytes(b'camera')
    project = create_project('Parallel review',str(tmp_path/'review.zuckervid'))
    segments = [{'clip_path':str(source),'source_path':str(source),'clip_start_sec':i,
                 'master_start_sec':i,'duration_sec':1,'filename':source.name} for i in range(3)]
    artifact_path(project,'edit_plan.json').write_text(json.dumps({'platform':'reel','segments':segments}))
    artifact_path(project,'coverage.json').write_text(json.dumps({'sources':[{'path':str(source)}]}))
    return project


def test_review_uses_two_workers_and_preserves_card_order(tmp_path, monkeypatch):
    import threading
    project = review_project(tmp_path)
    barrier, lock = threading.Barrier(2),threading.Lock()
    active, peak, calls = 0,0,0
    def render(command):
        nonlocal active,peak,calls
        with lock:
            active += 1;calls += 1;peak = max(peak,active); first = calls <= 2
        if first:
            barrier.wait(timeout=3)
        Path(command[-1]).write_bytes(b'complete JPEG')
        with lock:
            active -= 1
    monkeypatch.setattr(shot_review,'_run_thumbnail_command',render)
    progress=[]
    rows = shot_review.review_items(project,progress_callback=lambda percent,detail:progress.append(percent))
    assert peak == 2 and calls == 3
    assert [row['index'] for row in rows] == [0,1,2]
    assert all(row['thumbnail_status']=='ready' and row['thumbnail'] for row in rows)
    assert progress == sorted(progress) and progress[-1] == 100


def test_simultaneous_preview_requests_render_same_asset_only_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    project = review_project(tmp_path)
    barrier = threading.Barrier(2)
    actual = shot_review._render_thumbnail_to_path
    calls = []
    def enter(*args):
        barrier.wait(timeout=3)
        return actual(*args)
    def render(command):
        calls.append(command)
        Path(command[-1]).write_bytes(b'complete JPEG')
    monkeypatch.setattr(shot_review,'_render_thumbnail_to_path',enter)
    monkeypatch.setattr(shot_review,'_run_thumbnail_command',render)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(shot_review.review_items,project,render_indices={0}) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert len(calls) == 1
    assert results[0][0]['thumbnail'] == results[1][0]['thumbnail']
