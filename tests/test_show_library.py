"""The library: pictures you already have are found by name, used instead of drawn again,
kept in the project once approved, and can be made recurring for every project."""
import json
import time
from pathlib import Path

from PIL import Image

from core import show_library, show_proposals, show_scenes
from core.project import create_project
from server.api import create_app


def _picture(path: Path, colour=(200, 60, 40, 255)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (60, 40), (0, 0, 0, 0))
    image.paste(colour, (10, 5, 50, 35))
    image.save(path)
    return path


def _project(tmp_path, name, proposals=()):
    project = create_project(name, str(tmp_path / f"{name}.zuckervid"))
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 10, "shots": [], "words": []}))
    show_proposals.save(project.artifacts_dir / "show_proposals.json", list(proposals))
    show_scenes.save(project.artifacts_dir / "show_scenes.json", {"identity": {}, "scenes": [
        {"id": "scene-1", "name": "Scene 1", "text": "x", "start": 0, "end": 10, "effects": [item["id"] for item in proposals],
         "overlays": [], "decision": "pending", "proposal": ""}]})
    return project


def test_names_match_what_a_drawing_shows_in_other_words_too():
    sofa = {"name": "A lazy man in underwear sprawled on a saggy sofa · crayon", "path": "/x/lazy-6cb396.png"}
    plant = {"name": "Simón’s plant character · original close-up", "path": "/x/plant-character.png"}
    guitar = {"name": "electric guitar chatgpt", "path": "/x/electric-guitar-chatgpt.png"}
    assert show_library.match("a lazy guy on a couch", sofa) == show_library.match("el tío vago en el sofá", sofa) >= 0.8
    long = "a lazy guy in underwear on a couch scratching himself, holding a TV remote"
    assert show_library.best([plant, {"name": "Lazy man on a sofa · chosen ChatGPT image", "path": "/x/l.png"}], long)["path"] == "/x/l.png"
    assert show_library.best([{"name": "a tornado", "path": "/x/t.png"}], "a tornado made of spinning clocks") is None   # offered, not used
    assert show_library.suggest([{"name": "a tornado", "path": "/x/t.png"}], "a tornado made of spinning clocks")
    assert show_library.match("a guitar", plant) == 0
    assert show_library.best([plant, sofa, guitar], "a lazy guy on the sofa")["path"] == sofa["path"]
    assert show_library.best([plant, sofa, guitar], "a guitar")["path"] == guitar["path"]
    assert show_library.best([plant, sofa, guitar], "a lazy dog") is None          # one word of two is not a clear match
    assert [item["path"] for item in show_library.suggest([plant, {**sofa, "retired": True}], "the plant")] == [plant["path"]]


def test_the_library_lists_this_project_recurring_shelves_and_other_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    drawn = _picture(tmp_path / "Other.zuckervid" / "assets" / "show-library" / "drawn" / "sofa-1.png")
    hidden_draft = _picture(tmp_path / "Other.zuckervid" / "assets" / "show-library" / "drawn" / "draft-2.png", (10, 10, 200, 255))
    sofa = show_proposals.proposal("drawing", 1, 2, reason="Director", kind="drawing", subject="a lazy man on a sofa",
                                   picture=str(drawn.resolve()))
    sofa["status"] = "approved"
    other = _project(tmp_path, "Other", [sofa])
    for path in (drawn, hidden_draft):
        show_library.catalog_set(other.folder, path, name=path.stem, retired=True)        # drafts are hidden until approved
    show_library.catalog_set(other.folder, drawn, name="Lazy man on the sofa")
    here = _project(tmp_path, "Here")
    mine = _picture(here.folder / "assets" / "show-library" / "yours" / "my potato.png", (40, 160, 40, 255))
    _picture(tmp_path / "data" / "Gag library" / "Stickers" / "coffee cup.png", (90, 50, 20, 255))
    _picture(tmp_path / "data" / "Gag library" / "Painted" / "Ceras" / "cache 1a2b3c.png")
    monkeypatch.setattr(show_library, "shelf_projects", lambda: [("Other", other.folder), ("Here", here.folder)])
    client = create_app(str(here.folder), dev=True).test_client()

    items = client.get("/api/v1/show/library?others=1").get_json()["items"]
    by_name = {item["name"]: item for item in items}
    assert by_name["my potato"]["section"] == "project"
    assert by_name["coffee cup"]["section"] == "recurring" and by_name["coffee cup"]["shelf"] == "Stickers"
    assert by_name["Lazy man on the sofa"]["section"] == "other" and by_name["Lazy man on the sofa"]["project"] == "Other"
    assert "draft-2" not in by_name and "cache 1a2b3c" not in by_name                 # a draft never approved, a painter's cache
    assert client.get(by_name["Lazy man on the sofa"]["url"]).status_code == 200      # its thumbnail is served
    assert client.get("/api/v1/show/library/file", query_string={"path": str(hidden_draft.resolve())}).status_code == 404

    found = client.get("/api/v1/show/library/suggest", query_string={"subject": "a lazy guy on a couch"}).get_json()["items"]
    assert [item["name"] for item in found] == ["Lazy man on the sofa"]

    # Used here: copied once into this project, named and visible in its library.
    used = client.post("/api/v1/show/library/use", json={"path": by_name["Lazy man on the sofa"]["path"]}).get_json()
    assert Path(used["path"]).parent == (here.folder / "assets" / "show-library" / "from library").resolve()
    assert Path(used["path"]).read_bytes() == drawn.read_bytes()
    again = client.post("/api/v1/show/library/use", json={"path": by_name["Lazy man on the sofa"]["path"]}).get_json()
    assert again["path"] == used["path"]                                              # not copied twice
    mine_now = {item["path"]: item for item in client.get("/api/v1/show/library").get_json()["items"]}
    assert mine_now[used["path"]]["section"] == "project" and "from Other" in mine_now[used["path"]]["origin"]
    assert str(mine.resolve()) in mine_now


