from pathlib import Path
from urllib.parse import quote
import io
import json
import subprocess
import pytest

@pytest.fixture
def client(tmp_path, monkeypatch):
    from core import storage
    from server.api import create_app
    monkeypatch.delenv('ZUCKER_DATA_ROOT', raising=False)
    home = tmp_path / 'home'; home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: home)
    data = storage.save_data_root(str(tmp_path / 'external'))
    (data / 'config.json').write_text(json.dumps({'project_root': str(tmp_path / 'session')}))
    app = create_app(dev=False)
    app.config['TESTING'] = True
    return app.test_client()

@pytest.fixture
def video(tmp_path):
    path = tmp_path / 'Camera with spaces.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:size=640x360:rate=5', '-t', '3', '-c:v', 'libx264', str(path)], check=True)
    return path


def test_raw_import_streams_without_multipart_and_reuses_identical_bytes(client, video, monkeypatch):
    from flask import Request
    def reject_multipart(*args, **kwargs):
        raise AssertionError('Raw file must not pass through multipart spool')
    monkeypatch.setattr(Request, '_load_form_data', reject_multipart)
    payload = video.read_bytes()
    headers = {'X-Zucker-Filename': quote(video.name)}
    first = client.post('/api/v1/wizard/upload', data=payload, content_type='application/octet-stream', headers=headers)
    second = client.post('/api/v1/wizard/upload', data=payload, content_type='application/octet-stream', headers=headers)
    assert first.status_code == second.status_code == 200
    imported = first.json['videos'][0]['path']
    assert second.json['videos'][0]['path'] == imported
    assert Path(imported).read_bytes() == payload
    assert not list(Path(imported).parent.glob('*.part'))


def test_continue_draft_is_saved_discoverable_and_reopens_before_render(client, video):
    body = {'name': 'New song', 'videos': [str(video)], 'master': '', 'songs': ''}
    saved = client.post('/api/v1/wizard/draft', json=body)
    assert saved.status_code == 200, saved.json
    folder = Path(saved.json['project_id'])
    assert folder.parent.name == 'session'
    persisted = json.loads((folder / 'project.json').read_text())
    assert persisted['inputs']['videos'][0]['path'] == str(video)
    projects = client.get('/api/v1/wizard/projects').json['projects']
    assert any(item['path'] == str(folder) for item in projects)
    # A second Continue for the same project must not create a duplicate.
    second = client.post('/api/v1/wizard/draft', json={**body, 'project_id': str(folder)})
    assert second.status_code == 200
    assert len(client.get('/api/v1/wizard/projects').json['projects']) == 1
    assert client.post('/api/v1/wizard/projects/open', json={'path': str(folder)}).status_code == 200
    assert client.get('/api/v1/project').json['inputs']['videos'][0]['path'] == str(video)
    assert client.get('/api/v1/wizard/logo').json['mode'] == 'default'
    assert client.get('/api/v1/wizard/logo/default').status_code == 200


def test_draft_does_not_replace_running_project(client, video):
    state = client.application.config['ZUCKER_STATE']
    from server.wizard import WizardJob
    state.wizard._job = WizardJob(id='current', status='running')
    response = client.post('/api/v1/wizard/draft', json={'videos': [str(video)]})
    assert response.status_code == 409
    assert state.project is None


def test_missing_raw_filename_and_multipart_compatibility(client, video):
    assert client.post('/api/v1/wizard/upload', data=b'x', content_type='application/octet-stream').status_code == 400
    result = client.post('/api/v1/wizard/upload', data={'files': (io.BytesIO(video.read_bytes()), video.name)})
    assert result.status_code == 200
    assert len(result.json['videos']) == 1


def test_native_drop_registers_original_paths_without_uploading(tmp_path):
    from app import _enable_native_drop
    class Event:
        handler = None
        def __iadd__(self, handler): self.handler = handler; return self
    from types import SimpleNamespace
    event = Event()
    zone = SimpleNamespace(events=SimpleNamespace(drop=event))
    calls = []
    window = SimpleNamespace(dom=SimpleNamespace(get_element=lambda selector: zone), evaluate_js=calls.append)
    _enable_native_drop(window)
    media = tmp_path / 'camera.mov'
    event.handler({'dataTransfer': {'files': [{'name': media.name, 'pywebviewFullPath': str(media)}]}})
    assert calls[0] == 'window.nativeDropReady = true'
    assert str(media) in calls[1]
    assert 'receiveNativeDrop' in calls[1]


def test_repeated_classification_reuses_probe_and_invalidates_on_file_change(video, monkeypatch):
    import server.inbox as inbox
    original = inbox.ffprobe
    calls = []
    def counted(path): calls.append(path); return original(path)
    monkeypatch.setattr(inbox, 'ffprobe', counted)
    first = inbox.classify_file(video)
    first['probe']['width'] = -1
    assert inbox.classify_file(video)['probe']['width'] == 640
    assert len(calls) == 1
    video.touch()
    assert inbox.classify_file(video)['kind'] == 'videos'
    assert len(calls) == 2


def test_draft_keeps_all_audio_choices_for_medley(client, video, tmp_path):
    first = tmp_path / 'first.wav'; first.write_bytes(b'audio fixture one')
    second = tmp_path / 'second.wav'; second.write_bytes(b'audio fixture two')
    response = client.post('/api/v1/wizard/draft', json={'name':'Medley', 'videos':[str(video)], 'audio_paths':[str(first), str(second)]})
    assert response.status_code == 200
    audio = client.get('/api/v1/project').json['inputs']['additional_audio']
    assert [row['path'] for row in audio] == [str(first), str(second)]


def test_medley_start_dispatches_without_prepare_or_transcription(client, video, monkeypatch):
    from server.wizard import WizardRunner, WizardJob
    assert client.post('/api/v1/wizard/draft', json={'name':'Medley','videos':[str(video)]}).status_code == 200
    called = []
    def start(self, project, entries, duration, gap, fade):
        called.append((entries,duration,gap,fade))
        return WizardJob(id='fixture')
    monkeypatch.setattr(WizardRunner,'start_medley',start)
    response=client.post('/api/v1/wizard/start',json={'platform':'medley','videos':[str(video)],'medley_entries':[{'video':str(video),'audio':''}],'medley_duration_sec':4,'medley_gap_sec':.5,'medley_fade_sec':.25})
    assert response.status_code == 202, response.json
    assert called == [([{'video':str(video),'audio':''}],4,.5,.25)]


@pytest.mark.parametrize('mode',['youtube','reel','360','medley','backstage'])
def test_chosen_mode_is_saved_at_first_continue(client,video,mode):
    saved=client.post('/api/v1/wizard/draft',json={'name':'Mode first','videos':[str(video)],'master':'','platform':mode})
    assert saved.status_code==200,saved.json
    document=json.loads((Path(saved.json['project_id'])/'project.json').read_text())
    assert document['settings']['wizard']['platform']==mode
