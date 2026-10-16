from pathlib import Path
import shutil
import pytest
from core.project import create_project
from server import projects


def test_project_delete_keeps_the_only_source_recording_recoverable(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path))
    root=tmp_path/'Projects';root.mkdir()
    monkeypatch.setattr(projects,'project_locations',lambda:[root])
    project=create_project('Precious',str(root/'Precious.zuckervid'))
    video=project.folder/'inputs'/'videos'/'original.mp4';video.parent.mkdir(parents=True,exist_ok=True);video.write_bytes(b'only original recording')
    result=projects.delete_project_folder(str(project.folder))
    trash=Path(result['trash_path'])
    assert not project.folder.exists()
    assert (trash/'inputs/videos/original.mp4').read_bytes()==b'only original recording'
    # The advertised recovery really works by moving the intact folder back.
    shutil.move(str(trash),str(project.folder))
    assert video.read_bytes()==b'only original recording'


def test_trash_failure_never_falls_back_to_permanent_deletion(tmp_path,monkeypatch):
    root=tmp_path/'Projects';root.mkdir()
    monkeypatch.setattr(projects,'project_locations',lambda:[root])
    project=create_project('Keep',str(root/'Keep.zuckervid'))
    def denied(path): raise PermissionError('Trash denied')
    monkeypatch.setattr(projects,'move_to_trash',denied)
    with pytest.raises(PermissionError):projects.delete_project_folder(str(project.folder))
    assert (project.folder/'project.json').is_file()
