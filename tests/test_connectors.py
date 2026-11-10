"""Connectors: each account's state is read from its own tool, and signing in or switching
opens that tool's sign-in for the person (the app never handles a password)."""
import json
import subprocess

import pytest

from core import connectors


def fake(outputs):
    def run(command, timeout=20.0):
        key = " ".join(command[1:3])
        out, code = outputs.get(key, ("", 1))
        return subprocess.CompletedProcess(command, code, stdout=out, stderr="")
    return run


def test_states_come_from_each_tool(monkeypatch):
    monkeypatch.setattr(connectors, "_cli", lambda name: f"/bin/{name}")
    monkeypatch.setattr(connectors, "_run", fake({
        "auth status": (json.dumps({"loggedIn": True, "email": "me@example.com", "subscriptionType": "pro"}), 0),
        "login status": ("Logged in using ChatGPT", 0),
    }))
    assert connectors.status("claude") == {"installed": True, "signed_in": True, "account": "me@example.com", "plan": "Pro"}
    assert connectors.status("chatgpt")["signed_in"] is True
    monkeypatch.setattr(connectors, "_run", fake({"login status": ("Not logged in", 1),
                                                  "auth status": ("github.com\n  ✓ Logged in to github.com account someone (keyring)\n", 0)}))
    assert connectors.status("chatgpt")["signed_in"] is False
    assert connectors.status("github") == {"installed": True, "signed_in": True, "account": "someone"}
    with pytest.raises(KeyError):
        connectors.status("myspace")


def test_switching_opens_the_tools_own_sign_in(monkeypatch):
    opened = []
    monkeypatch.setattr(connectors, "_cli", lambda name: f"/bin/{name}")
    monkeypatch.setattr(connectors.subprocess, "run", lambda command, **kw: opened.append(command))
    monkeypatch.setattr(connectors.sys, "platform", "darwin")
    assert connectors.open_sign_in("chatgpt", switch=True) == "/bin/codex logout; /bin/codex login --device-auth"
    assert connectors.open_sign_in("claude") == "/bin/claude auth login --claudeai"
    assert opened[0][0] == "osascript" and "Terminal" in opened[0][2]
    with pytest.raises(ValueError):
        connectors.hugging_face_sign_in("my-password")                      # only an hf_ token is accepted
