"""Jingles for the Show: found by what they sound like, placed at any moment.

Search goes to Openverse (openly licensed audio gathered from Freesound, Jamendo,
ccMixter and others; no account needed). Only CC0 (public domain) by default;
CC BY on request, its credit kept for the video's description. Nothing is
downloaded until the person chooses a sound, and each one keeps the page where
its licence can be checked: Openverse does not verify licences. The person's own
"Music" folder in the library is offered too (their own jingles, later).

A jingle is laid over the show's sound at its moment, trimmed and faded, and the
voice dips under it while it plays.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OPENVERSE = "https://api.openverse.org/v1/audio/"
OPENVERSE_NOTE = "Sounds found with Openverse (openverse.org), which does not endorse Zucker Editor."
LICENSES = ("cc0", "by")                     # reusable in a monetised video; "by" needs a credit
USER_AGENT = "ZuckerEditor (Show jingles)"
AUDIO_SUFFIXES = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".aif", ".aiff")
MAX_BYTES = 40 * 1024 * 1024
JINGLE_SEC = 6.0                             # a long track is cut to its first seconds unless changed


def search_result(raw: dict[str, Any]) -> dict[str, Any]:
    """An Openverse audio result, as the review shows it."""
    return {"id": str(raw.get("id") or ""), "title": str(raw.get("title") or "untitled"), "creator": str(raw.get("creator") or ""),
            "license": str(raw.get("license") or ""), "license_version": str(raw.get("license_version") or ""),
            "license_url": str(raw.get("license_url") or ""), "source": str(raw.get("source") or raw.get("provider") or ""),
            "page": str(raw.get("foreign_landing_url") or ""), "url": str(raw.get("url") or ""),
            "duration": round(float(raw.get("duration") or 0) / 1000, 2), "filetype": str(raw.get("filetype") or ""),
            "attribution": str(raw.get("attribution") or "")}


def search(query: str, *, licenses: tuple[str, ...] = ("cc0",), page_size: int = 20, timeout: float = 15) -> list[dict[str, Any]]:
    """Sounds that match what the person asks for ("funny jingle", "drum roll"...)."""
    wanted = [name for name in licenses if name in LICENSES] or ["cc0"]
    params = urlencode({"q": query.strip()[:120], "license": ",".join(wanted), "page_size": max(1, min(50, page_size)), "mature": "false"})
    with urlopen(Request(f"{OPENVERSE}?{params}", headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
        data = json.load(response)
    return [search_result(raw) for raw in data.get("results") or [] if raw.get("license") in wanted and raw.get("url")]


def library_sounds(folder: Path) -> list[dict[str, Any]]:
    """The person's own music and jingles (the library's "Music" folder)."""
    if not folder.is_dir():
        return []
    return [{"id": f"own:{path.name}", "title": path.stem, "creator": "you", "license": "own", "source": "your library",
             "page": "", "url": "", "path": str(path), "duration": 0.0}
            for path in sorted(folder.iterdir()) if path.suffix.lower() in AUDIO_SUFFIXES]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "sound"


def fetch(item: dict[str, Any], folder: Path, *, ffmpeg: str, timeout: float = 60) -> dict[str, Any]:
    """The chosen sound, downloaded once into the project with its licence and source page."""
    if item.get("license") not in LICENSES:
        raise ValueError("Only public-domain (CC0) or CC BY sounds can go in the show")
    url = str(item.get("url") or "")
    if not url.startswith("https://"):
        raise ValueError("That sound has no download address")
    folder.mkdir(parents=True, exist_ok=True)
    listing = folder / "sounds.json"
    known = json.loads(listing.read_text(encoding="utf-8")) if listing.is_file() else []
    kept = next((entry for entry in known if entry.get("id") == item.get("id") and Path(entry.get("path", "")).is_file()), None)
    if kept:
        return kept
    suffix = Path(url.split("?")[0]).suffix.lower()
    suffix = suffix if suffix in AUDIO_SUFFIXES else f".{item.get('filetype') or 'mp3'}"
    target = folder / f"{_slug(item.get('title', ''))}-{_slug(str(item.get('id', '')))[:8]}{suffix}"
    partial = target.with_name(target.name + ".part")
    received = 0
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response, partial.open("wb") as output:
        while block := response.read(1 << 16):
            received += len(block)
            if received > MAX_BYTES:
                output.close()
                partial.unlink(missing_ok=True)
                raise ValueError("That sound is too big for a jingle")
            output.write(block)
    partial.replace(target)
    from core.ffmpeg import ffprobe
    try:
        duration = float(ffprobe(str(target))["format"]["duration"])
    except Exception as exc:  # not audio after all
        target.unlink(missing_ok=True)
        raise ValueError("That download is not a sound") from exc
    record = {**item, "path": str(target), "duration": round(duration, 2)}
    listing.write_text(json.dumps([*known, record], indent=1, ensure_ascii=False), encoding="utf-8")
    return record


def credits(sounds: list[dict[str, Any]]) -> str:
    """Lines for the video's description: CC BY sounds must be credited; CC0 need nothing."""
    lines = []
    for sound in sounds:
        if sound.get("license") != "by":
            continue
        lines.append(sound.get("attribution") or f'"{sound.get("title")}" by {sound.get("creator")} '
                     f'(CC BY {sound.get("license_version", "")}, {sound.get("license_url", "")})'.replace("  ", " "))
    if not lines:
        return ""
    return "Music and sounds:\n" + "\n".join(f"- {line}" for line in lines) + f"\n{OPENVERSE_NOTE}"


