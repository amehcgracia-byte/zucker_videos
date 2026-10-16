"""Copy chosen pictures into a Show project's own library, exactly.

  python tools/curate_show_library.py PROJECT.zuckervid LIST.json

LIST.json: [{"source": "/path/picture.png", "name": "Sun · original crayon drawing",
             "origin": "Codex · chosen artwork", "file": "sun-crayon.png"}, ...]
("file" may name a subfolder, e.g. "approved/money.png"; "retired": true keeps a
picture out of the sticker picker while drawings can still be linked to it.)

Each picture is copied into PROJECT/assets/show-library/ and checked byte for
byte (SHA-256) against its source; the catalog the storyboard reads
(assets/show-library/catalog.json) records its name, origin, source and hash.
Nothing is overwritten: a different file already there under the same name is
kept and the new one gets another name; an identical one is left as it is.
The previous catalog is kept beside it. Sources are never moved or changed.
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


def curate(project: Path, chosen: list[dict]) -> list[dict]:
    library = project / "assets" / "show-library"
    library.mkdir(parents=True, exist_ok=True)
    catalog_path = library / "catalog.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.is_file() else {}
    if catalog_path.is_file():
        shutil.copy2(catalog_path, library / f"catalog-before-{time.strftime('%Y%m%d-%H%M%S')}.json")
    done = []
    for entry in chosen:
        source = Path(entry["source"])
        digest = sha256(source)
        target = library / entry.get("file", source.name)
        n = 2
        while target.exists() and sha256(target) != digest:
            target = target.with_name(f"{Path(entry.get('file', source.name)).stem} {n}{target.suffix}")
            n += 1
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if sha256(target) != digest:
            raise SystemExit(f"Copy of {source} does not match its source")
        catalog[str(target.resolve())] = {"name": entry["name"], "origin": entry["origin"], "retired": bool(entry.get("retired", False)),
                                          "sha256": digest, "source": str(source)}
        done.append({"path": str(target.resolve()), "sha256": digest, "source": str(source)})
    pending = catalog_path.with_suffix(".pending")
    pending.write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
    pending.replace(catalog_path)
    return done


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    for item in curate(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())):
        print(item["sha256"][:16], item["path"])
