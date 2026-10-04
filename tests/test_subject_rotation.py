from core.stages.edit import _youtube_multicam_plan


def test_shared_subject_cooldown_across_two_drummer_cameras():
    sources = [{"path": f"/tmp/{name}.mp4", "filename": f"{name}.mp4", "camera_id": name,
                "camera_role": "handheld", "camera_subject": subject,
                "duration_sec": 90, "offset_sec": 0}
               for name, subject in [("drums1", "drummer"), ("drums2", "drummer"),
                                     ("vocal", "singer"), ("keys", "pianist"),
                                     ("guitar", "guitarist"), ("bass", "bassist")]]
    coverage = {"platform": "youtube", "window": {"start_sec": 0, "duration_sec": 90}, "sources": sources}
    plan = _youtube_multicam_plan(coverage, {"bars_sec": list(range(0, 91, 3))},
                                 {"edit": {"camera_role_weights": {"handheld": 1, "360": 0, "fixed_rear": 0}}})
    subjects = [shot["editorial_subject"] for shot in plan["segments"]]
    for index, subject in enumerate(subjects):
        assert subject not in subjects[max(0, index - 4):index]
    assert set(subjects) == {"drummer", "singer", "pianist", "guitarist", "bassist"}
    assert not any(shot["subject_cooldown_fallback"] for shot in plan["segments"])


def test_single_subject_coverage_reports_unavoidable_repeats():
    source = {"path": "/tmp/drums.mp4", "camera_subject": "drummer", "duration_sec": 20}
    plan = _youtube_multicam_plan({"platform": "youtube", "window": {"duration_sec": 20}, "sources": [source]},
                                 {"bars_sec": [0, 4, 8, 12, 16, 20]})
    assert all(shot["editorial_subject"] == "drummer" for shot in plan["segments"])
    assert plan["subject_unavoidable_repeat_windows"] > 0
    assert any(shot["subject_cooldown_fallback"] for shot in plan["segments"][1:])



def test_each_spherical_landmark_keeps_its_own_authored_pose():
    from core.stages.edit import _available_spherical_shots, migrate_spherical_landmarks
    raw = {"singer": {"yaw": 107.126, "pitch": -21.546, "fov": 82, "weight": 0},
           "drummer": {"yaw": 297.542, "pitch": -25, "fov": 93.1, "weight": 5},
           "left": {"yaw": 52.245, "pitch": -25, "fov": 93, "weight": 0},
           "planet": {"yaw": 102.433, "pitch": -25, "fov": 220, "weight": 0}}
    canonical = migrate_spherical_landmarks(raw)
    shots = {shot["type"]: shot for shot in _available_spherical_shots(canonical)}
    for identity in ("singer", "drummer", "left"):
        assert shots[identity]["yaw"] == canonical[identity]["yaw"]
        assert shots[identity]["pitch"] == canonical[identity]["pitch"]
        assert shots[identity]["fov"] == canonical[identity]["fov"]



def test_normalized_sphere_uses_original_source_landmark_profile():
    source = {"path": "/cache/sphere-proxy.mp4", "source_path": "/original/sphere.mp4",
              "projection": "equirect", "camera_role": "360", "duration_sec": 12}
    settings = {"spherical_landmarks": {"singer": {"yaw": 300, "weight": 1}},
                "spherical_landmarks_by_source": {"/original/sphere.mp4": {"singer": {"yaw": 107, "pitch": -21, "fov": 82, "weight": 1}}}}
    plan = _youtube_multicam_plan({"platform": "youtube", "window": {"duration_sec": 12}, "sources": [source]},
                                 {"bars_sec": [0, 4, 8, 12]}, settings)
    singer = [shot for shot in plan["segments"] if shot.get("spherical_shot", {}).get("type") == "singer"]
    assert singer
    assert all(shot["spherical_shot"]["yaw"] == 107 for shot in singer)
