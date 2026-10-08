import errno
import subprocess
import sys
import os
from pathlib import Path

import pytest

from core.ffmpeg import FFmpegError
from core.stages import export


def test_integrity_detects_same_size_rewrite_with_restored_mtime(tmp_path, monkeypatch):
    path = tmp_path / 'segment'
    path.write_bytes(b'first')
    monkeypatch.setattr(export, '_segment_cache_stamp_payload', lambda _: {'recipe': 1})
    export._write_segment_cache_stamp(path, {})
    original = path.stat()
    assert export._segment_cache_stamp_matches(path, {})
    path.write_bytes(b'other')
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert not export._segment_cache_stamp_matches(path, {})


def test_noisy_encoder_finishes_without_stderr_pipe_deadlock(tmp_path, monkeypatch):
    monkeypatch.setattr(export, 'working_temporary_file', lambda: open(tmp_path / 'errors', 'w+b'))
    real_popen = subprocess.Popen
    children = []
    def start(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        # A noisy encoder must exit independently of our progress reader.
        children.append(process)
        return process
    monkeypatch.setattr(export.subprocess, 'Popen', start)
    import threading
    timer = threading.Timer(5, lambda: [p.kill() for p in children if p.poll() is None])
    timer.start()
    try:
        export._run_ffmpeg_progress(
            [sys.executable, '-c', "import sys; sys.stderr.write('x'*1048576); print('out_time_ms=1000000')"],
            1, 'noisy', None,
        )
    finally:
        timer.cancel()


def test_encoder_failure_keeps_diagnostic_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(export, 'working_temporary_file', lambda: open(tmp_path / 'errors', 'w+b'))
    with pytest.raises(FFmpegError, match='last useful error') as caught:
        export._run_ffmpeg_progress([sys.executable, '-c', "import sys; sys.stderr.write('x'*1048576+'\\nlast useful error\\n'); sys.exit(7)"], 1, 'bad', None)
    assert caught.value.exit_code == 7
    assert len(str(caught.value)) <= 65536


def test_completed_segment_moves_without_copy(tmp_path, monkeypatch):
    source, destination = tmp_path / 'render', tmp_path / 'cache'
    source.write_bytes(b'complete encoded video')
    inode = source.stat().st_ino
    monkeypatch.setattr(export.shutil, 'copy2', lambda *_: pytest.fail('same-volume render copied'))
    export._publish_rendered_segment(source, destination)
    assert destination.stat().st_ino == inode
    assert destination.read_bytes() == b'complete encoded video'
    assert not source.exists()


def test_cross_volume_publish_and_other_errors(tmp_path, monkeypatch):
    source, destination = tmp_path / 'render', tmp_path / 'cache'
    source.write_bytes(b'complete')
    def different_volume(*_):
        raise OSError(errno.EXDEV, 'different disk')
    monkeypatch.setattr(export.os, 'replace', different_volume)
    export._publish_rendered_segment(source, destination)
    assert destination.read_bytes() == b'complete' and not source.exists()
    source.write_bytes(b'next')
    def denied(*_):
        raise PermissionError(errno.EACCES, 'denied')
    monkeypatch.setattr(export.os, 'replace', denied)
    with pytest.raises(PermissionError):
        export._publish_rendered_segment(source, destination)
    assert source.read_bytes() == b'next'
    assert destination.read_bytes() == b'complete'
