"""Manual editing must never silently redistribute the music edit plan."""
import copy
import json

from core.project import atomic_write_json, create_project


def test_editing_and_remembering_one_musician_changes_only_selected_shot(tmp_path, monkeypatch):
    import core.stages.cut as cut
    import server.edit_timeline as timeline
    from server.api import create_app

    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    p=create_project("One shot",str(tmp_path / "OneShot.zuckervid"))
    a=tmp_path/"camera-one.mp4";a.touch()
    b=tmp_path/"camera-two.mp4";b.touch()
    p.data["inputs"]["videos"]=[{"path":str(a),"probe":{}},{"path":str(b),"probe":{}}]
    p.data["inputs"]["master"]={"path":"master.wav"}
    p.data["settings"]["edit"]["camera_types"]={str(a):"fixed",str(b):"fixed"}
    p.save()
    shots=[
        {"source_path":str(a),"clip_path":str(a),"clip_start_sec":i,
         "master_start_sec":i,"duration_sec":1,
         "flat_framing":{"zoom":1.2,"x":.4,"y":.45},
         "manual_view":False,"performer_view":str(i%2),
         "stage_performers":[{"id":"p1","role":"guitarist","zoom":1.5,"x":.6,"y":.5}]}
        for i in range(6)
    ]
    document=p.artifacts_dir/"edit_plan.json"
    atomic_write_json(document,{"platform":"youtube","segments":shots})
    baseline=copy.deepcopy(shots)
    monkeypatch.setattr(cut,"load_coverage",lambda _: {"sources":[]})
    monkeypatch.setattr(timeline,"duration",lambda _:100)
    client=create_app(str(p.folder),dev=True).test_client()
    doc=client.get("/api/v1/edit/shot?index=2")
    assert doc.status_code==200,doc.json
    r=client.patch("/api/v1/edit/shot",json={
        "project_id":str(p.folder),"revision":doc.json["revision"],
        "index":2,"choice":0,"edit_component":"framing",
        "flat_framing":{"zoom":1.17,"x":.6,"y":.40},
        "remember_musician":"Guitar","apply_to_source":False,
        "movement":False})
    assert r.status_code==200,r.json
    actual=json.loads(document.read_text())["segments"]
    assert len(actual)==len(baseline)
    assert all(actual[i]==baseline[i] for i in range(len(shots)) if i!=2)
    assert actual[2]["flat_framing"]["x"]==.6
    assert actual[2]["performer_view_name"]=="Guitar"
    doc2=client.get("/api/v1/edit/shot?index=3").json
    guitar=[v for v in doc2["saved_views"] if v["name"]=="Guitar"]
    assert len(guitar)==1
    assert guitar[0]["flat_framing"]["x"]==.6
    assert doc2["performers"]==baseline[3]["stage_performers"]
    use=client.patch("/api/v1/edit/shot",json={
        "project_id":str(p.folder),"revision":doc2["revision"],
        "index":3,"choice":0,"edit_component":"framing",
        "flat_framing":{"zoom":1,"x":.5,"y":.5},
        "selected_musician_view":"Guitar","movement":False,
        "apply_to_source":False})
    assert use.status_code==200,use.json
    reused=json.loads(document.read_text())["segments"]
    assert reused[3]["flat_framing"]==reused[2]["flat_framing"]
    assert reused[3]["performer_view_name"]=="Guitar"
    assert reused[3]["motion_enabled"] is False
    assert reused[0]==baseline[0] and reused[1]==baseline[1]
    assert reused[4:]==baseline[4:]
    doc2=client.get("/api/v1/edit/shot?index=3").json
    reject=client.patch("/api/v1/edit/shot",json={
        "project_id":str(p.folder),"revision":doc2["revision"],
        "index":3,"choice":0,"edit_component":"framing",
        "flat_framing":{"zoom":1.1,"x":.5,"y":.5},
        "apply_to_source":True})
    assert reject.status_code==400
    assert json.loads(document.read_text())["segments"][3]==reused[3]


