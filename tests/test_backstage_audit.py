from pathlib import Path
import shutil
import pytest
from core.stages import backstage as b


def test_packaged_font_is_scalable_on_windows_without_system_fonts(tmp_path,monkeypatch):
    font=Path('assets/fonts/DejaVuSans.ttf')
    target=tmp_path/'assets/fonts/DejaVuSans.ttf';target.parent.mkdir(parents=True);shutil.copyfile(font,target)
    monkeypatch.setattr(b.sys,'_MEIPASS',str(tmp_path),raising=False)
    monkeypatch.setattr(b.sys,'platform','win32')
    assert b._subtitle_font_path()==target
    small=b._subtitle_font(24);large=b._subtitle_font(48)
    assert large.getbbox('Readable subtitles')[2]>small.getbbox('Readable subtitles')[2]*1.8


def test_mark_summary_matches_export_selection_and_preserves_closing_order(tmp_path,monkeypatch):
    import json
    import server.api as api
    from core.project import create_project
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    monkeypatch.setattr(api,'record_feedback',lambda *a,**k:None)
    p=create_project('Paper',str(tmp_path/'Paper.zuckervid'))
    segments=[dict(id='one',duration_sec=4),dict(id='two',duration_sec=6),dict(id='three',duration_sec=3)]
    cuts=[dict(id=x['id'],duration_sec=x['duration_sec'],mark='keep') for x in segments]
    (p.artifacts_dir/'backstage_edit.json').write_text(json.dumps(dict(segments=segments)))
    (p.artifacts_dir/'backstage_paper_edit.json').write_text(json.dumps(dict(cuts=cuts)))
    client=api.create_app(str(p.folder)).test_client()
    assert client.post('/api/v1/wizard/paper-edit/mark',json=dict(id='two',mark='drop')).get_json()['selected_duration_sec']==7
    response=client.post('/api/v1/wizard/paper-edit/mark',json=dict(id='one',mark='closing')).get_json()
    assert response['cut_count']==1
    plan=json.loads((p.artifacts_dir/'backstage_edit.json').read_text())
    assert [x['id'] for x in b.selected_segments(plan['segments'])]==['three','one']
    assert b.selection_summary(plan['segments'])==dict(selected_duration_sec=7,cut_count=1)


@pytest.mark.parametrize('text',['vamos','vamos a cantar','qué bonito','estamos hablando','seguir haciendo','la canción'])
def test_spanish_is_not_mistaken_for_an_english_translation(text):
    assert not b._looks_english(text)


@pytest.mark.parametrize('text',['Let us go','We play guitar','We remember her'])
def test_common_english_phrases_remain_usable(text):
    assert b._looks_english(text)


def test_selected_whisper_model_is_stored_in_the_project(tmp_path):
    from server.api import _apply_run_options
    from core.project import create_project
    p=create_project('Model',str(tmp_path/'Model.zuckervid'))
    _apply_run_options(p,dict(platform='backstage',backstage_whisper_model='small'))
    assert p.data['settings']['wizard']['backstage_whisper_model']=='small'
    with pytest.raises(ValueError):_apply_run_options(p,dict(platform='backstage',backstage_whisper_model='unknown'))


def test_model_change_invalidates_analysis_without_deleting_cached_transcripts(tmp_path):
    from core.project import create_project
    p=create_project('Fingerprint',str(tmp_path/'Fingerprint.zuckervid'))
    first=b.BackstageAnalysisStage().inputs_fingerprint(p)
    p.data['settings']['wizard']={'backstage_whisper_model':'small'}
    assert b.BackstageAnalysisStage().inputs_fingerprint(p)!=first


def test_volume_decodes_once_and_matches_ffmpeg_means(tmp_path):
    import subprocess
    source=tmp_path/'tone.wav';ffmpeg=b.tool_status()['ffmpeg_path']
    subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','sine=frequency=430:duration=2','-af','volume=0.2',str(source)],check=True)
    audio=b._volume_samples(str(source));assert audio is not None
    assert b._sample_volume(audio,.2,1)==pytest.approx(b._mean_volume(str(source),.2,1),abs=.2)


def test_window_analysis_reuses_one_decode_and_falls_back_on_failure(monkeypatch):
    import numpy as np
    calls=[]
    monkeypatch.setattr(b,'_has_audio',lambda p:True)
    monkeypatch.setattr(b,'detect_music_window',lambda *a:{})
    monkeypatch.setattr(b,'_volume_samples',lambda p:(calls.append(p) or (np.full(40*16000,1800.),16000)))
    monkeypatch.setattr(b,'_mean_volume',lambda *a:(_ for _ in ()).throw(AssertionError('per-window decode')))
    assert len(b._candidate_windows(dict(_path='video',_duration=40)))>1
    assert calls==['video']
    monkeypatch.setattr(b,'_volume_samples',lambda p:None)
    monkeypatch.setattr(b,'_mean_volume',lambda *a:-25)
    assert b._candidate_windows(dict(_path='video',_duration=40))
