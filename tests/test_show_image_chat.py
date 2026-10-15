"""Einstein: a chat that asks the person's own ChatGPT (Codex CLI) for a picture,
keeps every reply in the project, and places the chosen one on the scene."""
import json
import subprocess
import time

import pytest
from PIL import Image

from core import show_image_chat


def _fake_codex(monkeypatch, tmp_path, *, picture=True):
    """A Codex CLI that 'draws' into CODEX_HOME/generated_images/<thread>/ and prints its events."""
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(show_image_chat, "codex_binary", lambda: "/usr/local/bin/codex")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        thread = command[3] if command[2] == "resume" else "thread-1"
        if picture:
            folder = home / "generated_images" / thread
            folder.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (60, 40), (250, 200, 80, 255)).save(folder / f"exec-{len(calls)}.png")
        events = [{"type": "thread.started", "thread_id": thread},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "Here is a lazy guy on a sofa."}}]
        return subprocess.CompletedProcess(command, 0, "\n".join(json.dumps(e) for e in events), "")
    monkeypatch.setattr(show_image_chat.subprocess, "run", run)
    return calls


def _wait(chat):
    for _ in range(200):
        if not chat.load()["busy"]:
            return chat.load()
        time.sleep(0.02)
    raise AssertionError("still drawing")


def test_a_chat_asks_codex_with_chatgpt_and_keeps_each_picture(tmp_path, monkeypatch):
    calls = _fake_codex(monkeypatch, tmp_path)
    chat = show_image_chat.ImageChat(tmp_path / "chats")
    chat.send("a lazy guy on a sofa", context="I really dream about it")
    data = _wait(chat)
    assert data["thread"] == "thread-1" and data["error"] is None
    assert [m["role"] for m in data["messages"]] == ["person", "einstein"] and data["messages"][1]["images"] == ["image-1.png"]
    first = calls[0]
    assert first[:3] == ["/usr/local/bin/codex", "exec", "--json"] and "read-only" in first and "--ephemeral" not in first
    assert "image generation tool" in first[-1] and "a lazy guy on a sofa" in first[-1] and "I really dream" in first[-1]
    chat.send("make him lazier")                                                 # the same conversation, a new picture
    data = _wait(chat)
    assert calls[1][:4] == ["/usr/local/bin/codex", "exec", "resume", "thread-1"] and "make him lazier" in calls[1][-1]
    assert data["messages"][-1]["images"] == ["image-2.png"]
    again = show_image_chat.ImageChat(tmp_path / "chats", chat.id)                # read back from the project
    assert len(again.load()["messages"]) == 4 and again.image("image-2.png").is_file()
    with pytest.raises(ValueError):
        again.image("../chat.json")
    kept = again.accept("image-2.png", tmp_path / "library", "a lazy guy on a sofa!")
    assert kept.parent.name == "Stickers" and kept.name == "a lazy guy on a sofa.png"
    assert again.accept("image-2.png", tmp_path / "library", "a lazy guy on a sofa").name == "a lazy guy on a sofa 2.png"


def test_no_picture_or_no_cli_is_said_plainly(tmp_path, monkeypatch):
    _fake_codex(monkeypatch, tmp_path, picture=False)
    chat = show_image_chat.ImageChat(tmp_path / "chats")
    chat.send("something")
    assert "No picture" in _wait(chat)["error"]
    monkeypatch.setattr(show_image_chat, "codex_binary", lambda: None)
    assert show_image_chat.status()["ready"] is False
    other = show_image_chat.ImageChat(tmp_path / "chats")
    other.send("a cat")
    assert "not installed" in _wait(other)["error"]
    with pytest.raises(ValueError):
        show_image_chat.ImageChat(tmp_path / "chats", "../../etc")


def test_an_accepted_picture_is_placed_at_the_pinned_moment(tmp_path, monkeypatch):
    from tests.test_show_scenes import _storyboard
    project, client, document, _ = _storyboard(tmp_path, monkeypatch)
    _fake_codex(monkeypatch, tmp_path)
    scene = client.get("/api/v1/show/scenes").get_json()["scenes"][1]
    sent = client.post("/api/v1/show/einstein", json={"message": "a lazy guy on a sofa", "context": "so this is my show"})
    assert sent.status_code == 202
    chat = sent.get_json()["id"]
    for _ in range(200):
        data = client.get(f"/api/v1/show/einstein/{chat}").get_json()
        if not data["busy"]:
            break
        time.sleep(0.02)
    picture = data["messages"][-1]["images"][0]
    assert client.get(picture["url"]).status_code == 200
    placed = client.post(f"/api/v1/show/einstein/{chat}/accept", json={"image": picture["name"], "scene": scene["id"],
                                                                        "at": scene["start"] + 1.0, "duration": 2.5}).get_json()
    overlay = placed["scene"]["overlays"][placed["overlay"]]
    assert overlay["at"] == pytest.approx(scene["start"] + 1.0) and overlay["duration"] == 2.5 and overlay["asset"] == placed["asset"]
    assert placed["scene"]["decision"] == "modified"
    library = {item["path"] for item in client.get("/api/v1/show/library").get_json()["items"]}
    assert placed["asset"] in library                                            # usable again from the library
    assert client.post(f"/api/v1/show/einstein/{chat}/accept", json={"image": "image-9.png", "scene": scene["id"]}).status_code == 400


def test_a_used_up_chatgpt_allowance_is_said_with_when_it_comes_back(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(show_image_chat, "codex_binary", lambda: "/usr/local/bin/codex")
    limit = "You've hit your usage limit. Upgrade to Pro (https://x), visit https://y to purchase more credits or try again at Oct 16th, 2026 12:17 AM."
    events = [{"type": "thread.started", "thread_id": "t"}, {"type": "error", "message": limit}, {"type": "turn.failed", "error": {"message": limit}}]
    monkeypatch.setattr(show_image_chat.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(
        command, 1, "\n".join(json.dumps(e) for e in events), "Reading additional input from stdin..."))
    chat = show_image_chat.ImageChat(tmp_path / "chats")
    chat.send("a cat")
    error = _wait(chat)["error"]
    assert "allowance" in error and "Oct 16th, 2026 12:17 AM" in error and "This computer" in error
    assert "purchase" not in error and "stdin" not in error


def test_this_computer_s_painter_can_draw_it_instead_when_chosen(tmp_path, monkeypatch):
    from core import gag_paint, gags
    monkeypatch.setattr(gag_paint, "can_paint", lambda: True)
    painted = []
    monkeypatch.setattr(gags, "painted_picture", lambda subject, style, **kw: painted.append((subject, style)) or Image.new("RGBA", (30, 30), (1, 2, 3, 255)))
    chat = show_image_chat.ImageChat(tmp_path / "chats")
    chat.send("a lazy guy on a sofa", engine="local")
    data = _wait(chat)
    assert painted == [("a lazy guy on a sofa", "crayon")] and data["messages"][-1]["images"] == ["image-1.png"]
    assert data["messages"][0]["engine"] == "local"
    with pytest.raises(ValueError):
        chat.send("x", engine="dall-e")
