import json,subprocess,sys,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from core import render_watchdog as watchdog
from core.stages import export
from core.ffmpeg import FFmpegError


def test_idle_requires_no_cpu_and_no_new_output():
    state=watchdog.IdleState(0)
    assert not state.expired(0,0,0,120)
    assert not state.expired(119,0,0,120)
    assert not state.expired(120,1,0,120)  # active compute, no frames yet
    assert not state.expired(239,1,1,120)  # output without CPU change
    assert not state.expired(358,1,1,120)
    assert state.expired(359,1,1,120)


def test_unknown_cpu_measurement_never_kills_active_work():
    state=watchdog.IdleState(0)
    assert not state.expired(500,None,0,120)
    assert not state.expired(1000,None,0,120)


def accelerated_watchdog(monkeypatch):
    monkeypatch.setattr(watchdog,'IDLE_TIMEOUT_SEC',.15)
    monkeypatch.setattr(watchdog,'POLL_SEC',.02)
    monkeypatch.setattr(watchdog,'process_cpu_seconds',lambda _:0.)
    monkeypatch.setattr(watchdog,'stack_sample',lambda pid:{'kind':'test','pid':pid,'text':'scheduler wait'})


def test_hung_owned_child_is_killed_sampled_and_manifested_but_other_child_survives(tmp_path,monkeypatch):
    accelerated_watchdog(monkeypatch)
    project=SimpleNamespace(data={},artifacts_dir=tmp_path)
    other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
    calls=[]
    @watchdog.guard_segment_render
    def render(project,segment,master,output,platform,bitrate,force_cpu=False):
        calls.append(force_cpu)
        if force_cpu:output.write_bytes(b'CPU success');return 'original'
        export._run_ffmpeg_progress([sys.executable,'-c','import time;time.sleep(20)',str(output)],1,'hung',None)
    try:
        output=tmp_path/'segment-0007.mp4'
        assert render(project,{'source_path':'Sony'},'master',output,'youtube',18000000)=='original'
        assert calls==[False,True]
        assert other.poll() is None
        manifest=json.loads((tmp_path/'export_watchdog_manifest.json').read_text())
        assert len(manifest['events'])==1
        event=manifest['events'][0]
        assert event['segment']==7 and event['stack_sample']['text']=='scheduler wait'
        assert event['cpu_retry_succeeded'] is True and event['attempt']==0
    finally:other.kill();other.wait()


def test_cpu_retry_failure_aborts_without_third_attempt(tmp_path):
    project=SimpleNamespace(data={},artifacts_dir=tmp_path);calls=[]
    @watchdog.guard_segment_render
    def render(*args,**kwargs):
        calls.append(kwargs.get('force_cpu',False))
        raise watchdog.SegmentInactivityError({'pid':123,'timeout_sec':120})
    with pytest.raises(watchdog.SegmentInactivityError):
        render(project,{},'',tmp_path/'segment-0001.mp4','youtube',18000000)
    assert calls==[False,True]


def test_cancel_is_not_retried_and_kills_own_blocked_child(tmp_path,monkeypatch):
    accelerated_watchdog(monkeypatch);calls=[];events=[]
    token=watchdog._SCOPE.set({'record':events.append,'index':1,'attempt':0,'source':'x','output':'x'})
    def cancel(*args):raise RuntimeError('User cancelled')
    try:
        with pytest.raises(RuntimeError,match='User cancelled'):
            export._run_ffmpeg_progress([sys.executable,'-c','import time;time.sleep(20)',str(tmp_path/'x')],1,'cancel',cancel)
        assert not events
    finally:watchdog._SCOPE.reset(token)


@pytest.mark.parametrize('error',[RuntimeError('cancelled'),FFmpegError('bad encoder')])
def test_other_errors_do_not_trigger_inactivity_retry(tmp_path,error):
    calls=[]
    @watchdog.guard_segment_render
    def render(*args,**kwargs):calls.append(kwargs);raise error
    with pytest.raises(type(error)):
        render(SimpleNamespace(data={},artifacts_dir=tmp_path),{},'',tmp_path/'segment-0001.mp4','youtube',18000000)
    assert len(calls)==1


def test_retry_budget_is_shared_with_later_repair_of_same_segment(tmp_path):
    project=SimpleNamespace(data={},artifacts_dir=tmp_path);calls=[]
    @watchdog.guard_segment_render
    def render(*args,**kwargs):
        calls.append(kwargs.get('force_cpu',False))
        if kwargs.get('force_cpu'):return 'CPU'
        raise watchdog.SegmentInactivityError({'pid':1,'timeout_sec':120})
    args=(project,{},'',tmp_path/'segment-0001.mp4','youtube',18000000)
    assert render(*args)=='CPU'
    with pytest.raises(watchdog.SegmentInactivityError):render(*args)
    assert calls==[False,True,False]