def test_a_picture_made_recurring_is_in_every_project_and_can_go_back_to_the_trash(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    trash = tmp_path / "Trash"
    from core import trash as trash_module
    monkeypatch.setattr(trash_module, "_default_trash_root", lambda path: trash)
    here = _project(tmp_path, "Here")
    mine = _picture(here.folder / "assets" / "show-library" / "yours" / "mask.png", (230, 230, 230, 255))
    character = here.folder / "assets" / "show-library" / "Characters" / "Fridge"
    _picture(character / "cutout.png", (220, 20, 20, 255))
    _picture(character / "mask.png", (255, 255, 255, 255))
    (character / "character.json").write_text(json.dumps({"name": "Fridge", "mouth": [0.4, 0.6, 0.2, 0.1]}))
    client = create_app(str(here.folder), dev=True).test_client()

    made = client.post("/api/v1/show/library/recurring", json={"path": str(mine.resolve()), "shelf": "Masks", "name": "Carnival mask"}).get_json()
    assert Path(made["path"]) == (tmp_path / "data" / "Gag library" / "Masks" / "Carnival mask.png").resolve()
    rig = client.post("/api/v1/show/library/recurring", json={"path": str((character / "cutout.png").resolve()), "shelf": "Characters"}).get_json()
    assert json.loads((Path(rig["path"]).parent / "character.json").read_text())["name"] == "Fridge"   # it keeps its rig
    bad = client.post("/api/v1/show/library/recurring", json={"path": str(mine.resolve()), "shelf": "Shoes"})
    assert bad.status_code == 400

    other = _project(tmp_path, "Other")
    elsewhere = {item["path"]: item for item in create_app(str(other.folder), dev=True).test_client().get("/api/v1/show/library").get_json()["items"]}
    assert elsewhere[made["path"]]["section"] == "recurring" and elsewhere[made["path"]]["shelf"] == "Masks"
    assert elsewhere[rig["path"]]["character"] is True and elsewhere[rig["path"]]["name"].startswith("Fridge")
    listed = {item["path"]: item for item in client.get("/api/v1/show/library").get_json()["items"]}
    assert listed[str(mine.resolve())]["recurring"] == "Masks"                        # this project's copy says so

    gone = client.post("/api/v1/show/library/recurring", json={"path": made["path"], "recurring": False})
    assert gone.status_code == 200 and not Path(made["path"]).exists() and (trash / "Carnival mask.png").is_file()
    assert mine.is_file()                                                             # the project's own copy stays
    refused = client.post("/api/v1/show/library/recurring", json={"path": str(mine.resolve()), "recurring": False})
    assert refused.status_code == 400


def test_choosing_a_library_picture_for_a_drawing_links_it_and_approving_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    drawing = show_proposals.proposal("drawing", 2, 2, reason="Director", kind="drawing", subject="a lazy guy on a sofa", style="crayon")
    here = _project(tmp_path, "Here", [drawing])
    sofa = _picture(tmp_path / "data" / "Gag library" / "Drawings" / "lazy man on sofa.png")
    client = create_app(str(here.folder), dev=True).test_client()

    linked = client.post(f"/api/v1/show/review/{drawing['id']}/picture", json={"path": str(sofa.resolve())}).get_json()
    picture = Path(linked["params"]["picture"])
    assert picture.parent == (here.folder / "assets" / "show-library" / "from library").resolve()  # copied into the project
    assert linked["params"]["draft"] is False and linked["params"]["image_engine"] == "Your library · lazy man on sofa"
    assert linked["params"]["picture_sha256"] == show_library.sha256(picture)
    assert show_scenes.load(here.artifacts_dir / "show_scenes.json")["scenes"][0]["decision"] == "modified"
    missing = client.post(f"/api/v1/show/review/{drawing['id']}/picture", json={"path": "/etc/hosts"})
    assert missing.status_code == 404

    # A draft drawn for the storyboard is hidden in the library until it is approved.
    from core import show_drafts
    kept = show_drafts.keep(here, Image.open(sofa), "a lazy guy on a sofa", "Claude · draft")
    listed = {item["path"] for item in client.get("/api/v1/show/library").get_json()["items"]}
    assert kept["path"] not in listed
    client.patch(f"/api/v1/show/review/{drawing['id']}", json={"picture": kept["path"]})
    assert client.patch(f"/api/v1/show/review/{drawing['id']}", json={"status": "approved"}).status_code == 200
    listed = {item["path"]: item for item in client.get("/api/v1/show/library").get_json()["items"]}
    assert listed[kept["path"]]["approved"] is True                                   # approved: it stays in the library
    client.post("/api/v1/show/library/visibility", json={"path": kept["path"], "retired": True})
    assert kept["path"] not in {item["path"] for item in client.get("/api/v1/show/library").get_json()["items"]}  # unless you hide it


def test_accepting_a_scene_uses_what_the_library_has_instead_of_drawing_it(tmp_path, monkeypatch):
    from core import gags
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    sofa_drawing = show_proposals.proposal("drawing", 2, 2, reason="Director", kind="drawing", subject="a lazy guy on a sofa", style="crayon")
    tornado = show_proposals.proposal("drawing", 6, 2, reason="Director", kind="drawing", subject="a tornado", style="crayon")
    here = _project(tmp_path, "Here", [sofa_drawing, tornado])
    sofa = _picture(tmp_path / "data" / "Gag library" / "Drawings" / "lazy man on sofa.png")
    drawn = []
    monkeypatch.setattr(gags, "painted_picture", lambda subject, style, **kw: drawn.append(subject) or Image.new("RGBA", (40, 40), (1, 2, 3, 255)))
    client = create_app(str(here.folder), dev=True).test_client()
    client.post("/api/v1/show/scenes/scene-1/decision", json={"decision": "accepted"})
    for _ in range(200):
        items = {p["id"]: p for p in client.get("/api/v1/show/scenes").get_json()["proposals"]}
        if all(item["params"].get("picture") for item in items.values()):
            break
        time.sleep(0.02)
    assert drawn == ["a tornado"]                                                     # the sofa was not drawn again
    linked = items[sofa_drawing["id"]]["params"]
    assert Path(linked["picture"]).read_bytes() == sofa.read_bytes() and linked["draft"] is True
    assert linked["image_engine"] == "Your library · lazy man on sofa"
    assert items[sofa_drawing["id"]]["status"] == "proposed"                         # offered, still to approve


def test_take_from_the_library_is_offered_for_selected_words_and_in_the_add_list():
    import subprocess
    script = Path("web/show_review.js").read_text()
    menu = script[script.index("  function sayMenu("):script.index("  document.addEventListener('pointerdown',event=>{if(event.target.closest?.('#showSayTools'))")]
    assert '["library", "Take from the library…", null]' in menu
    assert '<option value="library">Take from the library</option>' in script
    tool = script[script.index("  async function extraTool("):script.index("  async function addEffect(")]
    program = ("const calls = []; const openLibrary = async (...args) => calls.push(args);\n" + tool +
               "extraTool('library', {id: 'scene-1'}, {text: 'good morning', start: 3, end: 4}).then((handled) => {"
               " if (!handled || calls.length !== 1 || calls[0][0] !== null || calls[0][1].text !== 'good morning') process.exit(1); });")
    subprocess.run(["node", "-e", program], check=True)
