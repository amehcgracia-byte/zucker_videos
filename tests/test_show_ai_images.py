import json
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from core import show_ai_images as ai, show_scenes, show_proposals
from core.project import create_project
from server.api import create_app


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    p=create_project('AI',str(tmp_path/'AI.zuckervid'))
    (p.artifacts_dir/'show_plan.json').write_text(json.dumps({'edited_duration':10,'shots':[],'words':[]}))
    show_proposals.save(p.artifacts_dir/'show_proposals.json',[])
    show_scenes.save(p.artifacts_dir/'show_scenes.json',{'identity':{},'scenes':[{'id':'scene-1','name':'Scene 1','text':'lazy','start':0,'end':10,'effects':[],'overlays':[],'decision':'pending','proposal':''}]})
    return p,create_app(str(p.folder),dev=True).test_client()


def request():
    return dict(scene_id='scene-1',direction='An adult slouching on a sofa',selected_text='lazy',at=2,duration=2,style='crayon')


def test_generated_image_is_only_applied_after_review_and_stays_inside_project(setup,monkeypatch):
    p,c=setup
    def fake(project,jid):
        job=ai.load(project,jid)
        Image.new('RGBA',(80,60),(255,0,0,100)).save(ai.folder(project,jid)/'image.png')
        job['status']='ready';ai.save(ai.folder(project,jid)/'request.json',job)
    monkeypatch.setattr(ai,'run',fake)
    response=c.post('/api/v1/show/ai/images',json=request());assert response.status_code==202
    jid=response.get_json()['id']
    for _ in range(100):
        status=c.get(f'/api/v1/show/ai/images/{jid}').get_json()
        if status['status']=='ready':break
        time.sleep(.01)
    assert status['status']=='ready'
    history=c.get('/api/v1/show/ai/images?scene_id=scene-1').get_json()['items']
    assert [item['id'] for item in history]==[jid]
    assert c.get('/api/v1/show/ai/images?scene_id=another').get_json()['items']==[]
    assert show_proposals.load(p.artifacts_dir/'show_proposals.json')['proposals']==[]
    assert c.get(status['url']).status_code==200
    applied=c.post(f'/api/v1/show/ai/images/{jid}/apply').get_json()
    assert applied['status']=='proposed' and applied['params']['image_engine']=='ChatGPT via Codex'
    assert p.folder in Path(applied['params']['picture']).parents
    c.patch(f"/api/v1/show/review/{applied['id']}",json={'size':.39,'effect':'wobble','at':2.1,'duration':1.2})
    again=c.post(f'/api/v1/show/ai/images/{jid}/apply').get_json()
    assert again['params']['size']==.39 and again['params']['effect']=='wobble'
    assert again['at']==2.1 and again['duration']==1.2
    assert again['id']==applied['id']
    assert len(show_proposals.load(p.artifacts_dir/'show_proposals.json')['proposals'])==1
    assert c.get('/api/v1/show/library').get_json()['items'][0]['folder']=='ChatGPT images'


def test_only_one_generation_per_project_and_failed_job_cannot_be_applied(setup,monkeypatch):
    p,c=setup;release=threading.Event()
    def fake(project,jid):
        release.wait(5)
        job=ai.load(project,jid);job.update(status='failed',error='Limit reached');ai.save(ai.folder(project,jid)/'request.json',job)
    monkeypatch.setattr(ai,'run',fake)
    response=c.post('/api/v1/show/ai/images',json=request());jid=response.get_json()['id']
    try:
        assert c.post('/api/v1/show/ai/images',json=request()).status_code==409
        assert c.post(f'/api/v1/show/ai/images/{jid}/apply').status_code==409
        assert c.get(f'/api/v1/show/ai/images/{jid}/image').status_code==404
    finally:release.set()


def test_art_prompt_separates_drawing_technique_from_character_age_and_reference(setup):
    p,_=setup
    scene={'id':'scene-1','start':0,'end':10}
    j=ai.create(p,scene,request())
    text=ai.prompt_for(j)
    assert 'ADULT' not in text and 'adults into children' in text and 'No anime' in text
    j['use_face']=True
    assert 'SAME ADULT' in ai.prompt_for(j)
    with pytest.raises(ValueError):ai.folder(p,'../../private')
    with pytest.raises(ValueError):ai.create(p,scene,{**request(),'direction':''})


def test_missing_codex_fails_explicitly_without_falling_back(setup,monkeypatch):
    p,_=setup;j=ai.create(p,{'id':'scene-1','start':0,'end':10},request())
    monkeypatch.setattr(ai.shutil,'which',lambda _:None)
    ai.run(p,j['id'])
    failed=ai.load(p,j['id'])
    assert failed['status']=='failed' and 'local painter was not used' in failed['error']


def test_provider_cannot_import_a_file_outside_the_request_workspace(setup,monkeypatch,tmp_path):
    p,_=setup;j=ai.create(p,{'id':'scene-1','start':0,'end':10},request())
    outside=tmp_path/'private.png';Image.new('RGBA',(10,10),(0,0,0,50)).save(outside)
    class Proc:
        returncode=0
        def __init__(self,command,**kwargs):
            self.command=command
        def communicate(self,data,timeout):
            assert 'Use the BUILT-IN image_gen tool' in data.decode()
            (ai.folder(p,j['id'])/'result.json').write_text(json.dumps({'image_path':str(outside)}))
    monkeypatch.setattr(ai.subprocess,'run',lambda *a,**kw: ai.subprocess.CompletedProcess(a,0,'Logged in using ChatGPT',''))
    monkeypatch.setattr(ai.subprocess,'Popen',Proc)
    ai.run(p,j['id'],executable='codex')
    assert ai.load(p,j['id'])['status']=='failed'
    assert 'outside this request' in ai.load(p,j['id'])['error']


def test_refinement_keeps_prior_picture_and_direction_and_rejects_unfinished_references(setup):
    p,_=setup
    scene={'id':'scene-1','start':0,'end':10}
    first=ai.create(p,scene,request())
    with pytest.raises(ValueError,match='completed image'):
        ai.create(p,scene,{**request(),'previous_id':first['id'],'direction':'Make it cruder'})
    first['status']='ready';ai.save(ai.folder(p,first['id'])/'request.json',first)
    second=ai.create(p,scene,{**request(),'previous_id':first['id'],'direction':'Make it cruder'})
    assert second['previous_id']==first['id'] and second['previous_direction']==first['direction']
    assert 'Make it cruder' in ai.prompt_for(second) and 'earlier illustration' in ai.prompt_for(second)


def test_api_key_auth_is_not_silently_used_for_chatgpt_requests(setup,monkeypatch):
    p,_=setup;j=ai.create(p,{'id':'scene-1','start':0,'end':10},request())
    monkeypatch.setattr(ai.subprocess,'run',lambda *a,**kw: ai.subprocess.CompletedProcess(a,0,'Logged in using an API key',''))
    ai.run(p,j['id'],executable='codex')
    failed=ai.load(p,j['id'])
    assert failed['status']=='failed' and 'API-key billing' in failed['error']
