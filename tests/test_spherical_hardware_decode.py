import pytest
from core import spherical_motion as motion
from core.ffmpeg import FFmpegError


def setup_hardware(tmp_path, monkeypatch):
    source = tmp_path / 'sphere.mp4'; source.write_bytes(b'source')
    monkeypatch.setattr(motion.sys, 'platform', 'darwin')
    monkeypatch.setattr(motion, '_verified_hardware_format', lambda *args: 'yuvj420p')
    return source


def test_hardware_decoder_failure_retries_cpu_with_same_cut_and_pose(tmp_path, monkeypatch):
    source = setup_hardware(tmp_path, monkeypatch); calls=[]
    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if kwargs.get('decoder_pixel_format'):
            raise motion.HardwareDecodeError('decoder failed')
    monkeypatch.setattr(motion, '_run_reprojected_command', run)
    shot={'movement':'pan_left'}
    motion.run_reprojected_command(['ffmpeg'], str(source), (3840,1920), 13.731, 120, shot)
    assert len(calls)==2 and calls[0][0]==calls[1][0]
    assert calls[0][1]['decoder_pixel_format']=='yuvj420p'
    assert 'decoder_pixel_format' not in calls[1][1]


@pytest.mark.parametrize('error',[FFmpegError('encoder failed'), RuntimeError('cancelled')])
def test_encoder_and_cancellation_errors_do_not_retry_decoder(tmp_path, monkeypatch, error):
    source = setup_hardware(tmp_path, monkeypatch); calls=[]
    def run(*args, **kwargs):
        calls.append(kwargs); raise error
    monkeypatch.setattr(motion, '_run_reprojected_command', run)
    with pytest.raises(type(error), match=str(error)):
        motion.run_reprojected_command(['ffmpeg'], str(source), (3840,1920), 0, 30, {})
    assert len(calls)==1


@pytest.mark.parametrize('pixel,range_,space,codec,expected',[
    ('yuvj420p','pc','bt709','hevc','yuvj420p'),
    ('yuv420p','tv','bt709','h264',None),
    ('yuv420p10le','tv','bt2020nc','hevc',None),
    ('yuvj420p','pc','bt709','h264',None),
])
def test_only_verified_format_enables_hardware(monkeypatch,pixel,range_,space,codec,expected):
    monkeypatch.setattr(motion,'ffprobe',lambda _: {'streams':[{'codec_type':'video','codec_name':codec,
        'pix_fmt':pixel,'color_range':range_,'color_space':space}]})
    motion._verified_hardware_format.cache_clear()
    assert motion._verified_hardware_format('sphere',())==expected


def test_non_mac_and_explicit_cpu_do_not_probe_or_enable_hardware(tmp_path, monkeypatch):
    source=setup_hardware(tmp_path, monkeypatch); calls=[]
    monkeypatch.setattr(motion,'_verified_hardware_format',lambda *args: pytest.fail('Unnecessary probe'))
    monkeypatch.setattr(motion,'_run_reprojected_command',lambda *args,**kwargs:calls.append(kwargs))
    motion.run_reprojected_command(['ffmpeg'],str(source),(3840,1920),0,30,{},hardware_decode=False)
    monkeypatch.setattr(motion.sys,'platform','win32')
    motion.run_reprojected_command(['ffmpeg'],str(source),(3840,1920),0,30,{})
    assert all('decoder_pixel_format' not in call for call in calls)
