"""Video clips from a link: only the chosen seconds are downloaded into the project.

A saved video reference (core.show_references.video_reference: a YouTube/Vimeo link with
its start and end) becomes footage on request: yt-dlp fetches just that interval, ffmpeg
evens it out (H.264 at 30 fps, stereo AAC) and it is kept in assets/clips/. In the show it
plays whole with its own sound while the host pauses (core.show_inserts.load_clip).
The clip belongs to whoever made it: the person decides whether they may publish it.
"""
from __future__ import annotations

import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "vimeo.com", "www.vimeo.com"}
MAX_SECONDS = 30.0


def check_link(url: str) -> str:
    parts = urllib.parse.urlsplit(str(url or "").strip())
    if parts.scheme != "https" or parts.hostname not in HOSTS or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("Use an HTTPS YouTube or Vimeo link")
    return parts.geturl()


def folder(project) -> Path:
    return project.folder / "assets" / "clips"


def download(project, reference: dict[str, Any], ffmpeg: str) -> Path:
    """The reference's interval as a file in the project (made once; the same one is reused)."""
    url = check_link(reference.get("url"))
    start, end = float(reference["start"]), float(reference["end"])
    if not 0 <= start < end:
        raise ValueError("Choose an end after the start (seconds)")
    if end - start > MAX_SECONDS:
        raise ValueError(f"Download at most {MAX_SECONDS:.0f} seconds of a clip")
    target = folder(project) / f"clip-{reference['id']}.mp4"
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    work = target.parent / f".{target.stem}.part"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError, download_range_func
        options = {"quiet": True, "no_warnings": True, "noplaylist": True, "noprogress": True,
                   "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b",
                   "merge_output_format": "mp4", "download_ranges": download_range_func(None, [(start, end)]),
                   "force_keyframes_at_cuts": True, "ffmpeg_location": ffmpeg, "max_filesize": 400 * 1024 * 1024,
                   "outtmpl": str(work / "source.%(ext)s")}
        try:
            with YoutubeDL(options) as ydl:
                ydl.download([url])
        except DownloadError as exc:
            raise RuntimeError(f"Could not download that clip: {str(exc).replace('ERROR: ', '')[:300]}") from exc
        fetched = next((path for path in work.iterdir() if path.is_file() and path.suffix in (".mp4", ".mkv", ".webm", ".mov")), None)
        if fetched is None:
            raise RuntimeError("The download finished without a video file")
        evened = work / "clip.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(fetched), "-map", "0:v:0", "-map", "0:a:0?",
                        "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
                        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(evened)],
                       check=True, capture_output=True)
        evened.replace(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target


def length(path: Path, ffprobe: str) -> float:
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return round(float(out.strip()), 3)
