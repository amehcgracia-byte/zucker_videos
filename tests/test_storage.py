import json
from pathlib import Path
import pytest
from core import storage
from core.normalization import cache_key_for_source, _referenced_segment_names
from core.project import create_project

@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / 'home';root.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: root)
    return root


def test_offline_volume_cannot_be_created_or_used(home, monkeypatch):
    monkeypatch.setattr(Path, 'is_mount', lambda _: False)
    storage.preference_path().parent.mkdir(parents=True)
    storage.preference_path().write_text(json.dumps({'data_root':'/Volumes/MissingZuckerDisk/work'}))
    assert not storage.storage_ready()
    with pytest.raises(OSError, match='Connect'):
        storage.data_root()
    with pytest.raises(OSError, match='Connect'):
        storage.save_data_root('/Volumes/MissingZuckerDisk/work')
    assert not Path('/Volumes/MissingZuckerDisk').exists()


def test_verified_migration_preserves_cache_key_but_changed_media_invalidates(home, tmp_path):
    original=home/'ZuckerVideos'/'WizardUploads'/'take.mp4';original.parent.mkdir(parents=True)
    original.write_bytes(b'original media')
    old_signature=original.stat();old_key=cache_key_for_source(original)
    chosen=tmp_path/'external';storage.save_data_root(str(chosen))
    copied=chosen/'WizardUploads'/'take.mp4';copied.parent.mkdir();copied.write_bytes(original.read_bytes())
    (chosen/'migration-signatures.json').write_text(json.dumps({'WizardUploads/take.mp4':dict(bytes=copied.stat().st_size,destination_mtime_ns=copied.stat().st_mtime_ns,source_mtime=old_signature.st_mtime,identity_path=str(original))}))
    assert storage.media_signature(copied)['mtime']==old_signature.st_mtime
    assert cache_key_for_source(copied)==old_key
    copied.write_bytes(b'changed media with different size')
    assert storage.migrated_identity(copied) is None
    assert cache_key_for_source(copied)!=old_key


def test_projects_in_separate_sessions_are_discovered_and_protected(home,tmp_path):
    chosen=storage.save_data_root(str(tmp_path/'external'))
    session=tmp_path/'session';session.mkdir()
    (chosen/'config.json').write_text(json.dumps({'project_root':str(session),'project_roots':[str(session)]}))
    project=create_project('Session song',str(session/'Session song.zuckervid'))
    key='a'*24+'.mp4';cache=chosen/'Cache'/'segments';cache.mkdir(parents=True);(cache/key).write_bytes(b'clip')
    (project.artifacts_dir/'edit_plan.json').write_text(json.dumps({'path':str(cache/key)}))
    assert key in _referenced_segment_names()
    from server.projects import list_projects, projects_root
    assert projects_root()==session
    assert any(p['name']=='Session song' for p in list_projects())


def test_cancelled_project_picker_does_not_change_location(home,monkeypatch):
    import app
    monkeypatch.setattr(app,'_open_folder_dialog',lambda *args: [])
    assert app.DesktopApi().pick_project_location() is None
    assert not storage.preference_path().exists()


def test_repeated_import_reuses_bytes_even_with_a_different_filename(tmp_path):
    import io
    from werkzeug.datastructures import FileStorage
    from server.media_import import save_media_upload
    root=tmp_path/'imports'
    first=save_media_upload(FileStorage(stream=io.BytesIO(b'video data'),filename='first.mp4'),root)
    second=save_media_upload(FileStorage(stream=io.BytesIO(b'video data'),filename='different.mp4'),root)
    assert first==second
    assert len(list(root.glob('*.mp4')))==1
    assert not list(root.glob('*.part'))


def test_interrupted_import_does_not_publish_partial_media(tmp_path):
    from types import SimpleNamespace
    from server.media_import import save_media_upload
    class Broken:
        def read(self, amount): raise OSError('interrupted')
    with pytest.raises(OSError,match='interrupted'):
        save_media_upload(SimpleNamespace(stream=Broken(),filename='take.mp4'),tmp_path)
    assert not list(tmp_path.glob('*.mp4'))
    assert not list(tmp_path.glob('*.part'))


def test_new_import_keeps_readable_filename_and_conflicting_contents_separate(tmp_path):
    import io
    from werkzeug.datastructures import FileStorage
    from server.media_import import save_media_upload
    first=save_media_upload(FileStorage(stream=io.BytesIO(b'first'),filename='My song.mp3'),tmp_path)
    other=save_media_upload(FileStorage(stream=io.BytesIO(b'second'),filename='My song.mp3'),tmp_path)
    assert first.name=='My song.mp3' and first!=other
    assert first.read_bytes()==b'first' and other.read_bytes()==b'second'


