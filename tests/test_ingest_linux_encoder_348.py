"""#348 regression: do not launch a macOS-only VideoToolbox on VM Linux."""
from pathlib import Path
from unittest.mock import Mock
import pytest

from core import normalization
from core.project import create_project
from core.project import file_record


def _record(tmp_path):
    source=tmp_path/'input.mov';source.write_bytes(b'fixture')
    record=file_record(str(source))
    record['probe']={'duration':3.0,'fps':30.0}
    project=create_project('Ingest348',str(tmp_path/'Ingest348.zuckervid'))
    return source, record, project


def test_linux_direct_libx264_no_bad_hardware_attempt(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'home'))
    monkeypatch.setattr(normalization.sys,'platform','linux')
    monkeypatch.setattr(normalization,'tool_status',lambda:{'ffmpeg_path':'ffmpeg'})
    source,record,project=_record(tmp_path)
    calls=[]
    def fake(cmd,duration,name,progress):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b'proxy')
    monkeypatch.setattr(normalization,'_run_ffmpeg_progress',fake)
    item=normalization.normalize_video_record(project,record,lambda *args:None)
    assert len(calls)==1
    assert calls[0][calls[0].index('-c:v')+1]=='libx264'
    assert '-hwaccel' not in calls[0]
    assert item['encode_path']=='software'
    assert item['normalization_version']==normalization.NORMALIZATION_VERSION
    assert Path(item['path']).exists()


def test_mac_videotoolbox_success_remains_first(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'home'))
    monkeypatch.setattr(normalization.sys,'platform','darwin')
    monkeypatch.setattr(normalization,'tool_status',lambda:{'ffmpeg_path':'ffmpeg'})
    _,record,project=_record(tmp_path)
    codecs=[]
    def fake(cmd,*args):
        codecs.append(cmd[cmd.index('-c:v')+1]);Path(cmd[-1]).write_bytes(b'proxy')
    monkeypatch.setattr(normalization,'_run_ffmpeg_progress',fake)
    item=normalization.normalize_video_record(project,record,lambda *args:None)
    assert codecs==['h264_videotoolbox']
    assert item['encode_path']=='hardware'


def test_mac_failure_falls_back_to_libx264(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'home'))
    monkeypatch.setattr(normalization.sys,'platform','darwin')
    monkeypatch.setattr(normalization,'tool_status',lambda:{'ffmpeg_path':'ffmpeg'})
    _,record,project=_record(tmp_path)
    codecs=[]
    def fake(cmd,*args):
        codecs.append(cmd[cmd.index('-c:v')+1])
        if len(codecs)==1:raise normalization.FFmpegError('hardware unavailable')
        Path(cmd[-1]).write_bytes(b'proxy')
    monkeypatch.setattr(normalization,'_run_ffmpeg_progress',fake)
    item=normalization.normalize_video_record(project,record,lambda *args:None)
    assert codecs==['h264_videotoolbox','libx264']
    assert item['encode_path']=='software'


def test_proxy_preset_canary_is_opt_in_and_reversible(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'home'))
    monkeypatch.setattr(normalization.sys,'platform','linux')
    monkeypatch.setattr(normalization,'tool_status',lambda:{'ffmpeg_path':'ffmpeg'})
    _,record,project=_record(tmp_path)
    captured=[]
    def fake(cmd,*args):
        captured.append(cmd)
        Path(cmd[-1]).write_bytes(b'proxy')
    monkeypatch.setattr(normalization,'_run_ffmpeg_progress',fake)
    monkeypatch.setenv('MONTARA_INGEST_PROXY_PRESET','veryfast')
    normalization.normalize_video_record(project,record,lambda *args:None)
    assert captured[0][captured[0].index('-preset')+1]=='veryfast'
    # Explicitly opt back into default; unknown values must fail safe.
    assert '-preset' not in normalization._normalization_command(Path('a'),Path('b'),30,'scale=640:360','libx264',software_preset='medium')
    monkeypatch.setenv('MONTARA_INGEST_PROXY_PRESET','unknown')
    next_source=tmp_path/'next-input.mov'
    next_source.write_bytes(b'fixture2')
    from core.project import file_record
    other=file_record(str(next_source))
    other['probe']={'duration':3.0,'fps':30.0}
    normalization.normalize_video_record(project,other,lambda *args:None)
    assert len(captured)==2
    assert '-preset' not in captured[1]
