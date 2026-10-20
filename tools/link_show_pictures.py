"""Link approved Show drawings to their exact pictures, so a render reuses them
instead of painting new ones.

  python tools/link_show_pictures.py PROJECT.zuckervid LINKS.json

LINKS.json: [{"proposal": "drawing-…", "picture": "/abs/PROJECT/assets/show-library/approved/x.png",
              "sha256": "…"}, ...]

Each picture must lie inside the project's own library (assets/show-library) and
have exactly the given SHA-256; only approved drawings are linked. The previous
show_proposals.json is kept beside it, the new one is written atomically and then
read back to check every link. Pictures are never changed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def link(project: Path, links: list[dict]) -> list[dict]:
    proposals_path = project / "artifacts" / "show_proposals.json"
    library = (project / "assets" / "show-library").resolve()
    document = json.loads(proposals_path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in document["proposals"]}
    for entry in links:
        item = by_id.get(entry["proposal"])
        picture = Path(entry["picture"]).resolve()
        if item is None or item["kind"] != "drawing" or item["status"] != "approved":
            raise SystemExit(f"{entry['proposal']} is not an approved drawing of this project")
        if library not in picture.parents or not picture.is_file():
            raise SystemExit(f"{picture} is not a picture in this project's library")
        if sha256(picture) != entry["sha256"]:
            raise SystemExit(f"{picture.name} is not the expected picture (SHA-256 differs)")
        item["params"]["picture"] = str(picture)
    shutil.copy2(proposals_path, proposals_path.with_name(f"show_proposals-before-links-{time.strftime('%Y%m%d-%H%M%S')}.json"))
    pending = proposals_path.with_suffix(".pending")
    pending.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(proposals_path)
    saved = {item["id"]: item for item in json.loads(proposals_path.read_text(encoding="utf-8"))["proposals"]}
    done = []
    for entry in links:
        picture = Path(saved[entry["proposal"]]["params"]["picture"])
        if sha256(picture) != entry["sha256"]:
            raise SystemExit(f"After saving, {entry['proposal']} does not point at its picture")
        done.append({"proposal": entry["proposal"], "picture": str(picture), "sha256": entry["sha256"]})
    return done


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    for row in link(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())):
        print(row["sha256"][:16], row["proposal"], Path(row["picture"]).name)
