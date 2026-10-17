from pathlib import Path
import io
import json
import wave

import numpy as np
from PIL import Image
import pytest

from core.show_pixel_layers import LookingPixels


def test_original_eyes_move_without_redrawing_and_return_without_looping():
    pixels=np.ones((80,120,4),np.float32)
    pixels[...,:3]=[.9,.7,.6]                     # skin
    pixels[30:46,30:70,:3]=1.0                    # the white of the eye
    iris=np.array([.2,.3,.6],np.float32)
    pixels[34:42,46:54,:3]=iris                   # the iris, in the middle
    layer=LookingPixels(pixels,[{'rect':[30/119,30/79,39/119,15/79]}],amount=.3)
    assert np.array_equal(layer.frame(0),pixels)
    assert np.array_equal(layer.frame(1),pixels)
    assert np.array_equal(layer.frame(10),pixels)
    left=layer.frame(.3)
    # The iris slides clearly left inside the eye; the eye's corners and the skin stay put.
    assert np.array_equal(left[38,40,:3],iris) and np.array_equal(left[38,52,:3],[1,1,1])
    assert np.array_equal(left[38,30],pixels[38,30]) and np.array_equal(left[38,69],pixels[38,69])
    outside=np.ones((80,120),bool);outside[30:46,30:70]=False
    assert np.array_equal(left[outside],pixels[outside])
    # Original pixels moved, not generated or recoloured.
    assert {tuple(v) for v in left.reshape(-1,4)}<= {tuple(v) for v in pixels.reshape(-1,4)}
    assert np.array_equal(layer.frame(.3),layer.frame(.35))  # quiet held gaze
    right=layer.frame(.6)
    assert np.array_equal(right[38,58,:3],iris)


def test_eye_regions_must_be_inside_the_cutout():
    pixels=np.ones((80,120,4),np.float32)
    with pytest.raises(ValueError): LookingPixels(pixels,[])
    with pytest.raises(ValueError): LookingPixels(pixels,[{'rect':[-.2,.2,.3,.3]}])
    with pytest.raises(ValueError): LookingPixels(pixels,[{'rect':[.2,.2,.3,.3]}],float('nan'))


def test_project_gallery_and_voice_upload_keep_assets_in_the_project(tmp_path,monkeypatch):
    from core.project import create_project
    from server.api import create_app
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'appdata'))
    project=create_project('Layer test',str(tmp_path/'test.zuckervid'))
    photo=project.folder/'photos'/'test.png';photo.parent.mkdir(exist_ok=True)
    Image.new('RGB',(80,60),(210,160,50)).save(photo)
    (photo.parent/'photos.json').write_text(json.dumps([{'photo':str(photo),'name':'Original'}]))
    folder=project.cache_dir/'show_cutouts';folder.mkdir(parents=True,exist_ok=True)
    np.save(folder/'abc.npy',np.ones((60,80),np.float32))
    app=create_app(str(project.folder));client=app.test_client()
    result=client.post('/api/v1/show/characters',json={'photo':str(photo),'mask':'abc','gallery_only':True,'name':'My cutout'})
    assert result.status_code==201,result.get_json()
    asset=Path(result.get_json()['cutout'])
    assert asset.is_relative_to(project.folder/'assets/show-library')
    assert np.array_equal(np.asarray(Image.open(asset))[:,:,:3],np.asarray(Image.open(photo)))
    assert any(item['path']==str(asset) for item in client.get('/api/v1/show/library').get_json()['items'])
    # Actual short WAV: server must decode it, not trust a suffix or MIME type.
    sound=io.BytesIO()
    with wave.open(sound,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*8000)
    response=client.post('/api/v1/show/voices',data={'project_id':str(project.folder),'file':(io.BytesIO(sound.getvalue()),'Voice.wav')})
    assert response.status_code==201,response.get_json()
    voice=response.get_json()
    assert voice['length']==pytest.approx(.5)
    assert Path(voice['source']).is_relative_to(project.folder/'assets/show-voices')
    assert not list(Path(voice['source']).parent.glob('*.upload'))
    assert client.post('/api/v1/show/voices',data={'project_id':'wrong','file':(io.BytesIO(sound.getvalue()),'Voice.wav')}).status_code==409
    assert client.post('/api/v1/show/voices',data={'project_id':str(project.folder),'file':(io.BytesIO(b'not audio'),'fake.wav')}).status_code==400


