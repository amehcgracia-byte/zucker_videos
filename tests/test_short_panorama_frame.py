"""A short registered panorama must yield a real frame, not seek past EOF."""
import subprocess
import pytest
from core.project import create_project, file_record
from server.api import create_app


@pytest.mark.parametrize('duration', [.04, 1, 20])
def test_short_sphere_source_frame_is_a_decodable_jpeg(tmp_path, monkeypatch, duration):
    monkeypatch.setenv('ZUCKER_DATA_ROOT', str(tmp_path/'data'))
    source=tmp_path/'sphere.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'testsrc2=size=320x160:rate=25:duration={duration}',
                    '-c:v','libx264','-preset','ultrafast',str(source)],check=True)
    project=create_project('Short sphere',str(tmp_path/'Short.zuckervid'))
    project.data['inputs']['videos']=[file_record(str(source))]
    project.save()
    c=create_app(project_path=str(project.folder)).test_client()
    response=c.get('/api/v1/wizard/spherical-source-frame',query_string={'source':str(source)})
    assert response.status_code==200,response.get_json(silent=True)
    assert response.mimetype=='image/jpeg' and response.data[:2]==b'\xff\xd8'
    subprocess.run(['ffmpeg','-v','error','-f','image2pipe','-i','pipe:0','-frames:v','1','-f','null','-'],
                   input=response.data,check=True,capture_output=True)
    other=tmp_path/'private.mp4';other.write_bytes(source.read_bytes())
    assert c.get('/api/v1/wizard/spherical-source-frame',query_string={'source':str(other)}).status_code==404