def test_selected_storage_covers_temporary_files_and_cached_model(home,tmp_path,monkeypatch):
    import os,tempfile
    root=storage.save_data_root(str(tmp_path/'external'))
    monkeypatch.setattr(tempfile,'tempdir',None)
    for key in ('TMPDIR','TMP','TEMP','HF_HOME','NUMBA_CACHE_DIR','MPLCONFIGDIR','XDG_CACHE_HOME'):
        monkeypatch.setenv(key,os.environ.get(key,''))
    folder=storage.configure_working_storage()
    assert Path(tempfile.gettempdir()).is_relative_to(root)
    assert Path(os.environ['HF_HOME']).is_relative_to(root)
    model=root/'Cache'/'WhisperModels'/'tiny';model.mkdir(parents=True)
    for name in ('model.bin','config.json','tokenizer.json','vocabulary.txt'):(model/name).write_text('present')
    assert storage.whisper_model_path('tiny')==str(model)


def test_session_media_outside_shared_storage_keeps_verified_identity(home,tmp_path):
    root=storage.save_data_root(str(tmp_path/'shared'))
    media=tmp_path/'session'/'take.mp4';media.parent.mkdir();media.write_bytes(b'known')
    (root/'migration-signatures.json').write_text(json.dumps({str(media):dict(bytes=5,destination_mtime_ns=media.stat().st_mtime_ns,source_mtime=123.,identity_path='/old/project/take.mp4')}))
    assert storage.media_signature(media)=={'size':5,'mtime':123.}
    assert storage.migrated_identity(media)[0]=='/old/project/take.mp4'


def test_first_launch_shows_location_setup_before_creating_media_home(home,monkeypatch):
    import app,sys
    from types import SimpleNamespace
    shown=[]
    monkeypatch.setattr(app,'parse_args',lambda:SimpleNamespace(dev=False,selftest=False,webgl_probe=False,operator_avoidance_probe=False,project=None))
    monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(create_window=lambda *a,**kw:shown.append(kw),start=lambda:None))
    app.main()
    assert 'choose_storage' in shown[0]['html']
    assert not (home/'ZuckerVideos').exists()


def test_switching_storage_keeps_existing_project_discovery(home,tmp_path):
    old=storage.save_data_root(str(tmp_path/'old'))
    create_project('Existing',str(old/'Projects'/'Existing.zuckervid'))
    storage.save_data_root(str(tmp_path/'new'))
    from server.projects import list_projects
    assert any(p['name']=='Existing' for p in list_projects())


def test_matching_relocated_inputs_reuses_existing_project(home, tmp_path):
    from core.project import file_record
    from server.projects import find_project_by_inputs
    root=storage.save_data_root(str(tmp_path/'external'))
    media=root/'WizardUploads'/'take.mp4';media.parent.mkdir();media.write_bytes(b'video')
    old_mtime=12345.6789
    (root/'migration-signatures.json').write_text(json.dumps({str(media):dict(bytes=media.stat().st_size,destination_mtime_ns=media.stat().st_mtime_ns,source_mtime=old_mtime,identity_path='/old/take.mp4')}))
    project=create_project('Existing',str(root/'Projects'/'Existing.zuckervid'))
    project.data['inputs']['videos']=[file_record(str(media))];project.save()
    assert find_project_by_inputs('',None,[str(media)]).folder==project.folder


def test_registered_previous_session_project_can_be_deleted(home,tmp_path):
    from server.projects import delete_project_folder
    root=storage.save_data_root(str(tmp_path/'external'))
    old=tmp_path/'old-session';new=tmp_path/'new-session';old.mkdir();new.mkdir()
    (root/'config.json').write_text(json.dumps({'project_root':str(new),'project_roots':[str(old),str(new)]}))
    project=create_project('Old session',str(old/'Old.zuckervid'))
    delete_project_folder(str(project.folder))
    assert not project.folder.exists()
    unrelated=create_project('Unrelated',str(tmp_path/'unregistered'/'Unrelated.zuckervid'))
    with pytest.raises(ValueError):delete_project_folder(str(unrelated.folder))
    assert unrelated.folder.exists()


def test_external_cleanup_keeps_recoverable_trash_on_external_volume(home,tmp_path,monkeypatch):
    from core import trash
    external=tmp_path/'mounted';external.mkdir();clip=external/'cache.mp4';clip.write_bytes(b'cached video')
    monkeypatch.setattr(trash,'_volume_root',lambda p: home if p==home else external)
    target=trash.move_to_trash(clip)
    assert target==external/'.ZuckerEditorTrash'/'cache.mp4'
    assert target.read_bytes()==b'cached video'
    assert not (home/'.Trash').exists()
