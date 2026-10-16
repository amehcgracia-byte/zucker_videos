import json
from pathlib import Path
import pytest
from PIL import Image
from core import show_caption_layout as layout, show_proposals, show_scenes, show_ai_images, gags
from core.project import create_project
from core.show import _publish
from server.api import create_app


@pytest.fixture
def board(tmp_path,monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    p=create_project('Captions',str(tmp_path/'Captions.zuckervid'))
    video=tmp_path/'video.mp4';video.write_bytes(b'fixture')
    p.data['inputs']['videos']=[{'path':str(video)}];p.save()
    words=[dict(text='This',start=.2,end=.6),dict(text='guy.',start=.6,end=1.2)]
    plan=dict(source=str(video),source_duration=3,edited_duration=3,cuts=1,fps=25,style='choe',removed_words={},words=words,
              shots=[dict(start_frame=0,end_frame=75,words=[0,1],zoom=1,focus=[.5,.5])])
    (p.artifacts_dir/'show_plan.json').write_text(json.dumps(plan))
    show_proposals.save(p.artifacts_dir/'show_proposals.json',[],source=str(video),resources=[])
    show_scenes.save(p.artifacts_dir/'show_scenes.json',dict(identity={},scenes=[dict(id='s1',name='One',text='This guy.',start=0,end=3,effects=[],overlays=[],decision='pending',proposal='')]))
    return p,create_app(str(p.folder),dev=True).test_client(),plan


def test_live_positions_survive_reopen_and_reach_final_cue_track(board):
    p,c,plan=board
    data=c.get('/api/v1/show/scenes').get_json();cue=data['scenes'][0]['captions'][0]
    request=dict(project_id=str(p.folder),revision=data['caption_revision'],changes=dict(horizontal=21,vertical=35,size=123))
    result=c.patch('/api/v1/show/captions/'+cue['id'],json=request)
    assert result.status_code==200
    assert result.get_json()['cue']['style_override']['size']==123
    assert c.patch('/api/v1/show/captions/'+cue['id'],json=request).status_code==409
    import time
    _publish(p,plan,p.exports_dir/'done.mp4',style_name='choe',language='en',started=time.monotonic())
    final=json.loads((p.folder/'cue_track.json').read_text())['cues'][0]
    assert final['style_override']['horizontal']==21 and final['style_override']['vertical']==35
    assert final['start']==cue['start'] and final['end']==cue['end']
    assert c.get('/api/v1/show/scenes').get_json()['scenes'][0]['captions'][0]['style_override']==final['style_override']


def test_stale_project_and_invalid_values_are_rejected(board):
    p,c,_=board;data=c.get('/api/v1/show/scenes').get_json();key=data['scenes'][0]['captions'][0]['id']
    assert c.patch('/api/v1/show/captions/'+key,json=dict(project_id='other',revision=0,changes={})).status_code==409
    for changes in [dict(size=9999),dict(text='unsafe contract'),dict(horizontal=True)]:
        assert c.patch('/api/v1/show/captions/'+key,json=dict(project_id=str(p.folder),revision=0,changes=changes)).status_code==400


def test_lettering_is_exact_artwork_after_explicit_apply_and_stale_text_requires_a_new_draft(board,tmp_path,monkeypatch):
    p,c,_=board
    scene=show_scenes.load(p.artifacts_dir/'show_scenes.json')['scenes'][0]
    job=show_ai_images.create(p,scene,dict(direction='Red marker protest lettering',purpose='lettering',lettering_text='This guy.',style='marker',at=.2,duration=1,image_center=[.64,.31],size=.63))
    prompt=show_ai_images.prompt_for(job)
    assert 'EXACTLY' in prompt and 'This guy.' in prompt and 'no lettering' not in prompt.lower()
    image=show_ai_images.folder(p,job['id'])/'image.png';Image.new('RGBA',(100,30),(210,30,40,150)).save(image)
    job['status']='ready';show_ai_images.save(image.parent/'request.json',job)
    assert not show_proposals.load(p.artifacts_dir/'show_proposals.json')['proposals']
    result=c.post(f"/api/v1/show/ai/images/{job['id']}/apply")
    assert result.status_code==200
    item=result.get_json();assert item['kind']=='word' and item['status']=='proposed'
    assert item['params']['image_center']==[.64,.31] and item['params']['size']==.63
    items,notes=gags.make_items([{**item['params'], 'at':item['at'],'duration':item['duration']}],tmp_path/'render',allow_generation=False)
    assert not notes and len(items)==1
    assert Image.open(items[0]['image']).tobytes()==Image.open(image).tobytes()
    changed=c.patch('/api/v1/show/review/'+item['id'],json=dict(text='Changed',status='approved'))
    assert changed.status_code==400


def test_partial_lettering_replacement_does_not_remove_the_rest_of_a_caption():
    cue=dict(id='c1',start=1,end=4,lines=['This guy.'])
    p=dict(id='w',kind='word',status='approved',at=2,duration=1,params=dict(replaces_caption='c1',text='This guy.',lettering_text='This guy.',picture='saved.png'))
    assert [(c['start'],c['end']) for c in layout.without_artwork([cue],[p],{'w'})]==[(1,2),(3,4)]
    assert layout.without_artwork([cue],[p],set())==[cue]


def test_live_caption_clock_handles_character_insertions_without_drifting():
    import subprocess
    js=Path('web/show_live_captions.js').read_text()
    program="const assert=require('node:assert/strict');const window={};\n"+js+"""
const clock=window.ShowCaptionClock;
assert.equal(clock.shift(2,[[1,3]]),5);
assert.equal(clock.unshift(2,[[1,3]]),1);
assert.equal(clock.unshift(5,[[1,3]]),2);
assert.equal(clock.unshift(9,[[1,3],[3,2]]),4);
"""
    subprocess.run(['node','-e',program],check=True,capture_output=True,text=True)


def test_realistic_edit_uses_identity_without_doodle_instructions(board):
    p,c,_=board
    job=show_ai_images.create(p,dict(id='s1',start=0,end=3),dict(direction='Me with long hair',purpose='photo_edit',at=.2,duration=1))
    assert job['use_face'] and job['purpose']=='photo_edit'
    prompt=show_ai_images.prompt_for(job)
    assert 'photorealistically' in prompt and 'long hair' in prompt
    assert 'five-minute drawing' not in prompt
    with pytest.raises(ValueError):
        show_ai_images.create(p,dict(id='s1',start=0,end=3),dict(direction='Long hair',purpose='photo_edit',engine='claude'))


def test_clay_sample_is_placed_on_new_words_not_original_recording_clock(board):
    from core import show_clay, show_bridge
    p,c,plan=board
    request=dict(project_id=str(p.folder),scene_id='s1',at=.2,end=1.2,mode='bubble')
    result=c.post('/api/v1/show/clay/sample',json=request)
    assert result.status_code==200, result.get_json()
    placed=result.get_json()['placements'][-1]
    assert placed['edited'][0][:2]==[.2,1.2]
    layers,report=show_bridge.studio_layers(p.folder,plan)
    assert report[-1]['status']=='applied', report
    import numpy as np
    frame=np.zeros((180,320,3),dtype=np.uint8)
    layers.draw(frame,.7)
    assert frame.max()>0
    data=show_clay.ClayStore(p.folder).load()
    assert data['stories'][0]['story']['source_start']==.2
    assert data['stories'][0]['story']['duration']==pytest.approx(1)
    assert c.post('/api/v1/show/clay/sample',json={**request,'project_id':'old'}).status_code==409
    assert c.post('/api/v1/show/clay/sample',json={**request,'end':9}).status_code==400
