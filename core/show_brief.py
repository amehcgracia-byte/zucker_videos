"""The person's own idea for their show, asked before anything is planned.

Written, pasted, imported from a document or dictated, it is kept in the project
(artifacts/show_brief.json) and handed to the director first: the person's ideas go where
the talk matches them, as they describe them, then the director adds its own around them.
"automatic" plans without one, as before.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

MODES = ("together", "automatic")
MAX_CHARS = 8000
DOCUMENTS = (".txt", ".md", ".srt", ".rtf", ".docx")


def path(project) -> Path:
    return project.artifacts_dir / "show_brief.json"


def load(project) -> dict[str, Any]:
    try:
        data = json.loads(path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"mode": "automatic", "idea": ""}
    return {"mode": data.get("mode") if data.get("mode") in MODES else "automatic", "idea": str(data.get("idea") or "")[:MAX_CHARS],
            "source": data.get("source") or "", "updated": data.get("updated") or ""}


def save(project, mode: str, idea: str, source: str = "typed") -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError("Choose “With my idea” or “Fully automatic”")
    idea = re.sub(r"\n{3,}", "\n\n", str(idea or "").replace("\r\n", "\n")).strip()[:MAX_CHARS]
    if mode == "together" and len(idea) < 3:
        raise ValueError("Tell us your idea first (write, paste, import or dictate it), or choose “Fully automatic”")
    brief = {"mode": mode, "idea": idea if mode == "together" else "", "source": source,
             "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path(project).parent.mkdir(parents=True, exist_ok=True)
    pending = path(project).with_suffix(".pending")
    pending.write_text(json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
    pending.replace(path(project))
    return brief


def idea_for_director(project) -> str:
    """The idea the director must follow, or "" when the show is fully automatic."""
    brief = load(project)
    return brief["idea"] if brief["mode"] == "together" else ""


def document_text(filename: str, payload: bytes) -> str:
    """The text of a script the person already wrote (plain text, Markdown, subtitles, RTF, Word)."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in DOCUMENTS:
        raise ValueError("Import a .txt, .md, .docx, .rtf or .srt document (save a PDF as text or Word first)")
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                xml = archive.read("word/document.xml").decode("utf-8", "replace")
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ValueError("That Word document could not be read") from exc
        paragraphs = [" ".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para)) for para in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)]
        text = "\n".join(paragraph for paragraph in paragraphs if paragraph.strip())
        return _unescape(text)[:MAX_CHARS]
    for encoding in ("utf-8", "latin-1"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if suffix == ".rtf":
        text = re.sub(r"\\par[d]?", "\n", text)
        text = re.sub(r"\\'[0-9a-f]{2}|\\[a-z]+-?\d* ?|[{}]", "", text)
    if suffix == ".srt":   # the words only, without numbers and times
        text = "\n".join(line for line in text.splitlines() if line.strip() and not line.strip().isdigit() and "-->" not in line)
    return text.strip()[:MAX_CHARS]


def _unescape(text: str) -> str:
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")


def dictate(audio: Path, ffmpeg: str) -> str:
    """What the person said into the microphone, as text (local Whisper; nothing is uploaded)."""
    wav = audio.with_suffix(".idea.wav")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", "-t", "600", str(wav)],
                   capture_output=True, check=True, timeout=120)
    try:
        from faster_whisper import WhisperModel
        from core.storage import whisper_model_path
        model = WhisperModel(whisper_model_path("small"), device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(wav), vad_filter=True, beam_size=5)
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip())[:MAX_CHARS]
    finally:
        wav.unlink(missing_ok=True)
