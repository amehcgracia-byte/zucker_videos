from pathlib import Path
import subprocess
import pytest
from core.ffmpeg import FFmpegError, tool_status
from core.stages import export


def test_invalid_direct_cadence_removes_candidate_and_requests_repair(tmp_path, monkeypatch):
    segment=tmp_path/'segment.mp4';segment.write_bytes(b'segment')
    listing=tmp_path/'concat.txt';listing.write_text("file 'segment.mp4'\n")
    candidate=tmp_path/'candidate.mp4';published=tmp_path/'published.mp4';published.write_bytes(b'previous result')
    calls=[]
    def mux(*args, **kwargs):
        calls.append(kwargs);candidate.write_bytes(b'bad cadence')
    monkeypatch.setattr(export,'_mux_continuous_master_audio',mux)
    def invalid(*args, **kwargs):raise FFmpegError('cadence invalid')
    monkeypatch.setattr(export,'_verify_video_cadence',invalid)
    phases={}
    result=export._try_direct_concat_master_audio(listing,[segment],'audio.wav',candidate,0,1,30,1000000,None,phases,content_start=0,content_end=1,audio_delay=0)
    assert result is None and not candidate.exists()
    assert published.read_bytes()==b'previous result'
    assert calls[0]['concat_input'] is True and calls[0]['video_size_bytes']==len(b'segment')
    assert 'direct_attempt_sec' in phases


def test_mux_failure_is_not_hidden_as_cadence_repair(tmp_path,monkeypatch):
    def failed(*args,**kwargs):raise FFmpegError('No space left on device')
    monkeypatch.setattr(export,'_mux_continuous_master_audio',failed)
    with pytest.raises(FFmpegError,match='No space'):
        export._try_direct_concat_master_audio(tmp_path/'list.txt',[],'audio.wav',tmp_path/'private.mp4',0,1,30,1000000,None,{},content_start=0,content_end=1,audio_delay=0)


def test_direct_master_matches_legacy_video_audio_and_preserves_previous_result_until_publish(tmp_path):
    ffmpeg=tool_status()['ffmpeg_path'];pieces=[]
    for index,seconds in enumerate([1.5,2.5]):
        output=tmp_path/f'segment-{index}.mp4'
        subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i',f'testsrc2=size=96x54:rate=30:duration={seconds}',
            '-vf',f'hue=h={index*50}','-an','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-video_track_timescale','30000',str(output)],check=True)
        pieces.append(output)
    listing=tmp_path/'concat.txt';listing.write_text(''.join(export._concat_file_line(p) for p in pieces))
    audio=tmp_path/'master.wav'
    subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','sine=frequency=1000:sample_rate=48000:duration=8',str(audio)],check=True)
    joined=tmp_path/'joined.mp4'
    subprocess.run([ffmpeg,'-v','error','-y','-f','concat','-safe','0','-i',str(listing),'-c','copy',str(joined)],check=True)
    legacy=tmp_path/'legacy.mp4'
    export._mux_continuous_master_audio(joined,str(audio),legacy,.25,4,1000000,None,content_start=.5,content_end=3.5,audio_delay=.5)
    published=tmp_path/'published.mp4';published.write_bytes(b'previous result')
    candidate=tmp_path/'candidate.mp4';phases={}
    result=export._try_direct_concat_master_audio(listing,pieces,str(audio),candidate,.25,4,120,1000000,None,phases,content_start=.5,content_end=3.5,audio_delay=.5)
    assert result==candidate and published.read_bytes()==b'previous result'
    hashes=[]
    for video in [legacy,candidate]:
        data=subprocess.check_output([ffmpeg,'-v','error','-i',str(video),'-map','0:v:0','-map','0:a:0','-f','framemd5','pipe:1'],text=True)
        hashes.append(data)
    assert hashes[0]==hashes[1], 'Direct assembly must preserve every decoded video and audio frame'
    assert phases['assembly']=='direct_concat_audio' and phases['concat_sec']==0
    export._publish_rendered_segment(candidate,published)
    assert not candidate.exists() and published.read_bytes()!=b'previous result'
