from __future__ import annotations

import json

from core import build_info


def test_build_info_reads_packaged_metadata(tmp_path, monkeypatch):
    (tmp_path / "build_info.json").write_text(
        json.dumps({"version": "0.1", "git_commit": "abc1234"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_info.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert build_info.build_info() == {"version": "0.1", "git_commit": "abc1234"}
    assert build_info.startup_label() == "Zucker Editor v0.1 git=abc1234"
