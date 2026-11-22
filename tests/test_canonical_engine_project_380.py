"""#380 guarded native Engine project flow; synthetic isolated test user only.

Strict mode stays OFF in production. These tests explicitly opt in in their
own temporary Engine app and do not use real projects or any running worker.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from server.project_guard_380 import (
    ProjectConflict380, duplicate_existing, ensure_title_available,
    project_revision, project_root_guard, rename_existing,
    verified_project_folder,
)
from server.api import create_app


class EngineCanonical380(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="engine380-")
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.data=self.root/"private-data"
        self.data.mkdir()
        self.projects=self.root/"projects"
        self.projects.mkdir()
        (self.data/"config.json").write_text(json.dumps({
            "project_root":str(self.projects),
            "source_folders":[str(self.root)],
            "inbox_path":str(self.root/"inbox"),
        }))
        self.old_data=os.environ.get("ZUCKER_DATA_ROOT")
        os.environ["ZUCKER_DATA_ROOT"]=str(self.data)
        self.addCleanup(self._restore_env)
        video=self.root/"test-video.mp4"
        subprocess.run([
            "ffmpeg","-hide_banner","-loglevel","error","-f","lavfi",
            "-i","color=c=blue:s=160x90:r=5","-t","1.2","-c:v","libx264",
            "-pix_fmt","yuv420p","-y",str(video)
        ],check=True,timeout=25)
        self.video=video
        app=create_app(dev=False)
        app.config.update(TESTING=True,CANONICAL_380_STRICT=True)
        self.app=app

    def _restore_env(self):
        if self.old_data is None:
            os.environ.pop("ZUCKER_DATA_ROOT",None)
        else:os.environ["ZUCKER_DATA_ROOT"]=self.old_data

    def draft(self, *, name="My Concert",existing=None,rev=None,mode="youtube"):
        return self.app.test_client().post(
            "/api/v1/wizard/draft",
            json={"name":name,"videos":[str(self.video)],"platform":mode,
                  "project_id":existing,"expected_revision":rev})

    def create(self,name="My Concert"):
        response=self.draft(name=name)
        self.assertEqual(response.status_code,200,response.json)
        return response.json

    def test_create_save_reload_reopen_stable_uuid(self):
        first=self.create()
        folder=Path(first["project_id"])
        self.assertTrue(folder.is_dir())
        self.assertEqual(len(list(self.projects.glob("*.zuckervid"))),1)
        rev=first["project_revision"]
        self.assertEqual(rev,project_revision(folder))
        opened=self.app.test_client().post(
            "/api/v1/wizard/projects/open",json={"path":str(folder)})
        self.assertEqual(opened.status_code,200,opened.json)
        self.assertEqual(opened.json["project_revision"],rev)
        second=self.draft(existing=str(folder),rev=rev,mode="360")
        self.assertEqual(second.status_code,200,second.json)
        self.assertNotEqual(second.json["project_revision"],rev)
        after=self.app.test_client().post(
            "/api/v1/wizard/projects/open",json={"path":str(folder)})
        self.assertEqual(after.status_code,200,after.json)
        self.assertEqual(after.json["project_revision"],second.json["project_revision"])
        self.assertEqual(len(list(self.projects.glob("*.zuckervid"))),1)

    def test_two_tabs_create_same_normalized_title_exactly_one_wins(self):
        barrier=Barrier(2)
        def create(title):
            barrier.wait()
            return self.draft(name=title)
        with ThreadPoolExecutor(max_workers=2) as pool:
            x=pool.submit(create,"Live Concert")
            y=pool.submit(create,"live   concert")
            outcomes=[x.result(),y.result()]
        self.assertEqual(sorted(x.status_code for x in outcomes),[200,409])
        self.assertEqual(len(list(self.projects.glob("*.zuckervid"))),1)

    def test_two_tabs_save_same_revision_one_only(self):
        first=self.create()
        barrier=Barrier(2)
        def write(mode):
            barrier.wait()
            return self.draft(existing=first["project_id"],
                              rev=first["project_revision"],mode=mode)
        with ThreadPoolExecutor(max_workers=2) as pool:
            x=pool.submit(write,"reel")
            y=pool.submit(write,"360")
            results=[x.result(),y.result()]
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        saved=[r for r in results if r.status_code==200][0]
        latest=json.loads((Path(first["project_id"])/"project.json").read_text())
        self.assertEqual(latest["settings"]["wizard"]["platform"],
                         saved.json["project"]["settings"]["wizard"]["platform"])

    def test_rename_preserves_path_and_reopen_and_rejects_stale(self):
        first=self.create()
        original=Path(first["project_id"])
        result=self.app.test_client().post(
            "/api/v1/wizard/projects/rename",
            json={"path":str(original),"name":"New Title",
                  "project_revision":first["project_revision"]})
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(result.json["path"],str(original))
        self.assertNotEqual(result.json["project_revision"],first["project_revision"])
        self.assertEqual(json.loads((original/"project.json").read_text())["name"],
                         "New Title")
        stale=self.app.test_client().post(
            "/api/v1/wizard/projects/rename",
            json={"path":str(original),"name":"Old title",
                  "project_revision":first["project_revision"]})
        self.assertEqual(stale.status_code,409)
        reopened=self.app.test_client().post(
            "/api/v1/wizard/projects/open",json={"path":str(original)})
        self.assertEqual(reopened.status_code,200)
        self.assertEqual(reopened.json["project_revision"],result.json["project_revision"])

    def test_duplicate_is_independent_native_copy_and_reopens_export(self):
        source=self.create()
        path=Path(source["project_id"])
        media=path/"exports"/"finished.mp4"
        media.write_bytes(self.video.read_bytes())
        original_hash=hashlib.sha256(media.read_bytes()).hexdigest()
        response=self.app.test_client().post(
            "/api/v1/wizard/projects/duplicate",
            json={"path":str(path),"name":"Concert Copy",
                  "project_revision":source["project_revision"]})
        self.assertEqual(response.status_code,201,response.json)
        dest=Path(response.json["path"])
        self.assertNotEqual(dest,path)
        self.assertTrue((dest/"project.json").is_file())
        self.assertEqual((dest/"exports"/"finished.mp4").read_bytes(),
                         media.read_bytes())
        self.assertNotEqual((dest/"exports"/"finished.mp4").stat().st_ino,
                            media.stat().st_ino)
        self.assertEqual(hashlib.sha256(media.read_bytes()).hexdigest(),original_hash)
        reopened=self.app.test_client().post(
            "/api/v1/wizard/projects/open",json={"path":str(dest)})
        self.assertEqual(reopened.status_code,200,reopened.json)
        self.assertEqual(reopened.json["project_revision"],
                         response.json["project_revision"])
        self.assertEqual(len(list(self.projects.glob("*.zuckervid"))),2)
        probe=subprocess.run([
            "ffprobe","-v","error","-show_entries","stream=codec_type",
            "-of","csv=p=0",str(dest/"exports"/"finished.mp4")
        ],capture_output=True,text=True,timeout=20,check=True)
        self.assertIn("video",probe.stdout)

    def test_rename_save_race_no_unrecorded_overwrite(self):
        first=self.create()
        barrier=Barrier(2)
        def rename():
            barrier.wait()
            return self.app.test_client().post(
                "/api/v1/wizard/projects/rename",
                json={"path":first["project_id"],"name":"New Name",
                      "project_revision":first["project_revision"]})
        def save():
            barrier.wait()
            return self.draft(existing=first["project_id"],
                              rev=first["project_revision"],mode="reel")
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(rename); b=pool.submit(save)
            results=[a.result(),b.result()]
        self.assertEqual(sorted(x.status_code for x in results),[200,409])
        winning=[x for x in results if x.status_code==200][0]
        self.assertEqual(project_revision(Path(first["project_id"])),
                         winning.json["project_revision"])

    def test_duplicate_save_race_no_stale_clone(self):
        first=self.create()
        barrier=Barrier(2)
        def duplicate():
            barrier.wait()
            return self.app.test_client().post(
                "/api/v1/wizard/projects/duplicate",
                json={"path":first["project_id"],"name":"Clone",
                      "project_revision":first["project_revision"]})
        def save():
            barrier.wait()
            return self.draft(existing=first["project_id"],
                              rev=first["project_revision"],mode="reel")
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(duplicate); b=pool.submit(save)
            results=[a.result(),b.result()]
        # Clone first may succeed and then Save succeeds on the original;
        # or Save wins first and rejects the now-stale clone.
        self.assertIn(sorted(x.status_code for x in results),
                      ([200,201],[200,409]))
        self.assertEqual(len(list(self.projects.glob("*.zuckervid"))),
                         2 if any(x.status_code==201 for x in results) else 1)

    def test_legacy_mutation_routes_fail_closed_under_strict_mode(self):
        response=self.app.test_client().post("/api/v1/inputs/videos",
                                             json={"paths":[str(self.video)]})
        self.assertEqual(response.status_code,423)
        self.assertEqual(self.app.test_client().post(
            "/api/v1/wizard/prepare",json={}).status_code,423)

    def test_untrusted_paths_refused(self):
        existing=self.create()
        other=self.root/"elsewhere.zuckervid"
        other.mkdir()
        (other/"project.json").write_text("{}")
        response=self.app.test_client().post(
            "/api/v1/wizard/projects/rename",
            json={"path":str(other),"name":"Escape",
                  "project_revision":existing["project_revision"]})
        self.assertEqual(response.status_code,409)
        link=self.projects/"linked.zuckervid"
        link.symlink_to(Path(existing["project_id"]),target_is_directory=True)
        response=self.app.test_client().post(
            "/api/v1/wizard/projects/rename",
            json={"path":str(link),"name":"Escape",
                  "project_revision":existing["project_revision"]})
        self.assertEqual(response.status_code,409)

    def test_new_routes_disabled_by_default(self):
        old=self.app.config["CANONICAL_380_STRICT"]
        self.app.config["CANONICAL_380_STRICT"]=False
        try:
            self.assertEqual(self.app.test_client().post(
                "/api/v1/wizard/projects/rename",
                json={"path":"/tmp","name":"X","project_revision":"a"*64}
            ).status_code,404)
        finally:
            self.app.config["CANONICAL_380_STRICT"]=old


if __name__=="__main__":
    unittest.main()
