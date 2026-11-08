import shutil
import subprocess
import pytest
from core.ffmpeg import FFmpegError
from core.spherical_motion import run_reprojected_command

@pytest.mark.parametrize('requested,success', [(31, True), (32, False)])
def test_only_one_missing_final_frame_is_allowed(tmp_path, requested, success):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg unavailable')
    source = tmp_path / 'sphere.mp4'
    subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i',
                    'testsrc2=size=320x160:rate=25:duration=1','-c:v','libx264',str(source)],check=True)
    output = tmp_path / 'out.mp4'
    command = ['ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','bgr24',
               '-s','160x90','-r','30','-i','pipe:0','-c:v','libx264',str(output)]
    def render():
        run_reprojected_command(command,str(source),(320,160),0,requested,
                                {'yaw':0,'pitch':0,'fov':80},hardware_decode=False,output_size=(160,90))
    if success:
        render()
        count = subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
                    '-show_entries','stream=nb_frames','-of','csv=p=0',str(output)],text=True)
        assert int(count.strip()) == requested
    else:
        with pytest.raises(FFmpegError,match='decode ended'):
            render()
