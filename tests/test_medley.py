from pathlib import Path
import subprocess
import threading
import pytest
import numpy as np
from core.medley import allocate, render, media_info, _run, select_highlights


def test_multiple_highlights_choose_distinct_musical_regions():
    events = [{"start_sec": t, "score": 1 if any(a <= t < a+6 for a in (5, 30, 55)) else .05,
               "rms": .1} for t in range(80)]
    clips = select_highlights(events, [], 80, 18)
    assert len(clips) == 3
    assert [row['start'] for row in clips] == [5, 30, 55]
    assert sum(row['duration'] for row in clips) == 18


def test_highlights_reject_black_and_silence_despite_novelty():
    events = [{"start_sec": t, "score": 1 if t < 12 else .7,
               "rms": 0 if 6 <= t < 12 else .1} for t in range(24)]
    pictures = [{'time_sec':t, 'quality':0 if t < 6 else 1} for t in range(24)]
    clips = select_highlights(events, pictures, 24, 6)
    assert clips[0]['start'] >= 12


@pytest.mark.parametrize('available,length', [(18,18), (18.1,18), (50,12.7), (5,4), (60,59.9)])
def test_highlights_preserve_budget_and_never_repeat_footage(available, length):
    clips = select_highlights([], [], available, length)
    assert sum(row['duration'] for row in clips) == pytest.approx(length)
    assert 1 <= len(clips) <= 3
    for i, row in enumerate(clips):
        assert row['start'] >= 0
        assert row['start'] + row['duration'] <= available + 1e-6
        if i:
            assert clips[i-1]['start'] + clips[i-1]['duration'] <= row['start'] + 1e-6


def test_render_internal_highlights_blend_and_song_boundaries_fade(tmp_path, monkeypatch):
    import core.medley as medley
    project = create_project('highlight cuts', str(tmp_path/'cuts.zuckervid'))
    monkeypatch.setattr(medley, 'media_info', lambda p: (36.5 if str(p).endswith('complete.mp4') else 80, True, True))
    events = [{'start_sec':t, 'score':1 if any(a <= t < a+6 for a in (5,30,55)) else 0} for t in range(80)]
    monkeypatch.setattr(medley, 'analyze_file', lambda *args, **kwargs: events)
    monkeypatch.setattr(medley, 'visual_quality', lambda *args, **kwargs: [])
    commands = []
    def run(command, duration, progress, cancel):
        commands.append(command)
        Path(command[-1]).touch()
        progress(100)
    monkeypatch.setattr(medley, '_run', run)
    output, manifest = render(project, [{'video':str(tmp_path/'a.mp4')}, {'video':str(tmp_path/'b.mp4')}],
                              36.5, .5, .25, lambda *args: None, lambda: None)
    songs = [command for command in commands if '-filter_complex' in command]
    assert len(songs) == 2
    for command in songs:
        graph = command[command.index('-filter_complex')+1]
        assert graph.count('xfade=transition=fade:') == 2
        assert graph.count('acrossfade=') == 2
        assert graph.count(',fade=t=in:') == 1
        assert graph.count(',fade=t=out:') == 1
        assert graph.count(',afade=t=in:') == 1
        assert graph.count(',afade=t=out:') == 1
    for row in manifest['songs']:
        assert sum(h['duration'] for h in row['highlights']) - 2*row['internal_blend_sec'] == pytest.approx(row['duration'])
    assert sum(any('color=c=black' in arg for arg in command) for command in commands) == 1
    assert all(len(row['highlights']) == 3 for row in manifest['songs'])
    assert output.exists()
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


def test_medley_silent_source_renders_without_external_audio(tmp_path):
    ffmpeg = tool_status()['ffmpeg_path']
    if not ffmpeg:
        pytest.skip('FFmpeg unavailable')
    source = tmp_path / 'silent.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30', '-t', '2', '-c:v', 'libx264', str(source)], check=True)
    project = create_project('Silent Medley', str(tmp_path/'silent.zuckervid'))
    updates = []
    output, manifest = render(project, [{'video':str(source),'audio':''}], 1.5, 0, .1, lambda *args: updates.append(args), lambda:None)
    duration, video, audio = media_info(output)
    assert video and audio and abs(duration-1.5) < .1
    assert manifest['songs'][0]['has_audio'] is False
    samples = subprocess.check_output([ffmpeg,'-v','error','-i',str(output),'-vn','-ac','1','-ar','8000','-f','f32le','pipe:1'])
    assert np.max(np.abs(np.frombuffer(samples,dtype='<f4'))) < .001
    assert any(row[1]=='visual' and row[4]==100 for row in updates)
    assert any('No audio' in row[3] for row in updates)


def test_real_internal_highlight_blend_keeps_picture_and_sound(tmp_path, monkeypatch):
    ffmpeg = tool_status()['ffmpeg_path']
    if not ffmpeg:
        pytest.skip('FFmpeg unavailable')
    source = tmp_path / 'song.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=320x180:r=30',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '18',
                    '-vf', "drawbox=color=blue:t=fill:enable='gte(t,8)'", '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True)
    project = create_project('Internal cut', str(tmp_path/'internal.zuckervid'))
    monkeypatch.setattr('core.medley.select_highlights', lambda events, pictures, available, length, count=None: [{'start': 0, 'duration': length/2}, {'start': 10, 'duration': length/2}])
    output, manifest = render(project, [{'video':str(source)}], 12, 0, .5, lambda *args:None, lambda:None)
    assert len(manifest['songs'][0]['highlights']) == 2
    assert media_info(output)[0] == pytest.approx(12, abs=.15)
    for second, channel in [(5.5, 0), (6.5, 2)]:
        frame = subprocess.check_output([ffmpeg, '-v', 'error', '-ss', str(second), '-i', str(output),
                                         '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
        assert frame[channel] > 200
    middle = subprocess.check_output([ffmpeg, '-v', 'error', '-ss', '6', '-i', str(output),
                                      '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
    assert 40 < middle[0] < 220 and 40 < middle[2] < 220  # actual red/blue overlap, not black
    sound = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(output), '-vn', '-ac', '1',
                                    '-ar', '8000', '-f', 'f32le', 'pipe:1'])
    samples = np.frombuffer(sound, dtype='<f4')
    assert np.sqrt(np.mean(samples[int(5.9*8000):int(6.1*8000)]**2)) > .04


def test_highlight_boundaries_prefer_nearby_musical_cues():
    events = [{"start_sec": t, "score": .5, "rms": .1} for t in range(30)]
    events[12]["cut_boundaries"] = [{"time_sec": 12.35, "strength": 1}]
    events[18]["cut_boundaries"] = [{"time_sec": 18.35, "strength": 1}]
    selected = select_highlights(events, [], 30, 6)
    assert selected[0]["start"] == pytest.approx(12.35)
    assert selected[0]["duration"] == 6


def test_analysis_records_subsecond_attack_cues():
    sr = 8000
    samples = np.zeros(sr*5)
    start = int(2.35*sr)
    samples[start:] = .15*np.sin(2*np.pi*440*np.arange(len(samples)-start)/sr)
    cues = [cue for event in analyze_samples(samples, sr) for cue in event.get("cut_boundaries", [])]
    assert any(abs(cue['time_sec']-2.35) <= .051 and cue['strength'] > .5 for cue in cues)
