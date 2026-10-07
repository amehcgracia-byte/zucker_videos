from core.stages import export
from core.project import create_project


def test_sony_reference_is_not_brightest_source():
    sony={'filename':'Sony/C0220.MP4'};sphere={'projection':'equirect'}
    assert export._color_reference_record([sphere,sony],[(sphere,{'luma':180}),(sony,{'luma':35})]) is sony


def test_360_lift_and_chroma_cannot_amplify_noise_aggressively():
    correction=export.color_correction_for_profile({'camera_kind':'360','luma':17,'saturation':12,'u_mean':100,'v_mean':150},{'luma':80,'saturation':80,'u_mean':130,'v_mean':128})
    assert correction['brightness_adjust']<=.015
    assert .95<=correction['saturation_adjust']<=1.05
    assert abs(correction['red_balance'])<=.025
    assert abs(correction['blue_balance'])<=.025


def test_sphere_color_samples_use_visible_projection(tmp_path,monkeypatch):
    project=create_project('Colour',str(tmp_path/'Colour.zuckervid'))
    monkeypatch.setattr(export,'_segment_source_info',lambda *args:dict(probe={'projection':'equirect'}))
    sample=export._visible_color_samples(project,[{'clip_start_sec':10,'duration_sec':4,'spherical_shot':{'type':'singer','yaw':120,'pitch':-20,'fov':80}}])[0]
    assert sample['start']==12
    assert 'v360' in sample['filter'] and 'yaw=120.000' in sample['filter']
    command=export.color_sample_commands('sphere.mp4',60,samples=[sample])[0]
    assert sample['filter'] in command[command.index('-vf')+1]


def test_reference_has_no_global_exposure_lift(tmp_path,monkeypatch):
    project=create_project('Colour',str(tmp_path/'Colour.zuckervid'))
    sony=str(tmp_path/'Sony'/'C0220.MP4')
    project.data['inputs']['videos']=[{'path':sony,'filename':'Sony/C0220.MP4'}]
    monkeypatch.setattr(export,'cached_or_measure_clip_color',lambda *args,**kwargs:({'luma':34,'saturation':10},None))
    warnings=[]
    corrections=export._color_profiles_for_segments(project,[{'clip_path':sony,'source_path':sony}],warnings)
    assert corrections[sony]=={}
    assert warnings==[]
