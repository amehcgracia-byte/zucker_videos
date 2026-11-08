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

@pytest.mark.parametrize('changes',[{'unstable_sync':True},{'low_confidence':True},{'error':'bad'},{'no_audio':True},{'confidence':2},{'offset_sec':20},{'duration_sec':11},{'offset_sec':float('nan')}])
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
