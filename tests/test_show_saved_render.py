"""A storyboard may show only the saved export of its current project."""
import json

import pytest

from core.project import create_project
from server.api import create_app


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT', str(tmp_path/'data'))
    project = create_project('Show', str(tmp_path/'Show.zuckervid'))
    movie = project.exports_dir/'Saved.mp4'
    movie.write_bytes(b'video bytes for range routing')
    manifest = project.artifacts_dir/'export_manifest.json'
    manifest.write_text(json.dumps({'platform':'show','exports':[{'path':str(movie)}]}))
    app = create_app(str(project.folder), dev=True)
    app.config['TESTING'] = True
    return project, manifest, app.test_client()


def test_saved_show_render_streams_with_range_and_requires_current_filename(saved):
    _, _, client = saved
    response = client.get('/api/v1/show/rendered/video?file=Saved.mp4', headers={'Range':'bytes=0-4'})
    assert response.status_code == 206
    assert response.data == b'video'
    assert client.get('/api/v1/show/rendered/video?file=Old.mp4').status_code == 404
    assert client.get('/api/v1/show/rendered/video').status_code == 404


def test_saved_render_rejects_an_export_manifest_pointing_outside_project(saved, tmp_path):
    _, manifest, client = saved
    other = tmp_path/'Private.mp4'
    other.write_bytes(b'private')
    manifest.write_text(json.dumps({'platform':'show','exports':[{'path':str(other)}]}))
    assert client.get('/api/v1/show/rendered/video?file=Private.mp4').status_code == 404


def test_saved_render_is_not_available_for_another_edit_mode(saved):
    _, manifest, client = saved
    data = json.loads(manifest.read_text())
    data['platform'] = 'reel'
    manifest.write_text(json.dumps(data))
    assert client.get('/api/v1/show/rendered/video?file=Saved.mp4').status_code == 404


def test_selected_word_drawing_direction_is_kept_as_unapproved_proposal(saved):
    project, _, client = saved
    (project.artifacts_dir/'show_plan.json').write_text(json.dumps({'edited_duration':10,'shots':[]}))
    (project.artifacts_dir/'show_proposals.json').write_text(json.dumps({'version':1,'proposals':[]}))
    response = client.post('/api/v1/show/review/add', json={'kind':'drawing','at':2,'subject':'a crooked sun','style':'crayon','direction':'Peek from the left; rough child-made lines.','selected_text':'good morning'})
    assert response.status_code == 201
    item = response.get_json()
    assert item['status'] == 'proposed'
    assert item['params']['selected_text'] == 'good morning'
    assert 'Peek from the left' in item['params']['prompt']
    stored = json.loads((project.artifacts_dir/'show_proposals.json').read_text())['proposals'][0]
    assert stored['params']['prompt'] == item['params']['prompt']
