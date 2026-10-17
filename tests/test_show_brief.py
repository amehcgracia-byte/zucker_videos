"""The person's idea is asked before the show is planned, kept in the project, read from
documents, and followed first by the director (its gags placed before the director's own)."""
import io
import json
import zipfile

import pytest

from core import gag_director, show_brief, show_proposals
from core.project import create_project


def test_the_idea_is_kept_and_needed_unless_automatic(tmp_path):
    project = create_project("Idea", str(tmp_path / "Idea.zuckervid"))
    assert show_brief.load(project)["mode"] == "automatic" and show_brief.idea_for_director(project) == ""
    with pytest.raises(ValueError):
        show_brief.save(project, "together", "  ")
    show_brief.save(project, "together", "When I say lazy, the guy on the sofa.\r\n\r\n\r\nThen the plant answers.", "dictated")
    assert show_brief.idea_for_director(project) == "When I say lazy, the guy on the sofa.\n\nThen the plant answers."
    show_brief.save(project, "automatic", "ignored")
    assert show_brief.idea_for_director(project) == ""


def test_documents_become_text():
    document = io.BytesIO()
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:t>El tío vago</w:t></w:r></w:p>'
                                              '<w:p><w:r><w:t xml:space="preserve">en el sofá &amp; la planta</w:t></w:r></w:p></w:body></w:document>')
    assert show_brief.document_text("guion.docx", document.getvalue()) == "El tío vago\nen el sofá & la planta"
    assert show_brief.document_text("idea.srt", "1\n00:00:01,000 --> 00:00:02,000\nHola\n\n2\n00:00:03,000 --> 00:00:04,000\nqué tal\n".encode()) == "Hola\nqué tal"
    assert "sofá" in show_brief.document_text("idea.txt", "el sofá".encode("latin-1"))
    with pytest.raises(ValueError):
        show_brief.document_text("idea.pdf", b"%PDF")


def test_the_director_follows_the_idea_first():
    prompt = gag_director.build_prompt([{"text": "hola", "start": 0, "end": 1}], [], idea="The guy on the sofa {when I say lazy}")
    assert prompt.index("The guy on the sofa {when I say lazy}") < prompt.index("Rules:")
    assert "from_idea" in prompt
    assert "BEFORE you plan" not in gag_director.build_prompt([{"text": "hola", "start": 0, "end": 1}], [])
    raw = [{"kind": "drawing", "at": 10.0, "duration": 3, "subject": "a sofa (director)"},
           {"kind": "drawing", "at": 11.0, "duration": 3, "subject": "the lazy guy on the sofa", "from_idea": True},
           {"kind": "drawing", "at": 20.0, "duration": 3, "subject": "the plant answering", "from_idea": True}]
    kept = gag_director.clean_gags(raw, 60.0)
    assert [gag["subject"] for gag in kept] == ["the lazy guy on the sofa", "the plant answering"]   # theirs first, even 9 s apart
    reasons = [item["reason"] for item in show_proposals.from_gags(kept)]
    assert reasons == ["Your idea: the lazy guy on the sofa", "Your idea: the plant answering"]


def test_starting_a_show_needs_the_idea_or_automatic(tmp_path, monkeypatch):
    from server.api import create_app
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Start", str(tmp_path / "Start.zuckervid"))
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"x")
    project.data["inputs"]["videos"] = [{"path": str(video)}]
    project.save()
    app = create_app(str(project.folder), dev=True)
    client = app.test_client()
    body = {"platform": "show", "project_id": str(project.folder), "videos": [str(video)], "show_gags": "off"}
    refused = client.post("/api/v1/wizard/start", json={**body, "show_idea_mode": "together", "show_idea": ""})
    assert refused.status_code == 400 and refused.get_json()["error"]["code"] == "idea_needed"
    assert not (project.artifacts_dir / "show_brief.json").exists()
