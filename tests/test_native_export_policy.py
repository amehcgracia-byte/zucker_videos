from types import SimpleNamespace
import pytest
from core.native_export_policy import optional_fallbacks

@pytest.fixture
def context(monkeypatch):
    import core.fillers as fillers
    import core.stages.sync as sync
    monkeypatch.setattr(fillers,'filler_paths',lambda p:{'logo.mp4'})
    monkeypatch.setattr(sync,'sync_map_is_current',lambda p:True)
    monkeypatch.setattr(sync.SyncStage,'inputs_fingerprint',lambda self,p:'current')
    clip={'path':'camera.mp4','offset_sec':5,'duration_sec':60,'confidence':10,'threshold':6}
    monkeypatch.setattr(sync,'load_sync_map',lambda *a,**kw:{'clips':{'cam':clip}})
    p=SimpleNamespace(data={'stages':{'sync':{'status':'done','fingerprint':'current'}}})
    camera={'source_path':'camera.mp4','master_start_sec':5,'clip_start_sec':0,'duration_sec':10,'flat_framing':{'zoom':1.2}}
    insert={'source_path':'logo.mp4','master_start_sec':15,'clip_start_sec':0,'duration_sec':3,'video_light':{'light':1}}
    records=[{'path':'logo.mp4'},{'path':'camera.mp4'}]
    return p,[camera,insert],records,clip

def test_optional_insert_has_same_clock_synchronized_fallback(context):
    p,shots,records,clip=context
    result=optional_fallbacks(p,shots,records)
    assert result=={'1':[{'source':1,'master_start_sec':15,'clip_start_sec':10.,'duration_sec':3.,'playback_speed':1.,'flat_framing':{'zoom':1.2}}]}
    assert 'video_light' not in result['1'][0]
    assert shots[1]['source_path']=='logo.mp4'

@pytest.mark.parametrize('changes',[{'unstable_sync':True},{'low_confidence':True},{'error':'bad'},{'no_audio':True},{'confidence':2},{'offset_sec':20},{'duration_sec':11},{'offset_sec':float('nan')},{'offset_ranges':[{'clip_start_sec':0,'clip_end_sec':60,'offset_sec':7}]},{'manual_override_ranges':True}])
def test_unproven_coverage_is_not_automatic_fallback(context,changes):
    p,shots,records,clip=context;clip.update(changes)
    assert optional_fallbacks(p,shots,records)=={}

def test_stale_sync_and_motion_are_not_reused(context):
    p,shots,records,clip=context
    p.data['stages']['sync']['fingerprint']='old'
    assert optional_fallbacks(p,shots,records)=={}
    p.data['stages']['sync']['fingerprint']='current'
    shots[0]['motion']={'type':'pan'}
    assert optional_fallbacks(p,shots,records)=={}


def test_fallback_retains_camera_light_instead_of_insert_light(context):
    p,shots,records,clip=context
    spec={'auto':False,'light':.2,'shadows':.1}
    p.data['settings']={'edit':{'video_light':{'enabled':True,'cameras':{'camera.mp4':spec}}}}
    assert optional_fallbacks(p,shots,records)['1'][0]['video_light']==spec


def test_automatic_flat_camera_motion_can_hold_its_saved_base_framing(context):
    p,shots,records,clip=context
    shots[0]['motion']={'type':'ken_burns','movement':'zoom_in','zoom_start':1,'zoom_end':1.2}
    fallback=optional_fallbacks(p,shots,records)['1'][0]
    assert fallback['flat_framing']==shots[0]['flat_framing']
    assert 'motion' not in fallback
    assert shots[0]['motion']['movement']=='zoom_in'
    assert fallback['master_start_sec']==15 and fallback['clip_start_sec']==10


def test_server_missing_optional_materializes_snapshot_without_changing_edit(context,tmp_path):
    from core.native_export_policy import omit_missing_optional
    p,shots,records,clip=context
    camera=tmp_path/'camera.mp4';camera.write_bytes(b'camera')
    logo=tmp_path/'logo.mp4'
    shots[0]['source_path']=str(camera);shots[1]['source_path']=str(logo)
    records[0]['path']=str(logo);records[1]['path']=str(camera)
    clip['path']=str(camera)
    p.data['inputs']={'videos':records}
    import core.fillers as fillers
    # context fixture supplies names; this integration uses real temporary paths.
    from unittest.mock import patch
    with patch.object(fillers,'filler_paths',return_value={str(logo)}):
        plan={'platform':'youtube','segments':shots}
        effective=omit_missing_optional(p,plan)
        assert effective['segments'][1]['source_path']==str(camera)
        assert effective['segments'][1]['master_start_sec']==15
        assert effective['segments'][1]['duration_sec']==3
        assert effective['optional_omissions'][0]['shot']==2
        assert 'omitted' in effective['warnings'][0]
        assert plan['segments'][1]['source_path']==str(logo)
        camera.unlink()
        missing=omit_missing_optional(p,plan)
        assert missing['segments'][1]['placeholder']=='black'
        assert missing['segments'][0]==shots[0]

def test_legacy_filler_provenance_recovers_optional_role_without_filename_guess():
    from core.native_export_policy import optional_paths
    p=SimpleNamespace(data={})
    shots=[{'source_path':'logo.small.mp4','filler':True}]*16
    assert optional_paths(p,shots)=={'logo.small.mp4'}
    assert optional_paths(p,shots+[{'source_path':'logo.small.mp4'}])==set()
    assert optional_paths(p,[{'source_path':'logo.small.mp4'}]*16)==set()


def test_native_recipe_recovers_saved_insert_provenance_without_rebuilding(tmp_path,monkeypatch):
    from core.project import create_project,atomic_write_json
    from server.api import create_app
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    p=create_project('Existing montage',str(tmp_path/'edit.zuckervid'))
    p.data['settings']['wizard']={'platform':'youtube'}
    p.data['inputs']['videos']=[{'path':'Tuneup short logo vid.small.mp4'},{'path':'camera.mp4'}]
    shots=[{'source_path':'Tuneup short logo vid.small.mp4' if (i+1)%5==0 else 'camera.mp4',
            'filler':(i+1)%5==0,'master_start_sec':i,'clip_start_sec':0,'duration_sec':1} for i in range(80)]
    atomic_write_json(p.artifacts_dir/'edit_plan.json',{'platform':'youtube','segments':shots})
    before=(p.artifacts_dir/'edit_plan.json').read_bytes();p.save()
    response=create_app(str(p.folder),dev=True).test_client().get('/api/v1/edit/native-recipe',query_string={'project_id':str(p.folder)})
    assert response.status_code==200,response.json
    assert response.json['sources'][0]['optional'] is True
    assert response.json['sources'][1]['optional'] is False
    assert len(response.json['segments'])==80
    assert response.json['fallbacks']=={}
    assert (p.artifacts_dir/'edit_plan.json').read_bytes()==before



def test_optional_provenance_uses_registered_original_and_normalized_identity():
    from core.native_export_policy import optional_paths
    p=SimpleNamespace(data={'inputs':{'videos':[{'path':'original.mp4','normalized':{'path':'preview.mp4'}}]}})
    assert optional_paths(p,[{'clip_path':'preview.mp4','filler':True}])=={'original.mp4','preview.mp4'}
    assert optional_paths(p,[{'clip_path':'preview.mp4','filler':True},{'source_path':'original.mp4'}])==set()
    p.data['settings']={'edit':{'fillers':['original.mp4']}}
    assert optional_paths(p,[{'clip_path':'preview.mp4'}])=={'original.mp4','preview.mp4'}
