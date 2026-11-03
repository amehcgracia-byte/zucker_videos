"""Linux skips Apple-only encoding; Mac fallback and cancellation stay intact."""
import pytest
from core.project import create_project, file_record
from core.ffmpeg import FFmpegError
from core.stages import export


@pytest.mark.parametrize('platform,fail_hardware,expected',[
    ('linux',False,['libx264']),
    ('darwin',False,['h264_videotoolbox']),
    ('darwin',True,['h264_videotoolbox','libx264']),
])
def test_segment_encoder_platform(tmp_path,monkeypatch,platform,fail_hardware,expected):
    run_segment(tmp_path,monkeypatch,platform,fail_hardware,expected)


def run_segment(tmp_path,monkeypatch,platform,fail_hardware,expected,cancel=False):
    project=create_project('Test',str(tmp_path/'Test.zuckervid'))
    source=tmp_path/'source.mp4';source.write_bytes(b'source')
    master=tmp_path/'master.wav';master.write_bytes(b'master')
    output=tmp_path/'segment.mp4'
    record=file_record(str(source))
    record['probe']={'valid_video':True,'video_codec':'h264','duration':5.,'width':1920,'height':1080}
    project.data['inputs']['videos']=[record]
    monkeypatch.setattr(export.sys,'platform',platform)
    monkeypatch.setattr(export,'_ffmpeg_path',lambda:'ffmpeg')
    monkeypatch.setattr(export,'_watermark_path',lambda:None)
    monkeypatch.setattr(export,'_ffmpeg_supports_filter',lambda name:False)
    calls=[]
    class Cancelled(Exception):pass
    def render(command,*args):
        codec=command[command.index('-c:v')+1];calls.append(codec)
        if cancel:raise Cancelled()
        if fail_hardware and codec=='h264_videotoolbox':raise FFmpegError('hardware unavailable')
        output.write_bytes(b'video')
    monkeypatch.setattr(export,'_run_ffmpeg_progress',render)
    def run():
        return export._render_segment(project,{'clip_path':str(source),'source_path':str(source),'clip_start_sec':0,'master_start_sec':0,'duration_sec':3},str(master),output,'youtube',4_000_000)
    if cancel:
        with pytest.raises(Cancelled):run()
        assert not output.exists()
    else:run();assert output.is_file()
    assert calls==expected


@pytest.mark.parametrize('platform,expected',[('linux',['libx264']),('darwin',['h264_videotoolbox'])])
def test_cancellation_never_triggers_encoder_retry(tmp_path,monkeypatch,platform,expected):
    run_segment(tmp_path,monkeypatch,platform,False,expected,cancel=True)
