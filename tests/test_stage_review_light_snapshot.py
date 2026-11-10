from core.project import create_project, atomic_write_json


def test_first_editor_camera_measurement_does_not_rekey_untouched_pictures(tmp_path):
    from core.video_light import stable_stage_review_profiles
    from core.shot_review import _thumbnail_asset
    p = create_project('Review',str(tmp_path/'review.zuckervid'))
    source=tmp_path/'camera.mp4'; source.write_bytes(b'fixture')
    p.data['inputs']['videos']=[{'path':str(source)}]
    segment={'clip_path':str(source),'clip_start_sec':2,'duration_sec':1}
    initial=stable_stage_review_profiles(p)
    before=_thumbnail_asset(p.cache_dir,1,segment,initial.get(str(source),{}))[2]
    atomic_write_json(p.cache_dir/'color_profiles.json',{'corrections':{str(source):{'brightness':1.12}}})
    after=stable_stage_review_profiles(p)
    assert after==initial
    assert _thumbnail_asset(p.cache_dir,1,segment,after.get(str(source),{}))[2]==before
    source.write_bytes(b'changed source')
    assert stable_stage_review_profiles(p)=={str(source):{'brightness':1.12}}
