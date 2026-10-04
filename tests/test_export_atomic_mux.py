from pathlib import Path
import pytest
from core.ffmpeg import FFmpegError
from core.stages import export


def test_failed_mux_preserves_previous_result_and_removes_partial(tmp_path, monkeypatch):
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'video')
    result = tmp_path / 'result.mp4'
    result.write_bytes(b'previous valid result')
    monkeypatch.setattr(export, '_check_export_disk_space', lambda *args: None)
    def failed(command, *args):
        Path(command[-1]).write_bytes(b'incomplete')
        raise FFmpegError('No space left on device')
    monkeypatch.setattr(export, '_run_ffmpeg_progress', failed)
    with pytest.raises(FFmpegError, match='No space'):
        export._mux_continuous_master_audio(source, 'audio.wav', result, 0, 4, 1000000, None)
    assert result.read_bytes() == b'previous valid result'
    assert not (tmp_path / '.result.partial.mp4').exists()


def test_short_mux_is_not_published(tmp_path, monkeypatch):
    source = tmp_path / 'video.mp4'; source.write_bytes(b'video')
    result = tmp_path / 'result.mp4'
    monkeypatch.setattr(export, '_check_export_disk_space', lambda *args: None)
    monkeypatch.setattr(export, '_run_ffmpeg_progress', lambda command, *args: Path(command[-1]).write_bytes(b'short'))
    monkeypatch.setattr(export, '_probe_streams', lambda *args: {'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}]})
    monkeypatch.setattr(export, '_media_duration', lambda *args: 2)
    with pytest.raises(FFmpegError, match='incompleta'):
        export._mux_continuous_master_audio(source, 'audio.wav', result, 0, 4, 1000000, None)
    assert not result.exists()
    assert not (tmp_path / '.result.partial.mp4').exists()


def test_selected_audio_is_delayed_and_trimmed_before_padding(tmp_path, monkeypatch):
    source = tmp_path / 'video.mp4'; source.write_bytes(b'video')
    result = tmp_path / 'result.mp4'
    commands = []
    monkeypatch.setattr(export, '_check_export_disk_space', lambda *args: None)
    def rendered(command, *args):
        commands.append(command)
        Path(command[-1]).write_bytes(b'complete')
    monkeypatch.setattr(export, '_run_ffmpeg_progress', rendered)
    monkeypatch.setattr(export, '_probe_streams', lambda *args: {'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}]})
    monkeypatch.setattr(export, '_media_duration', lambda *args: 24.2)
    start, delay = export._audio_mux_start_and_delay([{'master_start_sec': 154}])
    assert (start, delay) == (154, 10)
    export._mux_continuous_master_audio(source, 'master.wav', result, start, 24.2, 1000000, None,
                                      content_start=10, content_end=14, audio_delay=delay)
    graph = commands[0][commands[0].index('-filter_complex') + 1]
    assert graph.startswith('atrim=duration=4.000,asetpts=PTS-STARTPTS,adelay=10000:all=1,apad')
    assert 'afade=t=in:st=10.000' in graph
    assert export._audio_gain_at(5, 24.2, 10, 14) == 0
    assert export._audio_gain_at(18, 24.2, 10, 14) == 0
