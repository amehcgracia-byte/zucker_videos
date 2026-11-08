"""Chosen pictures are copied into a project's library byte for byte, and approved
drawings are linked to them only when the picture is exactly the expected one."""
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import curate_show_library  # noqa: E402
import link_show_pictures  # noqa: E402


def test_approved_drawings_are_linked_to_their_exact_pictures(tmp_path):
    project = tmp_path / "P.zuckervid"
    (project / "artifacts").mkdir(parents=True)
    source = tmp_path / "money 5d8c11.png"
    Image.new("RGBA", (40, 30), (10, 200, 30, 255)).save(source)
    proposals = [{"id": "drawing-1", "kind": "drawing", "status": "approved", "params": {"subject": "money"}},
                 {"id": "drawing-2", "kind": "drawing", "status": "pending", "params": {"subject": "camera"}}]
    (project / "artifacts" / "show_proposals.json").write_text(json.dumps({"proposals": proposals}))

    copied = curate_show_library.curate(project, [{"source": str(source), "name": "Money", "origin": "Liked render",
                                                   "file": "approved/money.png", "retired": True}])[0]
    catalog = json.loads((project / "assets" / "show-library" / "catalog.json").read_text())
    assert Path(copied["path"]).parent.name == "approved" and catalog[copied["path"]]["retired"] is True
    assert Path(copied["path"]).read_bytes() == source.read_bytes()

    with pytest.raises(SystemExit, match="SHA-256"):
        link_show_pictures.link(project, [{"proposal": "drawing-1", "picture": copied["path"], "sha256": "0" * 64}])
    with pytest.raises(SystemExit, match="not an approved drawing"):
        link_show_pictures.link(project, [{"proposal": "drawing-2", "picture": copied["path"], "sha256": copied["sha256"]}])
    with pytest.raises(SystemExit, match="library"):
        link_show_pictures.link(project, [{"proposal": "drawing-1", "picture": str(source), "sha256": copied["sha256"]}])
    assert "picture" not in json.loads((project / "artifacts" / "show_proposals.json").read_text())["proposals"][0]["params"]

    link_show_pictures.link(project, [{"proposal": "drawing-1", "picture": copied["path"], "sha256": copied["sha256"]}])
    saved = json.loads((project / "artifacts" / "show_proposals.json").read_text())["proposals"]
    assert saved[0]["params"]["picture"] == copied["path"] and "picture" not in saved[1]["params"]
    assert list((project / "artifacts").glob("show_proposals-before-links-*.json"))
