import io
import json
from pathlib import Path
import pytest
from core.project import create_project
from core.project_library import ProjectLibrary, remember_project, catalogue, reuse_asset


def library(tmp_path, name='one'):
    return ProjectLibrary(create_project(name, str(tmp_path / (name+'.zuckervid'))).folder)


def test_import_deduplicates_by_full_content_and_stays_portable(tmp_path):
    lib=library(tmp_path)
    first=lib.import_stream(io.BytesIO(b'pixels'), 'one.png')
    again=lib.import_stream(io.BytesIO(b'pixels'), 'renamed.jpg')
    assert again['reused'] and again['id']==first['id']
    assert len(lib.inventory()['assets'])==1
    assert (lib.root/first['path']).read_bytes()==b'pixels'
    assert not Path(first['path']).is_absolute()
    assert not list(lib.root.rglob('*.part'))


def test_failed_or_empty_import_does_not_publish_asset(tmp_path):
    lib=library(tmp_path)
    with pytest.raises(ValueError): lib.import_stream(io.BytesIO(), 'empty.png')
    with pytest.raises(ValueError): lib.import_stream(io.BytesIO(b'data'), '../bad.exe')
    assert lib.inventory()['assets']==[]
    assert not list(lib.root.rglob('*.part'))


def test_reuse_copies_once_for_portability_without_global_media(tmp_path):
    a,b=library(tmp_path,'a'),library(tmp_path,'b')
    asset=a.import_stream(io.BytesIO(b'photo'), 'photo.png',origin='User photograph')
    index=tmp_path/'index'/'projects.json';remember_project(index,a.project)
    reused=reuse_asset(b,index,str(a.project),asset['id'])
    assert reused['sha256']==asset['sha256']
    assert (b.root/reused['path']).read_bytes()==b'photo'
    assert reuse_asset(b,index,str(a.project),asset['id'])['reused']
    assert len(list(index.parent.iterdir()))==2 # index + lock, no media
    (a.root/asset['path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'): reuse_asset(b,index,str(a.project),asset['id'])
    assert len(catalogue(index))==1


def test_cleanup_never_selects_originals_and_requires_current_preview(tmp_path, monkeypatch):
    import core.project_library as module
    lib=library(tmp_path);asset=lib.import_stream(io.BytesIO(b'keep'), 'keep.png')
    previews=lib.root/'previews';previews.mkdir();(previews/'a.webp').write_bytes(b'preview')
    report=lib.inventory();assert report['cleanup_bytes']==7
    assert [a['path'] for a in report['cleanup']]==['previews/a.webp']
    (previews/'b.webp').write_bytes(b'new')
    with pytest.raises(ValueError,match='changed'): lib.clean_previews(report['cleanup_token'])
    trash=tmp_path/'trash';trash.mkdir()
    def move(p):
        out=trash/p.name;p.rename(out);return out
    monkeypatch.setattr(module,'move_to_trash',move)
    result=lib.clean_previews(lib.inventory()['cleanup_token'])
    assert result['moved_bytes']==10 and len(list(trash.iterdir()))==2
    assert (lib.root/asset['path']).read_bytes()==b'keep'


def test_symlink_escape_and_corrupt_manifest_are_not_overwritten(tmp_path):
    lib=library(tmp_path);lib.root.mkdir();outside=tmp_path/'outside';outside.mkdir()
    (lib.root/'assets').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError):lib.import_stream(io.BytesIO(b'data'),'file.png')
    lib.manifest.write_text('{broken')
    with pytest.raises(ValueError):lib.inventory()
    assert lib.manifest.read_text()=='{broken'


def test_corrupted_existing_asset_is_repaired_from_content(tmp_path):
    lib=library(tmp_path);a=lib.import_stream(io.BytesIO(b'original'), 'one.png')
    (lib.root/a['path']).write_bytes(b'bad')
    result=lib.import_stream(io.BytesIO(b'original'),'one.png')
    assert not result['reused'] and (lib.root/a['path']).read_bytes()==b'original'


def test_library_routes_import_catalogue_and_busy_cleanup(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from flask import Flask
    from server.project_library_api import register_library_routes
    project=create_project('api',str(tmp_path/'api.zuckervid'))
    state=SimpleNamespace(project=project,wizard=SimpleNamespace(status=lambda:{'status':'running'}),
                          engine=SimpleNamespace(status=lambda p:{'busy':False}),
                          composition=SimpleNamespace(status=lambda p:{}))
    app=Flask(__name__);register_library_routes(app,state,lambda:tmp_path/'index'/'projects.json')
    client=app.test_client()
    assert client.get('/api/v1/library').status_code==200
    response=client.post('/api/v1/library/import',data={'file':(io.BytesIO(b'asset'),'photo.png')})
    assert response.status_code==201
    report=client.get('/api/v1/library').get_json()
    assert report['asset_bytes']==5 and len(report['catalogue'])==1
    assert client.post('/api/v1/library/clean-previews',json={'token':report['cleanup_token']}).status_code==409
    state.wizard.status=lambda:{'status':'idle'}
    assert client.post('/api/v1/library/clean-previews',json={'token':'old'}).status_code==400
    assert client.post('/api/v1/library/clean-previews',json={'token':report['cleanup_token']}).status_code==200
    assert client.post('/api/v1/library/reuse',json={'project':'/unknown','id':response.get_json()['id']}).status_code==400
    state.project=None
    assert client.get('/api/v1/library').status_code==400
