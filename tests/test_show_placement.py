"""Placement uses the edited scene clock and survives all the way into render items."""
import json

import pytest
from PIL import Image

from core import gags, show_proposals, show_scenes
from core.project import create_project
from server.api import create_app


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT', str(tmp_path/'data'))
    project = create_project('Placement', str(tmp_path/'Placement.zuckervid'))
    item = show_proposals.proposal('drawing', 12, 2, reason='Your picture', kind='drawing', subject='sofa')
    # Model an existing project with timing accidentally outside its owning scene.
    item['at'] = 53
    show_proposals.save(project.artifacts_dir/'show_proposals.json', [item])
    (project.artifacts_dir/'show_plan.json').write_text(json.dumps({'edited_duration':60,'shots':[]}))
    show_scenes.save(project.artifacts_dir/'show_scenes.json', {'scenes':[dict(id='scene-2', start=10, end=20, effects=[item['id']], decision='accepted')]})
    return project, item['id'], create_app(str(project.folder), dev=True).test_client()


def test_word_placement_repairs_outside_effect_and_invalidates_scene(desk):
    project, ident, client = desk
    response = client.patch(f'/api/v1/show/review/{ident}', json={'at':13.25,'duration':1.18,'selected_text':'touching his balls'})
    assert response.status_code == 200
    stored = show_proposals.load(project.artifacts_dir/'show_proposals.json')['proposals'][0]
    assert (stored['at'],stored['duration'],stored['params']['selected_text']) == (13.25,1.18,'touching his balls')
    scene = show_scenes.load(project.artifacts_dir/'show_scenes.json')['scenes'][0]
    assert scene['decision'] == 'modified'


@pytest.mark.parametrize('changes', [{'at':100}, {'at':9}, {'at':19,'duration':2}, {'at':12,'duration':0}, {'at':float('nan')}, {'image_center':[float('inf'),.5]}])
def test_invalid_placement_does_not_mutate_saved_project(desk, changes):
    project, ident, client = desk
    before = (project.artifacts_dir/'show_proposals.json').read_bytes()
    assert client.patch(f'/api/v1/show/review/{ident}', json=changes).status_code == 400
    assert (project.artifacts_dir/'show_proposals.json').read_bytes() == before


def test_dragged_picture_position_reaches_render_recipe_without_mirroring(desk, tmp_path):
    project, ident, client = desk
    image = tmp_path/'image.png'
    Image.new('RGBA',(40,20),(255,0,0,255)).save(image)
    response = client.patch(f'/api/v1/show/review/{ident}', json={'at':12,'duration':.08,'image_center':[.9,.15], 'selected_text':'Spanish'})
    assert response.status_code == 200
    item = response.get_json()
    item['status']='approved'
    item['params']['picture']=str(image)
    items,_ = gags.make_items(show_proposals.render_inputs([item])[2], tmp_path/'render', face='left')
    assert len(items)==1
    assert (items[0]['x'],items[0]['y']) == (.9,.15)
    assert (items[0]['at'],items[0]['duration']) == (12,.08)


def test_individual_preview_reaches_renderer_without_a_progress_callback(desk, tmp_path, monkeypatch):
    from core import show
    project, ident, client = desk
    client.patch(f'/api/v1/show/review/{ident}', json={'at':12,'duration':1})
    plan_path=project.artifacts_dir/'show_plan.json'
    plan=json.loads(plan_path.read_text())
    plan.update(source=str(tmp_path/'talk.mp4'),fps=25,shots=[dict(start_frame=0,end_frame=1500,focus=[.5,.5])])
    plan_path.write_text(json.dumps(plan))
    calls=[]
    def render(source, part, output, **kwargs):
        calls.append(kwargs.get('progress'))
        output.write_bytes(b'preview')
    monkeypatch.setattr(show,'render_show',render)
    monkeypatch.setattr(gags,'make_items',lambda *args,**kwargs:([],[]))
    import core.show_bridge as bridge
    monkeypatch.setattr(bridge,'studio_layers',lambda *args,**kwargs:([],[]))
    audio_calls=[]
    def finish(*args):
        audio_calls.append(1)
        if len(audio_calls)==1:
            raise RuntimeError('audio failure')
    monkeypatch.setattr(show,'_lay_jingles',finish)
    with pytest.raises(RuntimeError,match='audio failure'):
        show.preview_proposal(project,ident)
    # A failed late audio pass must not become a successful cache hit.
    assert show.preview_proposal(project,ident).read_bytes()==b'preview'
    assert calls == [None, None]


def test_incomplete_character_can_be_retimed_before_its_voice_is_chosen(desk):
    project, ident, client = desk
    path=project.artifacts_dir/'show_proposals.json'
    stored=show_proposals.load(path)
    item=stored['proposals'][0]
    item.update(kind='character',duration=0)
    item['params'].update(source='talk.mp4',region=None,voice=None)
    show_proposals.save(path,[item])
    response=client.patch(f'/api/v1/show/review/{ident}',json={'at':12})
    assert response.status_code==200
    assert set(response.get_json()['needs'])=={'region','voice'}


def test_manually_sized_picture_can_extend_outside_frame(tmp_path):
    image=tmp_path/'tall.png'
    Image.new('RGBA',(20,100),(255,0,0,255)).save(image)
    recipe=dict(kind='drawing',picture=str(image),at=1,duration=.2,size=.5,image_center=[-.1,.5])
    items,_=gags.make_items([recipe],tmp_path/'pictures')
    assert items[0]['width']==.5
    assert (items[0]['x'],items[0]['y'])==(-.1,.5)