def test_saved_eye_regions_reopen_and_reach_the_scene_renderer(tmp_path,monkeypatch):
    from core.project import create_project
    from server.api import create_app
    from core.show_scenes import overlay_items
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'appdata'))
    project=create_project('Eyes',str(tmp_path/'eyes.zuckervid'))
    photo=project.folder/'photos'/'face.png';photo.parent.mkdir(exist_ok=True)
    Image.new('RGB',(100,80),(230,210,170)).save(photo)
    (photo.parent/'photos.json').write_text(json.dumps([{'photo':str(photo),'name':'Face'}]))
    masks=project.cache_dir/'show_cutouts';masks.mkdir(parents=True,exist_ok=True)
    np.save(masks/'abc.npy',np.ones((80,100),np.float32))
    client=create_app(str(project.folder)).test_client()
    eyes=[{'rect':[.2,.2,.15,.2]}]
    response=client.post('/api/v1/show/characters',json={'photo':str(photo),'mask':'abc','eyes':eyes,'name':'Original face'})
    assert response.status_code==201,response.get_json()
    saved=response.get_json()
    restored=client.get('/api/v1/show/characters/edit',query_string={'asset':saved['cutout']})
    assert restored.status_code==200
    assert restored.get_json()['record']['eyes']==eyes
    preview=client.post('/api/v1/show/characters/eyes-preview',json={'photo':str(photo),'mask':'abc','eyes':eyes})
    assert preview.status_code==200,preview.get_json()
    assert client.get(preview.get_json()['url']).status_code in (200,206)
    assert client.get('/api/v1/show/characters/edit',query_string={'asset':'/etc/passwd'}).status_code==404
    document={'scenes':[{'decision':'modified','start':0,'end':3,'overlays':[{'asset':saved['cutout'],'action':'look'}]}]}
    assert overlay_items(document)[0]['eyes']==eyes


def test_silhouette_gallery_preserves_original_alpha_not_an_opaque_rectangle(tmp_path,monkeypatch):
    from core.project import create_project
    from core.show_characters import cutout
    from server.api import create_app
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    project=create_project('Silhouette',str(tmp_path/'shadow.zuckervid'))
    photo=project.folder/'photos'/'photo.png';photo.parent.mkdir(exist_ok=True)
    Image.new('RGB',(40,40),(220,180,70)).save(photo)
    (photo.parent/'photos.json').write_text(json.dumps([{'photo':str(photo),'name':'Me'}]))
    mask=np.zeros((40,40),np.float32);mask[8:30,10:28]=1;mask[8,10]=.4
    cache=project.cache_dir/'show_cutouts';cache.mkdir(parents=True,exist_ok=True);np.save(cache/'abc.npy',mask)
    expected,_=cutout(photo,mask)
    client=create_app(str(project.folder)).test_client()
    response=client.post('/api/v1/show/characters',json={'photo':str(photo),'mask':'abc','gallery_only':True,'silhouette':True})
    assert response.status_code==201,response.get_json()
    actual=np.asarray(Image.open(response.get_json()['cutout']).convert('RGBA'))
    assert actual[:,:,:3].max()==0
    assert np.array_equal(actual[:,:,3],np.asarray(expected)[:,:,3])


def test_any_library_picture_can_be_rigged_without_changing_its_pixels(tmp_path,monkeypatch):
    from core.project import create_project
    from server.api import create_app
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    project=create_project('Original parts',str(tmp_path/'parts.zuckervid'))
    asset=project.folder/'assets/show-library/painting.png';asset.parent.mkdir(parents=True)
    image=Image.new('RGBA',(80,50),(180,60,90,255));image.save(asset)
    client=create_app(str(project.folder)).test_client()
    response=client.post('/api/v1/show/characters/prepare-layer',json={'project_id':str(project.folder),'asset':str(asset)})
    assert response.status_code==200,response.get_json()
    result=response.get_json()
    assert result['cut']['method']=='original-alpha'
    preview=project.cache_dir/'show_cutouts'/(result['cut']['mask']+'.png')
    assert Image.open(preview).tobytes()==image.tobytes()
    eyes=[{'rect':[.2,.2,.2,.3]}]
    rig=client.post('/api/v1/show/characters',json={'photo':result['photo']['photo'],'mask':result['cut']['mask'],'name':'My painting','eyes':eyes})
    assert rig.status_code==201,rig.get_json()
    assert rig.get_json()['eyes']==eyes
    from core.show_scenes import overlay_items
    items=overlay_items({'scenes':[{'start':0,'end':2,'decision':'accepted','overlays':[{'asset':rig.get_json()['cutout'],'action':'look','at':0,'duration':2}]}]})
    assert items[0]['eyes']==eyes
    assert client.post('/api/v1/show/characters/prepare-layer',json={'project_id':'old','asset':str(asset)}).status_code==409
    assert client.post('/api/v1/show/characters/prepare-layer',json={'project_id':str(project.folder),'asset':'/etc/passwd'}).status_code==400
