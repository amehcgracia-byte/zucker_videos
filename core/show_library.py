"""The Show library: pictures you already have, to use again instead of drawing them again.

Three places, one list:
  this project    assets/show-library: your pictures and cut-outs, and every drawing you
                  approved (a draft stays hidden until it is approved)
  recurring       the app's own "Gag library", on shelves (Characters, Stickers, Masks,
                  Drawings, Photos): what you mark as recurring is there in every project
  other projects  the libraries of the other projects on your shelf, to bring one over
A picture used from a recurring shelf or another project is copied once into this
project, so the project keeps working if the original moves or is removed. Nothing here
draws or downloads anything: it finds, names, copies and matches files you already have.
Matching is by name only (what is drawn), so a match is offered, never approved for you.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import unicodedata
from pathlib import Path
from typing import Any, Iterable

SHELVES = ("Characters", "Stickers", "Masks", "Drawings", "Photos")
CACHES = ("Painted", "Claude drawings")       # what the painters keep for themselves, not shelves
LOOSE = "Gag library"                           # pictures dropped straight in the library folder
SUFFIXES = {".png", ".webp", ".jpg", ".jpeg"}
SECTION_ORDER = {"project": 0, "recurring": 1, "other": 2}

_catalog_lock = threading.Lock()
_hashes: dict[str, tuple[int, int, str]] = {}

# Words that say how something is drawn or where it came from, not what it is.
_STOP = {"the", "and", "with", "his", "her", "its", "for", "from", "into", "onto", "that", "this", "very", "some",
         "original", "drawing", "drawn", "crayon", "crayons", "pencil", "marker", "painting", "painted", "picture",
         "photo", "image", "copy", "working", "version", "variation", "chatgpt", "claude", "codex", "your", "liked",
         "render", "oct", "exact", "pixels", "chosen", "closeup", "close", "cutout", "cut", "out", "draft", "earlier", "png",
         "una", "uno", "los", "las", "del", "con", "por", "para", "que", "dibujo"}
# Same thing, other words (and the Spanish ones you may type).
_SAME = {"guy": "man", "dude": "man", "bloke": "man", "fellow": "man", "tio": "man", "hombre": "man", "chico": "man",
         "couch": "sofa", "settee": "sofa", "vago": "lazy", "perezoso": "lazy", "planta": "plant", "nevera": "fridge",
         "frigorifico": "fridge", "bombilla": "lightbulb", "bulb": "lightbulb", "dinero": "money", "guitarra": "guitar",
         "reloj": "clock", "despertador": "alarm", "toro": "bull", "sol": "sun", "camara": "camera", "pulgar": "thumb",
         "tornado": "tornado", "pincel": "brush", "paleta": "palette", "adolescente": "teenager", "teen": "teenager"}


def words(text: str) -> set[str]:
    """What a name or a subject is about, in comparable words."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    found = set()
    for word in re.split(r"[^a-z0-9]+", text):
        if len(word) < 3 or word in _STOP or any(c.isdigit() for c in word):
            continue
        word = _SAME.get(word, word)
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        found.add(_SAME.get(word, word))
    return found


def match(subject: str, item: dict[str, Any]) -> float:
    """How well a picture's name and the subject describe each other (0 to 1): a long
    subject ("a lazy guy in underwear on a couch, holding a remote") still finds a picture
    whose whole name is in it ("lazy man on a sofa")."""
    wanted = words(subject)
    have = words(item.get("name", "")) | words(Path(str(item.get("path", ""))).stem)
    if not wanted or not have:
        return 0.0
    shared = len(wanted & have)
    covers, covered = shared / len(wanted), shared / len(have)
    return round(0.6 * max(covers, covered) + 0.4 * min(covers, covered), 3)


def suggest(items: Iterable[dict[str, Any]], subject: str, *, floor: float = 0.5, limit: int = 4) -> list[dict[str, Any]]:
    """Pictures whose names match the subject, best first; this project before the others."""
    scored = [{**item, "match": score} for item in items if not item.get("retired") and (score := match(subject, item)) >= floor]
    scored.sort(key=lambda item: (-item["match"], SECTION_ORDER.get(item.get("section", "project"), 3)))
    seen, out = set(), []
    for item in scored:
        key = item.get("sha256") or item["path"]
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out[:limit]