def test_switching_camera_discards_old_camera_specific_motion(tmp_path, monkeypatch):
    import core.stages.cut as cut
    import server.edit_timeline as timeline
    import core.shot_review as shot_review
    from server.api import create_app

    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    p=create_project("Switch",str(tmp_path/"Switch.zuckervid"))
    a=tmp_path/"camera-one.mp4";a.touch()
    b=tmp_path/"camera-two.mp4";b.touch()
    p.data["inputs"]["videos"]=[{"path":str(a),"probe":{}},{"path":str(b),"probe":{}}]
    p.data["settings"]["edit"]["camera_types"]={str(a):"fixed",str(b):"fixed"}
    p.save()
    old=[{"source_path":str(a),"clip_path":str(a),"clip_start_sec":i,"master_start_sec":i,
          "duration_sec":1,"flat_framing":{"zoom":1.4,"x":.25,"y":.45},
          "motion":{"type":"ken_burns","zoom_start":1.0,"zoom_end":1.25},
          "manual_view":True} for i in range(3)]
    document=p.artifacts_dir/"edit_plan.json"
    atomic_write_json(document,{"platform":"youtube","segments":old})
    monkeypatch.setattr(cut,"load_coverage",lambda _:{"sources":[]})
    monkeypatch.setattr(timeline,"duration",lambda _:100)
    monkeypatch.setattr(shot_review,"_review_candidate_pool",lambda *args,**kwargs:(
        [{"path":str(b),"source_path":str(b),"clip_start_sec":1,
          "offset_sec":0,"filename":b.name}], "test"))
    monkeypatch.setattr(shot_review,"_candidate_covers_slot",lambda *args,**kwargs:True)
    client=create_app(str(p.folder),dev=True).test_client()
    doc=client.get("/api/v1/edit/shot?index=1").json
    assert len(doc["choices"])>=2
    assert doc["choices"][1]["source_path"]==str(b)
    assert doc["choices"][1]["framing"]=={"zoom":1,"x":.5,"y":.5}
    r=client.patch("/api/v1/edit/shot",json={
        "project_id":str(p.folder),"revision":doc["revision"],
        "index":1,"choice":1,"edit_component":"framing",
        "flat_framing":{"zoom":1.12,"x":.55,"y":.50},
        "movement":False,"apply_to_source":False})
    assert r.status_code==200,r.json
    actual=json.loads(document.read_text())["segments"]
    assert actual[0]==old[0] and actual[2]==old[2]
    assert actual[1]["source_path"]==str(b)
    assert actual[1].get("motion") is None
    assert actual[1]["motion_enabled"] is False


def test_automatic_movement_varies_per_shot_but_never_rerolls_on_edit():
    from core.stages.edit import _default_shot_motion_enabled
    for seed in ("Tune Up", "Show A", "alternate musician"):
        shots=[_default_shot_motion_enabled(seed,i,"fixed-one") for i in range(35)]
        assert 5 <= sum(shots) <= 30
        assert any(a != b for a,b in zip(shots,shots[1:]))
        assert shots==[_default_shot_motion_enabled(seed,i,"fixed-one") for i in range(35)]


def test_one_shot_edit_rejects_camera_wide_light(tmp_path,monkeypatch):
    import core.stages.cut as cut
    import server.edit_timeline as timeline
    from server.api import create_app
    monkeypatch.setenv("ZUCKER_DATA_ROOT",str(tmp_path/"data"))
    p=create_project("Light safety",str(tmp_path/"Light.zuckervid"))
    video=tmp_path/"fixed.mp4";video.touch()
    p.data["inputs"]["videos"]=[{"path":str(video),"probe":{}}]
    p.data["settings"]["edit"]["camera_types"]={str(video):"fixed"}
    p.save()
    shots=[{"source_path":str(video),"clip_path":str(video),"clip_start_sec":i,
            "master_start_sec":i,"duration_sec":1} for i in range(3)]
    path=p.artifacts_dir/"edit_plan.json"
    atomic_write_json(path,{"platform":"youtube","segments":shots})
    monkeypatch.setattr(cut,"load_coverage",lambda _:{"sources":[]})
    monkeypatch.setattr(timeline,"duration",lambda _:100)
    client=create_app(str(p.folder),dev=True).test_client()
    doc=client.get("/api/v1/edit/shot?index=0").json
    r=client.patch("/api/v1/edit/shot",json={
        "project_id":str(p.folder),"revision":doc["revision"],
        "index":0,"choice":0,"edit_component":"light","light_scope":"camera",
        "video_light":{"auto":False,"light":0,"shadows":0},
        "apply_to_source":False})
    assert r.status_code==400
    assert json.loads(path.read_text())["segments"]==shots


