import subprocess

import pytest

from core.ffmpeg import FFmpegError, tool_status
from core.stages import export


@pytest.fixture(params=[30, 25, '30000/1001', 'vfr'])
def moving_video(tmp_path, request):
    path = tmp_path / 'moving.mp4'
    subprocess.run([
        tool_status()['ffmpeg_path'], '-v', 'error', '-y', '-f', 'lavfi',
        '-i', f'testsrc2=size=160x90:rate={30 if request.param == "vfr" else request.param}:duration=3',
        *(['-vf', 'select=not(mod(n\\,3))', '-fps_mode', 'vfr'] if request.param == 'vfr' else []),
        '-c:v', 'libx264', '-threads', '1', '-g', '30', '-bf', '3', str(path),
    ], check=True)
    return path


def test_single_session_matches_independent_seek_samples(moving_video):
    # Exact boundaries, non-frame-aligned seeks, and rounding to milliseconds.
    for first, second in [(0, 1), (.123, 1.987), (.0334, .0667), (.7995, 2.7996)]:
        expected = (export._frame_md5(moving_video, first), export._frame_md5(moving_video, second))
        assert export._frame_md5_pair(moving_video, first, second) == expected


def test_missing_second_sample_fails_closed(moving_video):
    with pytest.raises(FFmpegError, match='both motion verification samples'):
        export._frame_md5_pair(moving_video, .2, 10)


def test_motion_check_still_rejects_identical_video_frames_even_with_changing_audio(tmp_path):
    path = tmp_path / 'static.mp4'
    subprocess.run([
        tool_status()['ffmpeg_path'], '-v', 'error', '-y', '-f', 'lavfi',
        '-i', 'color=red:size=160x90:rate=30:duration=2',
        '-f', 'lavfi', '-i', 'sine=frequency=997:duration=2',
        '-c:v', 'libx264', '-threads', '1', '-c:a', 'aac', str(path),
    ], check=True)
    with pytest.raises(FFmpegError, match='Rendered static segment'):
        export._verify_moving_frames(path, .2, 1.5, 'static', 'test')


def test_cancellation_and_decoder_errors_are_not_hidden(monkeypatch):
    def cancelled(*args, **kwargs):
        raise RuntimeError('cancelled')
    monkeypatch.setattr(export.subprocess, 'run', cancelled)
    with pytest.raises(RuntimeError, match='cancelled'):
        export._frame_md5_pair('video.mp4', .2, 1.5)