def best(items: Iterable[dict[str, Any]], subject: str) -> dict[str, Any] | None:
    """The picture to offer as a drawing's draft instead of drawing it again: a clear match only."""
    wanted = words(subject)
    for item in suggest(items, subject, floor=0.66, limit=1):
        if len(wanted & (words(item.get("name", "")) | words(Path(item["path"]).stem))) >= min(2, len(wanted)):
            return item
    return None


def sha256(path: Path) -> str:
    """The file's SHA-256, remembered while the file stays the same."""
    stat = path.stat()
    known = _hashes.get(str(path))
    if known and known[:2] == (stat.st_size, stat.st_mtime_ns):
        return known[2]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _hashes[str(path)] = (stat.st_size, stat.st_mtime_ns, digest)
    return digest


def is_character(path: Path) -> bool:
    """A photo character: its cut-out with the rig (mouth, eyes, voice) beside it."""
    return path.name == "cutout.png" and (path.parent / "character.json").is_file()


def character_name(path: Path) -> str:
    try:
        return str(json.loads((path.parent / "character.json").read_text(encoding="utf-8")).get("name") or path.parent.name)
    except (OSError, ValueError):
        return path.parent.name


def pictures(root: Path) -> list[Path]:
    """The pictures in a library folder (a character's mask is part of the character)."""
    if not root.is_dir():
        return []
    return [path for path in sorted(root.rglob("*")) if path.is_file() and path.suffix.lower() in SUFFIXES
            and not path.name.startswith(".") and not (path.name == "mask.png" and (path.parent / "character.json").is_file())]


# ---- The project's catalog: names, origins, hidden or not ---------------------------------

def catalog_path(project_folder: Path) -> Path:
    return project_folder / "assets" / "show-library" / "catalog.json"


