"""Local media survives edits and remains reusable, with real playable thumbnails."""
import io
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from core import show_proposals, show_scenes, show_video_assets
from core.project import create_project
from server import api as api_module


@pytest.fixture
def media_workshop(tmp_path, monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT', str(tmp_path/'appdata'))
    project=create_project('Media workshop', str(tmp_path/'media.zuckervid'))
    ffmpeg=shutil.which('ffmpeg');ffprobe=shutil.which('ffprobe')
    assert ffmpeg and ffprobe, 'FFmpeg is required by the export suite'
    monkeypatch.setattr('core.ffmpeg.tool_status',lambda:dict(ffmpeg_path=ffmpeg,ffprobe_path=ffprobe))
    source=project.folder/'original.mp4'
    subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','color=c=blue:s=160x90:r=25:d=1',
                    '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(source)],check=True)
    show_proposals.save(project.artifacts_dir/'show_proposals.json',[],source=str(source),resources=[])
    (project.artifacts_dir/'show_plan.json').write_text(json.dumps(dict(source=str(source),fps=25,edited_duration=4,shots=[dict(start_frame=0,end_frame=100)],words=[])))
    show_scenes.save(project.artifacts_dir/'show_scenes.json',dict(identity={},scenes=[dict(id='s1',name='Scene',start=0,end=4,text='Hello',effects=[],overlays=[],decision='pending')]))
    client=api_module.create_app(project_path=str(project.folder),dev=True).test_client()
    return project,source,client,ffmpeg


def test_local_video_deduplicates_thumbnails_and_appears_in_timeline(media_workshop):
    project,source,client,_=media_workshop
    data=source.read_bytes()
    def upload():return client.post('/api/v1/show/library/media',data={'file':(io.BytesIO(data),'My local video.mp4')},content_type='multipart/form-data')
    response=upload();assert response.status_code==201,response.get_json()
    asset=response.get_json()['asset'];assert Path(asset['path']).read_bytes()==data
    assert upload().get_json()['asset']['id']==asset['id']
    assert len(client.get('/api/v1/show/video-library').get_json()['items'])==1
    thumb=client.get('/api/v1/show/video-library/'+asset['id']+'/thumbnail')
    assert thumb.status_code==200 and thumb.mimetype=='image/jpeg' and thumb.data.startswith(b'\xff\xd8')
    assert client.get('/api/v1/show/video-library/'+asset['id']+'/media',headers={'Range':'bytes=0-31'}).status_code==206
    result=client.post('/api/v1/show/video-library/'+asset['id']+'/place',json=dict(scene_id='s1',at=1,duration=.6))
    assert result.status_code==201,result.get_json()
    item=result.get_json()['proposal'];assert item['duration']==.6 and item['params']['clip']==asset['path']
    assert item['id'] in show_scenes.load(project.artifacts_dir/'show_scenes.json')['scenes'][0]['effects']
    assert client.get('/api/v1/show/source',query_string={'path':str(source)}).status_code==200
    assert client.get('/api/v1/show/source',query_string={'path':'/etc/hosts'}).status_code==404


def test_audio_import_is_an_audio_effect_and_wrong_project_is_rejected(media_workshop):
    project,source,client,ffmpeg=media_workshop
    audio=project.folder/'voice.wav'
    subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','sine=duration=1',str(audio)],check=True)
    assert client.post('/api/v1/show/library/media',json={'path':str(audio),'project_id':'another project'}).status_code==409
    result=client.post('/api/v1/show/library/media',json={'path':str(audio),'project_id':str(project.folder)})
    assert result.status_code==201,result.get_json()
    asset=result.get_json()['asset'];assert asset['media_kind']=='audio'
    assert client.get('/api/v1/show/video-library').get_json()['items']==[]
    assert len(client.get('/api/v1/show/audio-library').get_json()['items'])==1
    placed=client.post('/api/v1/show/video-library/'+asset['id']+'/place',json={'scene_id':'s1','at':0,'duration':.5})
    assert placed.status_code==201,placed.get_json()
    assert placed.get_json()['proposal']['kind']=='jingle'
    assert client.post('/api/v1/show/library/media',data={'file':(io.BytesIO(b'not a video'),'broken.mp4')}).status_code==400
    assert not list((project.folder/'assets/clips').glob('import-*'))


def test_catalog_keeps_original_and_face_versions_after_revert_and_removal(media_workshop):
    project,source,client,_=media_workshop
    face=project.folder/'face.mp4';face.write_bytes(source.read_bytes())
    item=show_proposals.proposal('clip',0,.5,reason='catalog regression',clip=str(source),clip_length=1,clip_start=0,matte='none')
    show_video_assets.remember(project,item)
    item['params'].update(clip=str(face),preview=str(face),clip_original=str(source))
    show_video_assets.remember(project,item)
    show_proposals.save(project.artifacts_dir/'show_proposals.json',[item],resources=[])
    response=client.post('/api/v1/show/faceswap/'+item['id']+'/original',json={})
    assert response.status_code==200,response.get_json()
    assert response.get_json()['params']['preview']==str(source)
    show_proposals.save(project.artifacts_dir/'show_proposals.json',[],resources=[])
    records=client.get('/api/v1/show/video-library').get_json()['items']
    assert {r['path'] for r in records}=={str(source),str(face)}
    assert any(r['face_swapped'] and r['path']==str(face) for r in records)
    face_record=next(r for r in records if r['path']==str(face))
    assert client.post('/api/v1/show/video-library/'+face_record['id']+'/place',json=dict(scene_id='s1',at=0,duration=.3)).status_code==201


def test_concurrent_catalog_writes_do_not_lose_assets(media_workshop):
    project,source,_,_=media_workshop
    items=[dict(id=str(i),params=dict(clip=str(source),clip_length=1),duration=1) for i in range(20)]
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda item:show_video_assets.remember(project,item),items))
    assert len(show_video_assets.catalog(project))==20