@pytest.mark.parametrize('spherical',[False,True])
def test_real_renderer_retry_keeps_recipe_and_forces_cpu_encoder_and_decoder(tmp_path,monkeypatch,spherical):
    project=SimpleNamespace(data={'settings':{'export':{'spherical_remap_backend':'metal'}}},artifacts_dir=tmp_path)
    segment={'duration_sec':1,'clip_start_sec':13.731,'source_path':'source.mp4'}
    if spherical:segment['spherical_shot']={'type':'singer','movement':'pan_left','runtime_motion_enabled':True,'yaw':10,'pitch':0,'fov':80}
    source={'source_path':'source.mp4','probe':{'projection':'equirect' if spherical else 'flat','width':3840,'height':1920}}
    monkeypatch.setattr(export,'_segment_source_info',lambda *args:source)
    monkeypatch.setattr(export,'_spherical_uses_original_motion',lambda *args:spherical)
    monkeypatch.setattr(export,'_spherical_export_source_info',lambda *args:(source,False))
    monkeypatch.setattr(export,'_watermark_path',lambda:None)
    monkeypatch.setattr(export,'_ffmpeg_supports_filter',lambda *args:False)
    calls=[]
    def run(command,*args,**kwargs):
        calls.append((command,args,kwargs))
        if len(calls)==1:raise watchdog.SegmentInactivityError({'pid':1,'timeout_sec':120})
        Path(command[-1]).write_bytes(b'CPU result')
    monkeypatch.setattr(export,'_run_ffmpeg_progress',run)
    monkeypatch.setattr(export,'run_reprojected_command',run)
    export._render_segment(project,segment,'master.wav',tmp_path/'segment-0001.mp4','youtube',18000000)
    assert len(calls)==2
    first,second=calls
    assert first[0][first[0].index('-c:v')+1]=='h264_videotoolbox'
    assert second[0][second[0].index('-c:v')+1]=='libx264'
    assert first[0][:first[0].index('-c:v')]==second[0][:second[0].index('-c:v')]
    assert second[0][second[0].index('-b:v')+1]=='18000000'
    if spherical:
        assert first[1]==second[1]
        assert second[2]['hardware_decode'] is False
        assert first[2]['remap_backend'] == 'metal'
        assert second[2].get('remap_backend', 'cpu') == 'cpu'


def test_real_busy_child_without_frames_is_not_timed_out(tmp_path,monkeypatch):
    monkeypatch.setattr(watchdog,'IDLE_TIMEOUT_SEC',.2)
    monkeypatch.setattr(watchdog,'POLL_SEC',.03)
    events=[];token=watchdog._SCOPE.set({'record':events.append,'index':1,'attempt':0,'source':'busy','output':'busy'})
    try:
        export._run_ffmpeg_progress([sys.executable,'-c','import time\nend=time.monotonic()+1.5\nwhile time.monotonic()<end: pass',str(tmp_path/'busy')],1,'busy',None)
        assert not events
    finally:watchdog._SCOPE.reset(token)


def test_repeated_stale_frame_messages_do_not_defeat_watchdog(tmp_path,monkeypatch):
    accelerated_watchdog(monkeypatch);events=[]
    token=watchdog._SCOPE.set({'record':events.append,'index':1,'attempt':0,'source':'stale','output':'stale'})
    try:
        with pytest.raises(watchdog.SegmentInactivityError):
            export._run_ffmpeg_progress([sys.executable,'-c','import time\nwhile True:\n print("frame=0",flush=True)\n time.sleep(.01)',str(tmp_path/'stale')],1,'stale',None)
        assert len(events)==1
    finally:watchdog._SCOPE.reset(token)


def test_real_frame_progress_without_cpu_change_keeps_child_alive(tmp_path,monkeypatch):
    accelerated_watchdog(monkeypatch);events=[]
    # Allow interpreter startup/scheduling after the heavy render tests. The
    # stream still lasts longer than the timeout, so missing progress fails.
    monkeypatch.setattr(watchdog, 'IDLE_TIMEOUT_SEC', 1.)
    token=watchdog._SCOPE.set({'record':events.append,'index':1,'attempt':0,'source':'frames','output':'frames'})
    try:
        export._run_ffmpeg_progress([sys.executable,'-c','import time\nfor i in range(40):\n print("frame="+str(i),flush=True)\n time.sleep(.04)',str(tmp_path/'frames')],1,'frames',None)
        assert not events
    finally:watchdog._SCOPE.reset(token)
