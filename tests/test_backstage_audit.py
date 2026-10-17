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
