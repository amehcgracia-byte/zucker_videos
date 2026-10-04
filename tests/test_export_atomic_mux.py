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