def read_catalog(project_folder: Path) -> dict[str, Any]:
    try:
        return json.loads(catalog_path(project_folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def catalog_set(project_folder: Path, path: Path, **entry: Any) -> dict[str, Any]:
    """Add or change one picture's entry, written whole so a crash never leaves half a file."""
    target = catalog_path(project_folder)
    with _catalog_lock:
        catalog = read_catalog(project_folder)
        key = str(Path(path).resolve())
        catalog[key] = {**catalog.get(key, {}), **entry}
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_suffix(".pending")
        pending.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(target)
        return catalog[key]


def approved_pictures(project_folder: Path) -> set[str]:
    """Pictures of drawings you approved: they stay in the project's library."""
    try:
        proposals = json.loads((project_folder / "artifacts" / "show_proposals.json").read_text(encoding="utf-8"))["proposals"]
    except (OSError, ValueError, KeyError, TypeError):
        return set()
    return {str(Path(item["params"]["picture"]).resolve()) for item in proposals
            if item.get("status") == "approved" and (item.get("params") or {}).get("picture")}


def visible(meta: dict[str, Any], path: str, approved: set[str], default_hidden: bool) -> bool:
    """Shown unless you hid it; a draft is hidden until approved; approved ones always show."""
    if meta.get("removed"):
        return False
    if path in approved:
        return True
    return not meta.get("retired", default_hidden)


# ---- Recurring: the app's own shelves, there in every project ---------------------------

def recurring_root() -> Path:
    from core.gag_library import library_dir
    root = library_dir()
    for shelf in SHELVES:
        (root / shelf).mkdir(parents=True, exist_ok=True)
    return root


def shelf_of(path: Path, root: Path) -> str | None:
    """The shelf of a picture in the app's library (None for the painters' own caches);
    a picture dropped loose in the library folder is on no shelf in particular."""
    try:
        parts = Path(path).resolve().relative_to(root.resolve()).parts
    except ValueError:
        return None
    if len(parts) < 2:
        return LOOSE
    return None if parts[0] in CACHES else parts[0]


def recurring_hashes(root: Path) -> dict[str, str]:
    """SHA-256 of every recurring picture -> its shelf, to tell what is recurring already."""
    found = {}
    for path in pictures(root):
        shelf = shelf_of(path, root)
        if shelf:
            try:
                found[sha256(path)] = shelf
            except OSError:
                continue
    return found


def _safe(name: str, fallback: str = "picture") -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w .,'()-]+", " ", unicodedata.normalize("NFC", name))).strip(" .")[:70] or fallback


def _unique(target: Path) -> Path:
    if not target.exists():
        return target
    for number in range(2, 1000):
        candidate = target.with_name(f"{target.stem} {number}{target.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"Too many pictures called {target.name}")


def _copy_character(folder: Path, destination_root: Path, name: str) -> Path:
    """A character's whole folder (cut-out, mask, rig), so it keeps talking and looking."""
    for existing in destination_root.glob("*/cutout.png"):
        if (existing.parent / "character.json").is_file() and sha256(existing) == sha256(folder / "cutout.png"):
            return existing
    target = _unique(destination_root / _safe(name, "character"))
    pending = target.with_name(f".{target.name}.part")
    shutil.rmtree(pending, ignore_errors=True)
    shutil.copytree(folder, pending)
    pending.rename(target)
    return target / "cutout.png"


def _copy_picture(path: Path, folder: Path, name: str) -> Path:
    """One picture into a folder; an identical one already there is used instead."""
    folder.mkdir(parents=True, exist_ok=True)
    digest = sha256(path)
    for existing in folder.iterdir():
        if existing.is_file() and existing.suffix.lower() in SUFFIXES and sha256(existing) == digest:
            return existing
    target = _unique(folder / f"{_safe(name)}{path.suffix.lower()}")
    pending = target.with_name(f".{target.stem}.part{target.suffix}")
    shutil.copy2(path, pending)
    pending.replace(target)
    return target


def make_recurring(path: Path, shelf: str, name: str) -> Path:
    """Keep a copy on one of the app's shelves: it appears in every project from now on."""
    if shelf not in SHELVES:
        raise ValueError(f"Choose a shelf: {', '.join(SHELVES)}")
    path = Path(path)
    root = recurring_root()
    if is_character(path):
        return _copy_character(path.parent, root / shelf, name)
    return _copy_picture(path, root / shelf, name)


def forget_recurring(path: Path) -> Path | None:
    """Take a picture off its shelf, to the Trash (recoverable). Projects that used it keep their copy."""
    from core.trash import move_to_trash
    path = Path(path).resolve()
    root = recurring_root().resolve()
    shelf = shelf_of(path, root)
    if not shelf or shelf == LOOSE:
        raise ValueError("Only pictures on a recurring shelf can be taken off it")
    return move_to_trash(path.parent if is_character(path) and path.parent != root / shelf else path)


def bring_into(project_folder: Path, path: Path, name: str, origin: str) -> Path:
    """A recurring picture or another project's, copied once into this project's library."""
    path = Path(path)
    library = project_folder / "assets" / "show-library"
    if is_character(path):
        kept = _copy_character(path.parent, library / "Characters", name)
    else:
        kept = _copy_picture(path, library / "from library", name)
    catalog_set(project_folder, kept, name=name[:80], origin=origin, retired=False, removed=False, sha256=sha256(kept))
    return kept.resolve()


# ---- Other projects on your shelf --------------------------------------------------------

def shelf_projects() -> list[tuple[str, Path]]:
    """The projects on your shelf, as (name, folder)."""
    from server.projects import list_projects
    return [(entry["name"], Path(entry["path"])) for entry in list_projects()]


def other_projects(folders: Iterable[tuple[str, Path]], current: Path) -> list[dict[str, Any]]:
    """What the other projects' libraries hold and show: their pictures and approved drawings."""
    items = []
    current = Path(current).resolve()
    for project_name, folder in folders:
        folder = Path(folder)
        try:
            if folder.resolve() == current:
                continue
        except OSError:
            continue
        root = folder / "assets" / "show-library"
        catalog, approved = read_catalog(folder), approved_pictures(folder)
        for path in pictures(root):
            key = str(path.resolve())
            meta = catalog.get(key) or {}
            if not visible(meta, key, approved, default_hidden=False):
                continue
            items.append({"name": meta.get("name") or (character_name(path) + " · photo character" if is_character(path) else path.stem),
                          "path": key, "folder": path.parent.name, "origin": meta.get("origin") or "Your library",
                          "section": "other", "project": project_name, "retired": False,
                          "character": is_character(path)})
    return items
