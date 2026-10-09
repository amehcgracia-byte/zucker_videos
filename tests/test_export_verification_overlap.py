import threading

import pytest

from core.project import create_project
from core.stages import export


def test_verification_failure_stops_next_render_and_preserves_published_result(tmp_path, monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT', str(tmp_path / 'data'))
    project = create_project('Overlap', str(tmp_path / 'Overlap.zuckervid'))
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    master = tmp_path / 'master.wav'
    master.write_bytes(b'master')
    output = tmp_path / 'out.mp4'
    output.write_bytes(b'previous result')
    monkeypatch.setattr(export, '_ffmpeg_path', lambda: 'ffmpeg')
    monkeypatch.setattr(export, '_color_profiles_for_segments', lambda *a: {})
    monkeypatch.setattr(export, '_render_logo_clip', lambda path, *a, **kw: path.write_bytes(b'logo'))
    monkeypatch.setattr(export, '_segment_worker_count', lambda *a: 1)
    monkeypatch.setattr(export, '_verification_overlap_enabled', lambda *a: True)
    next_render = threading.Event()
    stopped = threading.Event()
    calls = []

    def job(*args, verification_submit):
        index, progress = args[1], args[-2]
        calls.append(index)
        if index == 1:
            def verify():
                assert next_render.wait(2), 'Next shot must render while verification is pending'
                raise RuntimeError('verification failed')
            return verification_submit(verify)
        next_render.set()
        try:
            for _ in range(200):
                stopped.wait(.01)
                progress(10, f'Rendering segment {index}/20: 10%')
        except RuntimeError:
            stopped.set()
            raise
        raise AssertionError('Active render was not cancelled')

    monkeypatch.setattr(export, '_render_segment_job', job)
    segments = [dict(source_path=str(source), clip_path=str(source), clip_start_sec=i,
                     master_start_sec=i, duration_sec=1) for i in range(20)]
    with pytest.raises(export.SegmentRenderError, match='verification failed') as error:
        export._render_plan(project, segments, str(master), output, 'youtube', 4_000_000, [], lambda *a: None)
    assert error.value.segment_index == 1
    assert calls == [1, 2] and stopped.is_set()
    assert output.read_bytes() == b'previous result'


def test_overlap_stays_off_when_memory_is_unknown_or_insufficient(tmp_path, monkeypatch):
    project = create_project('Memory', str(tmp_path / 'Memory.zuckervid'))
    project.data['settings']['export']['overlap_segment_verification'] = True
    monkeypatch.setattr(export.sys, 'platform', 'darwin')
    monkeypatch.setattr(export.subprocess, 'check_output', lambda *a, **kw: str(8 * 1024 ** 3).encode())
    assert not export._verification_overlap_enabled(project)
    def unavailable(*a, **kw):
        raise OSError('No memory information')
    monkeypatch.setattr(export.subprocess, 'check_output', unavailable)
    assert not export._verification_overlap_enabled(project)


def test_overlap_requires_available_memory_not_just_installed_ram(tmp_path, monkeypatch):
    project = create_project('Available', str(tmp_path / 'Available.zuckervid'))
    monkeypatch.setattr(export.sys, 'platform', 'darwin')
    free_pages = [100]
    def memory(command, **kwargs):
        if command[0] == 'sysctl':
            return str(32 * 1024 ** 3).encode()
        return f'Mach Virtual Memory Statistics: (page size of 4096 bytes)\nPages free: {free_pages[0]}.\nPages inactive: 0.\nPages speculative: 0.\n'
    monkeypatch.setattr(export.subprocess, 'check_output', memory)
    assert not export._verification_overlap_enabled(project)
    free_pages[0] = 700000
    assert export._verification_overlap_enabled(project)
    project.data['settings']['export']['overlap_segment_verification'] = False
    assert not export._verification_overlap_enabled(project)
