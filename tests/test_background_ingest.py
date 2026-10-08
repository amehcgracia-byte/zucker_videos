from __future__ import annotations

import threading
import json

import pytest

from core.project import create_project, file_record
from server.api import create_app


def environment(tmp_path, monkeypatch, block=False):
    import server.wizard as wizard
    monkeypatch.setenv('ZUCKER_DATA_ROOT', str(tmp_path/'data'))
    app = create_app()
    state = app.config['ZUCKER_STATE']
    project = create_project('Background', str(tmp_path/'background.zuckervid'))
    video, audio = tmp_path/'video.mp4', tmp_path/'audio.wav'
    video.write_bytes(b'video'); audio.write_bytes(b'audio')
    project.data['inputs']['videos'] = [file_record(str(video))]
    project.data['inputs']['master'] = file_record(str(audio))
    project.data['settings']['wizard'] = {'platform': 'youtube'}
    project.save()
    state.project = project
    calls = []
    entered, release = threading.Event(), threading.Event()
    if not block:
        release.set()
    def run_stage(job, current, stage, start, end, message):
        calls.append(stage.name)
        job.stage = stage.name
        job.progress = max(job.progress, start)
        job.stage_progress = 37
        if stage.name == 'ingest':
            job.progress = 8.14
            entered.set()
            assert release.wait(5), 'Test did not release the ingest worker'
            if job.cancel_event.is_set():
                raise wizard.WizardCancelled()
        else:
            assert current.data['settings']['wizard']['audio_trim'] == {'start_sec':12.0,'end_sec':24.0}
            assert job.status == 'running'
        current.data['stages'][stage.name].update(status='done', fingerprint=stage.inputs_fingerprint(current))
        job.progress = max(job.progress, end)
        job.stage_progress = 100
        current.save()
        if stage.name == 'export':
            output = current.exports_dir/'fixture.mp4'
            output.write_bytes(b'fixture')
            manifest = current.artifacts_dir/'export_manifest.json'
            manifest.write_text(json.dumps({'exports':[{'path':str(output)}]}))
            return {'export_manifest':str(manifest)}
        return {}
    monkeypatch.setattr(state.wizard, '_run_stage', run_stage)
    monkeypatch.setattr(wizard, '_assert_coverage', lambda project: None)
    monkeypatch.setattr(wizard, '_predicted_total_seconds', lambda *args: None)
    monkeypatch.setattr(wizard, 'review_items', lambda *args, **kwargs: [])
    return app, state, project, calls, entered, release


@pytest.mark.parametrize('block', [False, True])
@pytest.mark.parametrize('platform,expected,status', [
    ('youtube',['ingest','sync','cut','edit'],'waiting_review'),
    ('reel',['ingest','cut','edit'],'waiting_review'),
    ('backstage',['ingest','cut','edit'],'waiting_paper_edit'),
    ('360',['ingest','sync','cut','edit','export'],'done'),
])
def test_continue_reuses_background_job_and_ingests_once(tmp_path, monkeypatch, block, platform, expected, status):
    app, state, project, calls, entered, release = environment(tmp_path, monkeypatch, block)
    project.data['settings']['wizard']['platform'] = platform
    client = app.test_client()
    try:
        response = client.post('/api/v1/wizard/prepare-background', json={'project_id':str(project.folder)})
        assert response.status_code == 202
        assert entered.wait(2)
        if not block:
            state.wizard._thread.join(2)
            assert state.wizard._job.status == 'waiting_choice'
            assert state.wizard._job.progress == 22
        job = state.wizard._job
        started = job.started_at
        assert client.post('/api/v1/wizard/prepare-background', json={'project_id':str(project.folder)}).status_code == 202
        assert state.wizard._job is job
        assert calls == ['ingest']  # no sync or editing during settings
        response = client.post('/api/v1/wizard/start', json={
            'project_id':str(project.folder), 'platform':platform, 'name':'Background',
            'master':project.data['inputs']['master']['path'],
            'videos':[project.data['inputs']['videos'][0]['path']],
            'trim_start_sec':12, 'trim_end_sec':24,
        })
        assert response.status_code == 202, response.get_json()
        assert state.wizard._job is job
        release.set()
        state.wizard._thread.join(3)
        assert not state.wizard._thread.is_alive()
        assert job.status == status, job.error
        assert calls == expected
        assert job.started_at == started
    finally:
        release.set()
        state.wizard._thread.join(3)
        state.wizard.reset()


def test_background_does_not_cancel_other_project_or_accept_stale_id(tmp_path, monkeypatch):
    app, state, project, calls, entered, release = environment(tmp_path, monkeypatch, True)
    client = app.test_client()
    try:
        assert client.post('/api/v1/wizard/prepare-background', json={'project_id':'wrong'}).status_code == 409
        assert not calls
        assert client.post('/api/v1/wizard/prepare-background', json={'project_id':str(project.folder)}).status_code == 202
        assert entered.wait(2)
        job = state.wizard._job
        other = create_project('Other', str(tmp_path/'other.zuckervid'))
        other.data['settings']['wizard'] = {'platform':'youtube'}
        state.project = other
        assert client.post('/api/v1/wizard/prepare-background', json={'project_id':str(other.folder)}).status_code == 409
        assert not job.cancel_event.is_set()
        assert state.wizard._job is job
        assert client.post('/api/v1/wizard/start', json={'project_id':str(project.folder)}).status_code == 409
    finally:
        release.set()
        state.wizard._thread.join(3)
        state.wizard.reset()


def test_cancel_background_leaves_no_worker_and_can_restart(tmp_path, monkeypatch):
    app, state, project, calls, entered, release = environment(tmp_path, monkeypatch, True)
    client = app.test_client()
    client.post('/api/v1/wizard/prepare-background', json={'project_id':str(project.folder)})
    assert entered.wait(2)
    state.wizard.cancel()
    release.set()
    state.wizard._thread.join(3)
    assert state.wizard._job.status == 'cancelled'
    assert client.post('/api/v1/wizard/prepare-background', json={'project_id':str(project.folder)}).status_code == 202
    state.wizard._thread.join(3)
    assert state.wizard._job.status == 'waiting_choice'
    assert calls == ['ingest','ingest']
    state.wizard.reset()
