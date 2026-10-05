from pathlib import Path
import subprocess
import threading
import pytest
import numpy as np
from core.medley import allocate, render, media_info, _run
from core.highlights import analyze_samples, best_start
from core.project import create_project
from core.ffmpeg import tool_status


def test_allocation_redistributes_short_sources():
    assert allocate([1, 9], 6) == [1, 5]
    with pytest.raises(ValueError):
        allocate([1, 2], 4)
    with pytest.raises(ValueError):
        allocate([1, 2], float('nan'))


def test_highlight_novelty_and_silence():
    sr = 8000
    t = np.arange(sr * 12) / sr
    y = .03 * np.sin(2 * np.pi * 220 * t)
    y[sr*5:sr*7] = .3 * np.sin(2 * np.pi * 1200 * t[sr*5:sr*7])
    events = analyze_samples(y, sr)
    assert 4 <= best_start(events, 12, 2) <= 6
    assert all(row['instrument'] is None for row in events)
    assert all(row['score'] == 0 for row in analyze_samples(np.zeros(sr*3), sr))


def test_real_medley_black_gap_and_valid_audio(tmp_path):
    ffmpeg = tool_status()['ffmpeg_path']
    if not ffmpeg:
        pytest.skip('FFmpeg unavailable')
    entries = []
    for i, color in enumerate(['red', 'blue']):
        video = tmp_path / f'{i}.mp4'
        subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', f'color={color}:s=320x240:r=30', '-f', 'lavfi', '-i', f'sine=frequency={440+i*440}:sample_rate=48000', '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)], check=True)
        entries.append({'video': str(video), 'audio': ''})
    project = create_project('Medley test', str(tmp_path / 'project.zuckervid'))
    updates = []
    output, manifest = render(project, entries, 4.5, .5, .25, lambda *values: updates.append(values), lambda: None)
    duration, video, audio = media_info(output)
    assert video and audio and abs(duration-4.5) < .15
    assert len(manifest['songs']) == 2
    frame = subprocess.run([ffmpeg, '-v', 'error', '-ss', '2.25', '-i', str(output), '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], capture_output=True, check=True).stdout
    assert max(frame) < 5
    sound = subprocess.run([ffmpeg, '-v', 'error', '-i', str(output), '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', 'pipe:1'], capture_output=True, check=True).stdout
    samples = np.frombuffer(sound, dtype='<f4')
    assert np.max(np.abs(samples[17000:18500])) < .003
    assert np.max(np.abs(samples[4000:8000])) > .03
    assert any(len(row) > 4 and 0 < row[4] < 100 for row in updates)
    assert not list(project.cache_dir.glob('medley-*'))


def test_cancellation_does_not_publish(tmp_path):
    project = create_project('cancel', str(tmp_path / 'cancel.zuckervid'))
    def cancel():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        render(project, [{'video': 'unused'}], 10, .5, .5, lambda *args: None, cancel)
    assert not list(project.exports_dir.glob('*.mp4'))


def test_instrument_events_require_actual_stems():
    from core.highlights import instrument_events
    sr = 8000
    t = np.arange(sr*8)/sr
    vocals = np.zeros_like(t)
    vocals[sr*2:sr*5] = .2*np.sin(2*np.pi*440*t[sr*2:sr*5])
    piano = .015*np.sin(2*np.pi*220*t)
    events = instrument_events({'vocals': vocals, 'piano': piano}, sr)
    singing = [row for row in events if row['instrument']=='singer']
    assert singing and all(2 <= row['start_sec'] < 5 for row in singing)
    assert not [row for row in events if row['instrument']=='drummer']
