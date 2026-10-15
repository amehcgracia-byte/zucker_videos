import json
import subprocess

import numpy as np
from PIL import Image
import pytest

from core import show_scenes, show_proposals
from core.project import create_project
from core.show import preview_scene
from core.ffmpeg import tool_status
from server.api import create_app


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    project=create_project('Stickers',str(tmp_path/'Stickers.zuckervid'))
    root=project.folder/'assets/show-library';root.mkdir(parents=True)
    image=root/'sun.png';Image.new('RGBA',(40,40),(255,0,0,255)).save(image)
    scene=dict(id='scene-1',start=0,end=3,decision='accepted',effects=[],overlays=[],name='Scene 1',text='',proposal='')
    show_scenes.save(project.artifacts_dir/'show_scenes.json',{'scenes':[scene]})
    show_proposals.save(project.artifacts_dir/'show_proposals.json',[])
    plan={'source':str(tmp_path/'source.mp4'),'fps':25,'source_duration':3,'edited_duration':3,'cuts':0,'words':[],'shots':[{'start_frame':0,'end_frame':75,'focus':[.5,.5],'zoom':1,'words':[]}]}
    (project.artifacts_dir/'show_plan.json').write_text(json.dumps(plan))
    return project,image,create_app(str(project.folder),dev=True).test_client()


def test_sticker_id_timing_quote_and_removal_persist(studio):
    project,image,client=studio
    route='/api/v1/show/scenes/scene-1/overlays'
    sticker=dict(id='chosen-sun',asset=str(image),at=1.2,duration=.08,selected_text='Spanish',x=10,y=10,width=20)
    response=client.post(route,json={'overlays':[sticker]})
    assert response.status_code==200
    saved=response.get_json()['overlays'][0]
    assert (saved['id'],saved['at'],saved['duration'],saved['selected_text'])==('chosen-sun',1.2,.08,'Spanish')
    assert show_scenes.overlay_items({'scenes':[response.get_json()]})[0]['duration']==.08
    bad=client.post(route,json={'overlays':[{**sticker,'at':2.9,'duration':1}]})
    assert bad.status_code==400
    assert show_scenes.load(project.artifacts_dir/'show_scenes.json')['scenes'][0]['overlays'][0]['duration']==.08
    removed=client.post(route,json={'overlays':[]})
    assert removed.get_json()['decision']=='modified'
    assert show_scenes.overlay_items({'scenes':[removed.get_json()]})==[]


def test_retired_library_is_hidden_without_breaking_projects_and_can_restore(studio, monkeypatch, tmp_path):
    _,image,client=studio
    from core.gag_library import library_dir
    old=library_dir()/'Painted/Ceras/old.png';old.parent.mkdir(parents=True);Image.new('RGBA',(20,20)).save(old)
    visible=client.get('/api/v1/show/library').get_json()
    assert str(old) not in [x['path'] for x in visible['items']]
    assert visible['retired_count']==1
    # A saved project using the retired picture still has access to its exact bytes.
    assert client.get('/api/v1/show/library/file',query_string={'path':str(old)}).status_code==200
    assert client.post('/api/v1/show/library/visibility',json={'path':str(old),'retired':False}).status_code==200
    assert str(old) in [x['path'] for x in client.get('/api/v1/show/library').get_json()['items']]
    assert client.post('/api/v1/show/library/visibility',json={'path':str(image),'retired':True}).status_code==200
    assert image.exists() and client.get('/api/v1/show/library/file',query_string={'path':str(image)}).status_code==200
    assert client.post('/api/v1/show/library/visibility',json={'path':'/private/not-library.png'}).status_code==404


def test_sticker_reaches_real_preview_only_inside_its_interval(studio, tmp_path):
    project,image,client=studio
    ffmpeg=tool_status()['ffmpeg_path']
    subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','color=c=gray:s=320x180:r=25:d=3','-f','lavfi','-i','sine=frequency=300:duration=3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(tmp_path/'source.mp4')],check=True)
    client.post('/api/v1/show/scenes/scene-1/overlays',json={'overlays':[dict(id='sun',asset=str(image),at=1,duration=.5,x=10,y=10,width=20,action='none')]})
    # Other unfinished effects must not prevent reviewing this particular sticker.
    character=show_proposals.proposal('character',2,0,reason='incomplete',source='missing.mp4')
    show_proposals.save(project.artifacts_dir/'show_proposals.json',[character])
    document=show_scenes.load(project.artifacts_dir/'show_scenes.json');document['scenes'][0]['effects']=[character['id']];show_scenes.save(project.artifacts_dir/'show_scenes.json',document)
    movie=preview_scene(project,'scene-1',width=320,height=180,only_sticker_id='sun')
    raw=subprocess.run([ffmpeg,'-v','error','-i',str(movie),'-f','rawvideo','-pix_fmt','rgb24','-'],check=True,capture_output=True).stdout
    frames=np.frombuffer(raw,dtype=np.uint8).reshape(-1,180,320,3)
    assert frames[12,40,55,0]<180
    assert frames[30,40,55,0]>200 and frames[30,40,55,1]<80
    assert frames[50,40,55,0]<180
