from types import SimpleNamespace
from unittest.mock import Mock
import json
import pytest
import core.ffmpeg as media


def test_probe_cache_copies_and_invalidates(tmp_path, monkeypatch):
    source = tmp_path / 'test.mp4'
    source.write_bytes(b'first')
    monkeypatch.setattr(media, 'tool_status', lambda: {'ffprobe_path': 'fixture-ffprobe'})
    command = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({'streams': [{'width': 640}]}), stderr=''))
    monkeypatch.setattr(media.subprocess, 'run', command)
    media._cached_ffprobe.cache_clear()
    first = media.ffprobe(str(source))
    first['streams'][0]['width'] = 1
    assert media.ffprobe(str(source))['streams'][0]['width'] == 640
    assert command.call_count == 1
    source.write_bytes(b'changed content')
    media.ffprobe(str(source))
    assert command.call_count == 2


def test_youtube_and_medley_do_not_transcribe():
    from server.api import AutoReadRunner
    runner = AutoReadRunner()
    for platform in ['youtube', '360', 'medley']:
        project = SimpleNamespace(data={'settings': {'wizard': {'platform': platform}}})
        with pytest.raises(ValueError, match='only for Reel and Backstage'):
            runner.start(project)
        assert runner._thread is None


def test_rerun_applies_new_mode_before_ingest(tmp_path, monkeypatch):
    import server.wizard as wizard
    from core.project import create_project
    project = create_project('switch', str(tmp_path / 'switch.zuckervid'))
    project.data['settings']['wizard'] = {'platform': 'youtube'}
    monkeypatch.setattr(wizard, 'register_selected_inputs', lambda *args, **kwargs: None)
    runner = wizard.WizardRunner()
    def stage(job, project, *args):
        assert project.data['settings']['wizard']['platform'] == 'backstage'
        raise wizard.WizardCancelled()
    monkeypatch.setattr(runner, '_run_stage', stage)
    job = wizard.WizardJob(id='fixture')
    runner._run_existing_from_scratch(job=job, project=project, options={'platform': 'backstage'})
    assert job.status == 'cancelled'


def test_highlights_setting_survives_finish(tmp_path, monkeypatch):
    import inspect
    import server.wizard as wizard
    from core.project import create_project
    project=create_project('highlights',str(tmp_path/'highlights.zuckervid'))
    project.data['settings']['wizard']={'instrument_highlights':True}
    runner=wizard.WizardRunner()
    def stop(job,project,*args):
        assert project.data['settings']['wizard']['instrument_highlights'] is True
        raise wizard.WizardCancelled()
    monkeypatch.setattr(runner,'_run_stage',stop)
    monkeypatch.setattr(wizard,'_store_audio_trim',lambda *args: None)
    options={name:None for name in inspect.signature(runner._finish).parameters if name not in {'job','project'}}
    options.update(name='highlights',platform='youtube',master_path='',video_paths=[],transition_type='none')
    job=wizard.WizardJob(id='fixture')
    runner._finish(job=job,project=project,**options)
    assert job.status=='cancelled'


def test_switching_modes_cannot_reuse_previous_prepare(tmp_path):
    from core.project import create_project
    from server.api import _project_can_skip_prepare
    project=create_project('switch',str(tmp_path/'switch2.zuckervid'))
    project.data['settings']['wizard']={'platform':'youtube'}
    project.data['stages']['ingest']['status']='done'
    assert not _project_can_skip_prepare(project,'backstage')
