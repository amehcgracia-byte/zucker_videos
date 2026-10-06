from __future__ import annotations

from server.wizard import _aggregate_segment_progress


def test_parallel_segment_progress_uses_all_known_worker_progress():
    percent, completed = _aggregate_segment_progress(
        {1: 1.0, 2: 1.0, 3: 0.5, 4: 0.0},
        4,
    )
    assert percent == 62
    assert completed == 2


def test_parallel_segment_progress_is_bounded_and_empty_safe():
    assert _aggregate_segment_progress({}, 0) == (0, 0)
    assert _aggregate_segment_progress({1: 1.5, 2: -1.0}, 2) == (50, 1)


def test_segment_phase_leaves_progress_for_join_audio_and_validation(tmp_path):
    from core.project import create_project
    from core.stages.base import ProgressDetail
    from server.wizard import WizardRunner, WizardJob, serialize_wizard_job
    project = create_project('Measured', str(tmp_path/'measured.zuckervid'))
    job = WizardJob(id='measured', progress=70)
    snapshots = []
    class MeasuredExport:
        name = 'export'
        def inputs_fingerprint(self, project):
            return 'measured'
        def run(self, project, callback):
            callback(80, 'Rendering segment 2/2: complete')
            snapshots.append(serialize_wizard_job(job))
            callback(80, ProgressDetail('Rendering segment 1/2: Preparing 360 source — 100%',
                                       task_id='source', label='Preparing source', percent=100))
            snapshots.append(serialize_wizard_job(job))
            callback(80, 'Rendering segment 1/2: complete')
            snapshots.append(serialize_wizard_job(job))
            callback(90, 'Joining rendered shots')
            snapshots.append(serialize_wizard_job(job))
            callback(95, 'Muxing master audio')
            return {}
    WizardRunner()._run_stage(job, project, MeasuredExport(), 70, 100, 'Exporting video')
    assert [snapshot['progress'] for snapshot in snapshots] == [83.5, 83.5, 94, 97]
    assert snapshots[2]['stage_progress'] == 80
    assert job.progress == 100


def test_stage_records_measured_duration_on_completion_and_failure(tmp_path):
    import pytest
    from core.project import create_project
    from server.wizard import WizardRunner, WizardJob
    project = create_project('Timing', str(tmp_path / 'timing.zuckervid'))
    class TimedStage:
        name = 'cut'
        def inputs_fingerprint(self, project):
            return 'timing'
        def run(self, project, callback):
            return {}
    runner = WizardRunner()
    runner._run_stage(WizardJob(id='timing'), project, TimedStage(), 0, 100, 'Cutting')
    assert project.data['stages']['cut']['elapsed_seconds'] >= 0
    class FailedStage(TimedStage):
        def run(self, project, callback):
            raise RuntimeError('failed')
    with pytest.raises(RuntimeError):
        runner._run_stage(WizardJob(id='failure'), project, FailedStage(), 0, 100, 'Cutting')
    assert project.data['stages']['cut']['status'] == 'failed'
    assert project.data['stages']['cut']['elapsed_seconds'] >= 0


def test_medley_retires_previous_phase_tasks(tmp_path, monkeypatch):
    from core.project import create_project
    from server.wizard import WizardRunner, WizardJob, serialize_wizard_job
    snapshots=[]
    project=create_project('Medley progress',str(tmp_path/'medley.zuckervid'))
    job=WizardJob(id='current')
    def render(project, entries, duration, gap, fade, progress, cancel):
        progress(1,'inspect',0,'Checking first file',100)
        snapshots.append(serialize_wizard_job(job))
        progress(5,'music',0,'Decoding song')
        snapshots.append(serialize_wizard_job(job))
        progress(20,'visual',0,'Checking pictures',50)
        snapshots.append(serialize_wizard_job(job))
        progress(60,'render',0,'Rendering excerpt',50)
        snapshots.append(serialize_wizard_job(job))
        output=project.exports_dir/'result.mp4';output.write_bytes(b'fixture')
        return output,{}
    monkeypatch.setattr('core.medley.render',render)
    WizardRunner()._run_medley(job,project,[{'video':'fixture'}],10,0,0)
    assert job.status=='done',job.error
    assert all(len(row['tasks'])==1 for row in snapshots)
    assert snapshots[1]['tasks'][0]['percent'] is None
    assert snapshots[2]['tasks'][0]['percent']==50
    assert snapshots[1]['stage']=='edit'
    assert snapshots[3]['stage']=='export'