def _between(windows: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in windows)


def mix(video: Path, jingles: list[dict[str, Any]], output: Path, *, ffmpeg: str) -> Path:
    """The video with each jingle laid over its sound at ``at`` seconds: trimmed from
    ``start`` for ``duration``, faded in and out, at ``volume``; the rest of the
    sound dips to ``duck`` while it plays. The picture is copied as it is."""
    jingles = [item for item in jingles if Path(item["jingle"]).is_file() and float(item["duration"]) > 0]
    if not jingles:
        raise ValueError("No jingle to mix")
    inputs = ["-i", str(video)]
    chains = []
    ducks = []
    for index, item in enumerate(jingles, start=1):
        at, length = max(0.0, float(item["at"])), float(item["duration"])
        start = max(0.0, float(item.get("start") or 0))
        fade = max(0.01, min(float(item.get("fade") or 0.05), length / 2))
        delay = int(round(at * 1000))
        inputs += ["-i", str(item["jingle"])]
        chains.append(f"[{index}:a]atrim=start={start:.3f}:duration={length:.3f},asetpts=PTS-STARTPTS,"
                      f"aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:d={fade:.3f},"
                      f"afade=t=out:st={max(0.0, length - fade):.3f}:d={fade:.3f},volume={float(item.get('volume') if item.get('volume') is not None else 0.8):.3f},"
                      f"adelay={delay}|{delay}[j{index}]")
        ducks.append((at, at + length, float(item.get("duck") if item.get("duck") is not None else 0.5)))
    host = "[0:a]aformat=sample_rates=48000:channel_layouts=stereo"
    for level in sorted({duck for _, _, duck in ducks}):
        windows = [(a, b) for a, b, duck in ducks if duck == level]
        host += f",volume=volume={level:.3f}:enable='{_between(windows)}'"
    under=[index for index,item in enumerate(jingles,start=1) if item.get('under_voice')]
    split=host+(f",asplit={len(under)+1}[host]"+''.join(f'[sc{i}]' for i in under) if under else '[host]')
    side=[f'[j{i}][sc{i}]sidechaincompress=threshold=0.025:ratio=6:attack=20:release=250[d{i}]' for i in under]
    labels=''.join(f'[d{i}]' if i in under else f'[j{i}]' for i in range(1,len(jingles)+1))
    safe=',alimiter=limit=0.95:level=0:latency=1' if under else ''
    graph=';'.join([split,*chains,*side,f'[host]{labels}amix=inputs={len(jingles)+1}:normalize=0:duration=first{safe}[mixed]'])
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([ffmpeg, "-y", "-v", "error", "-nostdin", *inputs, "-filter_complex", graph, "-map", "0:v", "-map", "[mixed]",
                             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("Mixing the jingles failed: " + result.stderr[-800:])
    return output
