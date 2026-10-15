"""Einstein: pictures for the Show drawn by the person's own ChatGPT.

The Codex CLI, signed in with the person's ChatGPT account (no API key, no paid
API), is asked to draw one picture per request with its image tool. A chat is
one Codex session, so "make him lazier" changes the same drawing. Every reply
and picture is kept in the project (cache/image_chat/<id>/); a picture the
person accepts is copied into their gag library, where the storyboard places it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

STYLE = ("a rough, funny hand-drawn doodle, as if an adult drew it quickly with wax crayons, pencil and a felt-tip marker: "
         "wobbly incomplete outlines, uneven scribbled colouring with gaps, silly and expressive. Transparent background: "
         "no paper, no table, no shadow, no frame. No sticker look, no white border, no 3D shine, no text unless asked")
FIRST = ("You are Einstein, the drawing helper inside Zucker Editor, a video editor. Whenever the person asks for a picture, "
         "use your image generation tool to create exactly one image: {style}. Then answer with one short, friendly sentence "
         "in the person's language. Do not run commands and do not read or write files.\n\nThe person asks: {message}")
NEXT = ("The person says: {message}\n(Create exactly one new image in the same style when they ask for a picture or a change, "
        "then answer with one short sentence in their language.)")
TIMEOUT = 360
EXTRA_PATHS = ("/usr/local/bin", "/opt/homebrew/bin", str(Path.home() / ".local/bin"), str(Path.home() / ".npm-global/bin"))
CHAT_ID = re.compile(r"^[0-9a-f]{12}$")


def _environment() -> dict[str, str]:
    """A PATH that finds codex and node also when the app is opened from the Finder."""
    env = dict(os.environ)
    parts = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join(parts + [p for p in EXTRA_PATHS if p not in parts])
    return env


def codex_binary() -> str | None:
    return shutil.which("codex", path=_environment()["PATH"])


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def status() -> dict[str, Any]:
    """Whether Einstein can draw: the Codex CLI is installed and signed in with ChatGPT."""
    binary = codex_binary()
    if not binary:
        return {"ready": False, "detail": "Install the Codex CLI and sign in with your ChatGPT account to let Einstein draw."}
    try:
        answer = subprocess.run([binary, "login", "status"], capture_output=True, text=True, timeout=20, env=_environment())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ready": False, "detail": f"The Codex CLI did not answer: {exc}"}
    said = (answer.stdout + answer.stderr).strip()
    if "chatgpt" not in said.lower():
        return {"ready": False, "detail": "Sign in to the Codex CLI with your ChatGPT account (codex login) to let Einstein draw."}
    return {"ready": True, "detail": said.splitlines()[0] if said else "Signed in"}


def painter_ready() -> bool:
    try:
        from core import gag_paint
        return bool(gag_paint.can_paint())
    except Exception:  # an unreadable painter install is reported as not ready
        return False


def plain_failure(message: str) -> str:
    """Codex's own refusal, said plainly (a usage limit names when it resets)."""
    if "usage limit" in message.lower():
        until = re.search(r"try again at ([^.]+(?:\.\d+)?[^.]*)", message)
        return ("your ChatGPT plan has used up its Codex allowance for now"
                + (f"; it is available again at {until.group(1).strip().rstrip('.')}" if until else "")
                + ". Meanwhile you can choose “This computer” to paint it here")
    return message[:300]


