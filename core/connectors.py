"""Connectors: the accounts the app works with, their state, and switching them.

When one account runs out of allowance the person can sign in with another and carry on.
The app never sees passwords: Claude, ChatGPT (Codex) and GitHub sign in with their own
command-line tools in a Terminal window the person types into; Hugging Face stores the
token the person pastes into the app's own field (never sent anywhere but huggingface.co).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

CONNECTORS = [
    {"id": "claude", "name": "Claude", "used_for": "Plans your show; draws and letters with your Claude subscription."},
    {"id": "chatgpt", "name": "ChatGPT (Codex)", "used_for": "Images, hand lettering and realistic photo edits, with your ChatGPT plan."},
    {"id": "huggingface", "name": "Hugging Face", "used_for": "Free GPU demos (Wan2.2 video) and model downloads."},
    {"id": "github", "name": "GitHub", "used_for": "Sharing and updating the project's code."},
]


def _run(command: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def _cli(name: str) -> str | None:
    if name == "claude":
        from core.gag_director import claude_cli
        return claude_cli()
    for candidate in (shutil.which(name), f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}"):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def status(connector: str) -> dict[str, Any]:
    """{"installed", "signed_in", "account", "plan", "detail"} — what can be read without asking anything."""
    if connector == "claude":
        cli = _cli("claude")
        if not cli:
            return {"installed": False, "signed_in": False, "detail": "Claude Code is not installed on this computer."}
        result = _run([cli, "auth", "status"])
        try:
            data = json.loads(result.stdout if result else "{}")
        except ValueError:
            data = {}
        return {"installed": True, "signed_in": bool(data.get("loggedIn")), "account": data.get("email") or "",
                "plan": str(data.get("subscriptionType") or "").title()}
    if connector == "chatgpt":
        cli = _cli("codex")
        if not cli:
            return {"installed": False, "signed_in": False, "detail": "Codex is not installed on this computer."}
        result = _run([cli, "login", "status"])
        text = ((result.stdout or "") + (result.stderr or "")).strip() if result else ""
        signed = bool(result) and result.returncode == 0 and "not logged in" not in text.lower()
        return {"installed": True, "signed_in": signed, "account": "", "detail": text.splitlines()[0] if text else ""}
    if connector == "huggingface":
        try:
            from huggingface_hub import get_token, whoami
            token = get_token()
            if not token:
                return {"installed": True, "signed_in": False}
            me = whoami(token=token)
            return {"installed": True, "signed_in": True, "account": me.get("name") or "", "plan": "Pro" if me.get("isPro") else "Free"}
        except Exception as exc:  # offline, an expired token…
            return {"installed": True, "signed_in": False, "detail": str(exc)[:200]}
    if connector == "github":
        cli = _cli("gh")
        if not cli:
            return {"installed": False, "signed_in": False, "detail": "The GitHub command-line tool is not installed."}
        result = _run([cli, "auth", "status"])
        text = ((result.stdout or "") + (result.stderr or "")) if result else ""
        account = next((line.split(" account ", 1)[1].split()[0] for line in text.splitlines() if " account " in line and "Logged in" in line), "")
        return {"installed": True, "signed_in": bool(account), "account": account}
    raise KeyError(connector)


def all_status() -> list[dict[str, Any]]:
    return [{**entry, **status(entry["id"])} for entry in CONNECTORS]


SIGN_IN = {
    "claude": ("{cli} auth login --claudeai", "{cli} auth logout; {cli} auth login --claudeai"),
    "chatgpt": ("{cli} login", "{cli} logout; {cli} login"),
    "github": ("{cli} auth login", "{cli} auth login; {cli} auth switch"),
}


def open_sign_in(connector: str, switch: bool = False) -> str:
    """A Terminal window running the account's own sign-in; the person signs in there."""
    if connector not in SIGN_IN:
        raise KeyError(connector)
    cli = _cli({"claude": "claude", "chatgpt": "codex", "github": "gh"}[connector])
    if not cli:
        raise RuntimeError(status(connector).get("detail") or "Not installed")
    command = SIGN_IN[connector][1 if switch else 0].format(cli=cli)
    if sys.platform == "darwin":
        script = f'tell application "Terminal" to do script "{command}"\ntell application "Terminal" to activate'
        subprocess.run(["osascript", "-e", script], check=True, timeout=20)
    elif sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", command.replace("; ", " & ")])
    else:
        raise RuntimeError(f"Open a terminal and run: {command}")
    return command


def hugging_face_sign_in(token: str) -> dict[str, Any]:
    """The token the person pasted, checked with Hugging Face and kept by huggingface_hub."""
    from huggingface_hub import login, whoami
    token = str(token or "").strip()
    if not token.startswith("hf_") or len(token) > 200:
        raise ValueError("Paste a Hugging Face token (it starts with hf_)")
    me = whoami(token=token)                                      # refused tokens raise here
    login(token=token, add_to_git_credential=False)
    return {"signed_in": True, "account": me.get("name") or ""}


def hugging_face_sign_out() -> None:
    from huggingface_hub import logout
    logout()