def test_swap_one_shot_preserves_other_shots_and_their_continuity_metadata(tmp_path,monkeypatch):
    from core import shot_review
    p=create_project("Swap isolated",str(tmp_path/"Swap.zuckervid"))
    first="/fixed.mp4"
    second="/other.mp4"
    old=[{
        "source_path":first,"clip_path":first,"clip_start_sec":i*4,
        "master_start_sec":i*4,"duration_sec":4,
        "spherical_shot":{"type":"manual","yaw":float(10+i*5),"pitch":-5.0,
                          "fov":90.0,"continuity_duration_sec":16.0,
                          "continuity_offset_sec":i*4.0},
        "manual_view":True,
    } for i in range(4)]
    path=p.artifacts_dir/"edit_plan.json"
    atomic_write_json(path,{"platform":"youtube","segments":old})
    monkeypatch.setattr(shot_review,"load_coverage",lambda _:{
        "platform":"youtube","sources":[]})
    monkeypatch.setattr(shot_review,"_project_candidate_pool",lambda *a,**k:([
        {"path":second,"source_path":second,"clip_start_sec":4,
         "offset_sec":0,"duration_sec":100,"confidence":10}], "test"))
    monkeypatch.setattr(shot_review,"_replacement_candidates",
        lambda pool,segment,tried,platform,**k:(
            [(10.,pool[0],"alternate")] if "alternate" not in tried else [],
            {"pool":1}))
    monkeypatch.setattr(shot_review,"review_items",lambda *a,**k:[])
    result=shot_review.replace_slots(p,[1])
    assert result["replaced"]==[1]
    saved=json.loads(path.read_text())["segments"]
    assert saved[1]["source_path"]==second
    for index in (0,2,3):
        assert saved[index]==old[index],f"Shot {index} changed after replacing shot 1"


def test_360_pianist_and_drummer_choices_keep_distinct_authored_angles(tmp_path, monkeypatch):
    import core.stages.cut as cut
    import core.shot_review as shot_review
    import server.edit_timeline as timeline
    from server.api import create_app
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path/"data"))
    p=create_project("360 instruments",str(tmp_path/"360.zuckervid"))
    video=tmp_path/"sphere.mp4";video.touch()
    p.data["inputs"]["videos"]=[{"path":str(video),"projection":"equirect","probe":{"projection":"equirect"}}]
    p.data["settings"]["edit"]["camera_types"]={str(video):"360"}
    p.save()
    shots=[{"source_path":str(video),"clip_path":str(video),"projection":"equirect",
            "clip_start_sec":i,"master_start_sec":i,"duration_sec":1,
            "spherical_shot":{"type":"singer","label":"Singer","yaw":20,"pitch":-3,"fov":95}} for i in range(3)]
    path=p.artifacts_dir/"edit_plan.json"
    atomic_write_json(path,{"platform":"youtube","segments":shots})
    monkeypatch.setattr(cut,"load_coverage",lambda _:{"platform":"youtube","sources":[]})
    monkeypatch.setattr(timeline,"duration",lambda _:100)
    views=[{"type":"pianist","label":"Piano","yaw":100,"pitch":-8,"fov":90},
           {"type":"drummer","label":"Drums","yaw":200,"pitch":5,"fov":100}]
    monkeypatch.setattr(shot_review,"_review_candidate_pool",lambda *a,**k:([
        {"path":str(video),"source_path":str(video),"projection":"equirect",
         "clip_start_sec":1,"offset_sec":0,"spherical_shot":view,
         "filename":video.name} for view in views],"test"))
    monkeypatch.setattr(shot_review,"_candidate_covers_slot",lambda *a,**k:True)
    client=create_app(str(p.folder),dev=True).test_client()
    doc=client.get("/api/v1/edit/shot?index=1").json
    assert len(doc["choices"])==3
    assert [c["pose"]["yaw"] for c in doc["choices"]]==[20,100,200]
    for choice,yaw in [(1,100),(2,200)]:
        doc=client.get("/api/v1/edit/shot?index=1").json
        pose=doc["choices"][choice]["pose"]
        r=client.patch("/api/v1/edit/shot",json={
            "project_id":str(p.folder),"revision":doc["revision"],
            "index":1,"choice":choice,"edit_component":"framing",
            "yaw":pose["yaw"],"pitch":pose["pitch"],"fov":pose["fov"],
            "movement":False,"apply_to_source":False})
        assert r.status_code==200,r.json
        saved=json.loads(path.read_text())["segments"]
        assert saved[0]==shots[0] and saved[2]==shots[2]
        assert saved[1]["spherical_shot"]["yaw"]==yaw
        assert saved[1]["spherical_shot"]["label"]==pose["label"]
        assert saved[1]["spherical_shot"]["type"]==pose["type"]