class ImageChat:
    """One conversation with Einstein, kept in ``folder``."""

    _lock = threading.Lock()

    def __init__(self, folder: Path, chat_id: str | None = None):
        if chat_id is not None and not CHAT_ID.match(chat_id):
            raise ValueError("Unknown chat")
        self.id = chat_id or uuid.uuid4().hex[:12]
        self.folder = Path(folder) / self.id
        self.record = self.folder / "chat.json"
        if chat_id is not None and not self.record.is_file():
            raise ValueError("Unknown chat")

    def load(self) -> dict[str, Any]:
        if not self.record.is_file():
            return {"id": self.id, "thread": None, "messages": [], "busy": False, "error": None}
        data = json.loads(self.record.read_text(encoding="utf-8"))
        asked = [m for m in data["messages"] if m["role"] == "person"]
        if data.get("busy") and asked and asked[-1]["at"] < time.time() - TIMEOUT - 60:
            data.update(busy=False, error="The last drawing was interrupted; ask again.")   # the app closed while it drew
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        pending = self.record.with_suffix(".part")
        pending.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        pending.replace(self.record)

    def image(self, name: str) -> Path:
        path = self.folder / name
        if not re.match(r"^image-\d{1,3}\.png$", name) or not path.is_file():
            raise ValueError("No such picture in this chat")
        return path

    def send(self, message: str, *, context: str = "", engine: str = "chatgpt", run=None) -> dict[str, Any]:
        """The person's message; Einstein answers in the background (``busy`` until then).
        ``engine``: "chatgpt" (their ChatGPT through the Codex CLI) or "local" (this
        computer's own painter, free and slower), as the person chooses."""
        message = message.strip()[:2000]
        if not message:
            raise ValueError("Say what to draw")
        if engine not in ("chatgpt", "local"):
            raise ValueError("Choose ChatGPT or this computer's painter")
        with self._lock:
            data = self.load()
            if data.get("busy"):
                raise ValueError("Einstein is still drawing the last picture")
            data["messages"].append({"role": "person", "text": message, "at": time.time(), "engine": engine})
            data.update(busy=True, error=None)
            if context and not data.get("context"):
                data["context"] = context[:400]
            self._save(data)
        answer = run or (self._answer if engine == "chatgpt" else self._paint)
        thread = threading.Thread(target=answer, args=(message,), daemon=True, name=f"einstein-{self.id}")
        thread.start()
        return self.load()

    def _answer(self, message: str) -> None:
        data = self.load()
        binary = codex_binary()
        try:
            if not binary:
                raise RuntimeError("The Codex CLI is not installed")
            work = self.folder / "work"
            work.mkdir(parents=True, exist_ok=True)
            asked = message if not data.get("context") else f"{message}\n(It goes over this moment of the video: “{data['context']}”.)"
            if data.get("thread"):
                command = [binary, "exec", "resume", data["thread"], "--json", "--skip-git-repo-check",
                           "-c", 'sandbox_mode="read-only"', NEXT.format(message=asked)]
            else:
                command = [binary, "exec", "--json", "--skip-git-repo-check", "-s", "read-only", "-C", str(work),
                           FIRST.format(style=STYLE, message=asked)]
            started = time.time()
            done = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT, env=_environment(), cwd=str(work),
                                  stdin=subprocess.DEVNULL)
            thread, words, failure = data.get("thread"), [], ""
            for line in done.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "thread.started":
                    thread = event.get("thread_id") or thread
                if event.get("type") in ("error", "turn.failed"):
                    failure = event.get("message") or (event.get("error") or {}).get("message") or failure
                item = event.get("item") or {}
                if event.get("type") == "item.completed" and item.get("type") == "agent_message" and item.get("text"):
                    words.append(item["text"].strip())
            images = []
            made = codex_home() / "generated_images" / str(thread or "")
            taken = set(data.get("taken") or [])                                  # pictures of earlier answers
            if thread and made.is_dir():
                fresh = sorted((p for p in made.glob("*.png") if p.name not in taken and p.stat().st_mtime >= started - 1),
                               key=lambda p: p.stat().st_mtime)
                count = sum(len(m.get("images") or []) for m in data["messages"])
                for path in fresh:
                    count += 1
                    target = self.folder / f"image-{count}.png"
                    shutil.copy2(path, target)
                    images.append(target.name)
                    taken.add(path.name)
            if failure and not images:
                raise RuntimeError(plain_failure(failure))
            if done.returncode != 0 and not images:
                said = [line for line in (done.stderr or "").strip().splitlines() if "additional input from stdin" not in line]
                raise RuntimeError(said[-1][:300] if said else "Codex stopped without an answer")
            with self._lock:
                data = self.load()
                data["thread"] = thread
                data["taken"] = sorted(taken)
                data["messages"].append({"role": "einstein", "text": " ".join(w for w in words if w) or
                                         ("Here it is." if images else "I could not draw it this time."), "images": images, "at": time.time()})
                data.update(busy=False, error=None if images else "No picture came back; try saying it another way.")
                self._save(data)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            with self._lock:
                data = self.load()
                data.update(busy=False, error=f"Einstein could not draw: {exc}")
                self._save(data)

    def _paint(self, message: str) -> None:
        """This computer's own crayon painter (free, slower), when the person picks it."""
        try:
            from core import gag_paint, gags
            if not gag_paint.can_paint():
                raise RuntimeError("this computer's painter is not installed yet (a 2.1 GB download, once, from the Show settings)")
            data = self.load()
            count = sum(len(m.get("images") or []) for m in data["messages"])
            picture = gags.painted_picture(message[:80], "crayon", seed=3 + count)
            target = self.folder / f"image-{count + 1}.png"
            picture.save(target)
            with self._lock:
                data = self.load()
                data["messages"].append({"role": "einstein", "text": "Painted on this computer.", "images": [target.name], "at": time.time()})
                data.update(busy=False, error=None)
                self._save(data)
        except Exception as exc:  # the painter's own errors are said in the chat, never hidden
            with self._lock:
                data = self.load()
                data.update(busy=False, error=f"This computer's painter could not draw it: {exc}")
                self._save(data)

    def accept(self, name: str, library: Path, title: str) -> Path:
        """Keep a picture in the person's library (Stickers), named after what it shows."""
        source = self.image(name)
        words = re.sub(r"[^\w -]+", "", title, flags=re.UNICODE).strip()[:60] or "Einstein drawing"
        folder = Path(library) / "Stickers"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{words}.png"
        n = 2
        while target.exists():
            target = folder / f"{words} {n}.png"
            n += 1
        shutil.copy2(source, target)
        return target.resolve()
