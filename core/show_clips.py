"""Video clips from a link: only the chosen seconds are downloaded into the project.

A saved video reference (core.show_references.video_reference: a YouTube/Vimeo link with
its start and end) becomes footage on request: yt-dlp fetches just that interval, ffmpeg
evens it out (H.264 at 30 fps, stereo AAC) and it is kept in assets/clips/. In the show it
plays whole with its own sound while the host pauses (core.show_inserts.load_clip).
The clip belongs to whoever made it: the person decides whether they may publish it.
"""
from __future__ import annotations

import shutil
import json
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "vimeo.com", "www.vimeo.com", "dailymotion.com", "www.dailymotion.com", "twitch.tv", "www.twitch.tv", "clips.twitch.tv", "www.tiktok.com", "www.instagram.com", "www.reddit.com", "archive.org", "www.pexels.com", "pixabay.com"}
MAX_SECONDS = 30.0


def check_link(url: str) -> str:
    parts = urllib.parse.urlsplit(str(url or "").strip())
    if parts.scheme != "https" or parts.hostname not in HOSTS or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("Use an HTTPS link from YouTube, Vimeo, Dailymotion, Twitch, TikTok, Instagram, Reddit, Archive.org, Pexels or Pixabay")
    return parts.geturl()


def folder(project) -> Path:
    return project.folder / "assets" / "clips"


def browser_setting():
    import json
    from core.storage import data_root
    path=data_root()/'download_access.json'
    try:return json.loads(path.read_text()).get('browser') or None
    except (OSError,ValueError):return None


def set_browser(browser):
    from core.storage import data_root
    from core.project import atomic_write_json
    if browser not in (None,'','chrome','safari','firefox','edge','brave'):raise ValueError('Choose a supported browser or no browser session')
    atomic_write_json(data_root()/'download_access.json',{'browser':browser or None})


def download_error(message):
    lower=message.lower()
    if any(word in lower for word in ("not a bot","confirm you're",'confirm you’re','sign in','cookies','http error 429')):
        return ('YouTube needs a browser verification, not more credits. Open this link in your browser, sign in or complete its check, '
                'then select that browser under Browser session and retry. Its current session is read locally on each download; '
                'Zucker cannot solve a CAPTCHA or renew a signed-out account automatically.')
    return 'Could not download that clip. Check that the link plays in your browser and retry. '+message.replace('ERROR: ','')[:180]


def media_title(path: Path, fallback: str = "") -> str:
    """Read only the small, curated metadata kept beside a downloaded asset."""
    try:
        return str(json.loads(path.with_suffix(path.suffix + ".json").read_text()).get("title") or fallback)
    except (OSError, ValueError, TypeError):
        return fallback


def _info_title(work: Path) -> str:
    for info in work.glob("*.info.json"):
        try:
            title = str(json.loads(info.read_text()).get("title") or "").strip()
        except (OSError, ValueError, TypeError):
            continue
        if title:
            return title
    return ""


def _save_title(work: Path, target: Path, url: str) -> None:
    for info in work.glob("*.info.json"):
        try:
            title = str(json.loads(info.read_text()).get("title") or "").strip()
        except (OSError, ValueError, TypeError):
            continue
        if title:
            target.with_suffix(target.suffix + ".json").write_text(json.dumps({"title": title, "source_url": url}, ensure_ascii=False))
            break


def video_key(url: str) -> str:
    """The same online video under its different links (watch?v=, shorts/, youtu.be/, extra
    parameters) has one key."""
    from urllib.parse import parse_qs, urlparse
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return str(url or "")
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    if host in ("youtube.com", "music.youtube.com"):
        video = parse_qs(parsed.query).get("v", [""])[0] or next((part for part in parsed.path.split("/")[2:3] if parsed.path.startswith(("/shorts/", "/embed/", "/live/"))), "")
        if video:
            return f"youtube:{video}"
    if host == "youtu.be" and parsed.path.strip("/"):
        return f"youtube:{parsed.path.strip('/').split('/')[0]}"
    return f"{host}{parsed.path.rstrip('/')}"


def fetch_title(url: str) -> str:
    """The online video's title (metadata only: nothing is downloaded)."""
    from yt_dlp import YoutubeDL
    options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "socket_timeout": 15, "retries": 1}
    browser = browser_setting()
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(check_link(url), download=False) or {}
    return str(info.get("title") or "").strip()


def name_downloads(files: dict[str, list[str]]) -> int:
    """Titles for downloads saved before titles were kept: ``{url: [file, …]}``. Each title is
    written beside its files (``<file>.json``); returns how many were found."""
    found = 0
    for url, paths in files.items():
        try:
            title = fetch_title(url)
        except Exception:  # offline, removed video, a site that wants a sign-in…
            continue
        if not title:
            continue
        for path in paths:
            sidecar = Path(str(path) + ".json")
            try:
                data = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
            except (OSError, ValueError):
                data = {}
            if not data.get("title"):
                sidecar.write_text(json.dumps({**data, "title": title, "source_url": data.get("source_url") or url}, ensure_ascii=False))
        found += 1
    return found


def reuse(project, reference: dict[str, Any]) -> tuple[Path, float] | None:
    """A download of the same video that already covers the chosen seconds, and where they
    start in it: the same video is not downloaded again for another moment inside it."""
    from core import show_video_assets
    key, start, end = video_key(reference.get("url") or ""), float(reference["start"]), float(reference["end"])
    best = None
    for record in show_video_assets.catalog(project).values():
        params = record.get("params") or {}
        path = Path(str(record.get("path") or ""))
        if video_key(record.get("url") or params.get("url") or "") != key or record.get("face_swapped") or not path.is_file():
            continue
        begins = float(params.get("source_start") or 0.0)
        length = float(params.get("clip_length") or record.get("duration") or 0.0)
        if begins - 0.05 <= start and end <= begins + length + 0.05:
            best = best or (path, round(max(0.0, start - begins), 3))
    return best


def download(project, reference: dict[str, Any], ffmpeg: str) -> Path:
    """The reference's interval as a file in the project (made once; the same one is reused)."""
    ffmpeg=shutil.which(ffmpeg) or ffmpeg
    url = check_link(reference.get("url"))
    start, end = float(reference["start"]), float(reference["end"])
    if not 0 <= start < end:
        raise ValueError("Choose an end after the start (seconds)")
    if end - start > MAX_SECONDS:
        raise ValueError(f"Download at most {MAX_SECONDS:.0f} seconds of a clip")
    from core.media_names import find_coded, unique_file
    made = find_coded(folder(project), str(reference["id"]), ".mp4")   # made once (old "clip-<id>" names too)
    if made is not None:
        return made
    target = folder(project) / f"clip-{reference['id']}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    work = target.parent / f".{target.stem}.part"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError, download_range_func
        options = {"writeinfojson": True, "quiet": True, "no_warnings": True, "socket_timeout":20, "retries":2, "fragment_retries":2, "noplaylist": True, "noprogress": True,
                   "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b",
                   "merge_output_format": "mp4", "download_ranges": download_range_func(None, [(start, end)]),
                   "force_keyframes_at_cuts": True, "ffmpeg_location": ffmpeg, "max_filesize": 400 * 1024 * 1024,
                   "outtmpl": str(work / "source.%(ext)s")}
        browser=browser_setting()
        if browser:options["cookiesfrombrowser"]=(browser,)
        try:
            with YoutubeDL(options) as ydl:
                ydl.download([url])
        except DownloadError as exc:
            raise RuntimeError(download_error(str(exc))) from exc
        fetched = next((path for path in work.iterdir() if path.is_file() and path.suffix in (".mp4", ".mkv", ".webm", ".mov")), None)
        if fetched is None:
            raise RuntimeError("The download finished without a video file")
        evened = work / "clip.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(fetched), "-map", "0:v:0", "-map", "0:a:0?",
                        "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
                        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(evened)],
                       check=True, capture_output=True, timeout=180)
        title = _info_title(work)
        if title:                                           # named after the video, the code keeps it unique
            target = unique_file(target.parent, title, str(reference["id"]), ".mp4")
        evened.replace(target)
        _save_title(work, target, url)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target


def length(path: Path, ffprobe: str) -> float:
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return round(float(out.strip()), 3)


def download_audio(project,reference,ffmpeg):
    """Download only the chosen interval's audio; persist MP3, not the source video."""
    url=check_link(reference.get('url'));start,end=float(reference['start']),float(reference['end'])
    if not 0<=start<end or end-start>MAX_SECONDS:raise ValueError('Choose up to 30 seconds after the source start')
    from core.media_names import find_coded
    made=find_coded(folder(project),str(reference['id']),'.mp3')
    if made is not None:return made
    target=folder(project)/f"audio-{reference['id']}.mp3"
    target.parent.mkdir(parents=True,exist_ok=True)
    import tempfile
    with tempfile.TemporaryDirectory(prefix='.audio-download-',dir=target.parent) as temp:
        work=Path(temp)
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError,download_range_func
        options={'writeinfojson':True,'quiet':True,'no_warnings':True,'noplaylist':True,'noprogress':True,'socket_timeout':20,'retries':2,'fragment_retries':2,
                 'format':'bestaudio/best','download_ranges':download_range_func(None,[(start,end)]),'ffmpeg_location':ffmpeg,
                 'max_filesize':100*1024*1024,'outtmpl':str(work/'source.%(ext)s')}
        browser=browser_setting()
        if browser:options['cookiesfrombrowser']=(browser,)
        try:
            with YoutubeDL(options) as downloader:downloader.download([url])
        except DownloadError as exc:raise RuntimeError(download_error(str(exc))) from exc
        source=next((p for p in work.iterdir() if p.is_file() and p.suffix.lower() in {'.webm','.m4a','.mp3','.ogg','.opus','.mp4','.mkv'}),None)
        if source is None:raise ValueError('The download returned no playable audio')
        from core.audio_import import mp3
        mp3(source,work/'ready.mp3',ffmpeg)
        title=_info_title(work)
        if title:
            from core.media_names import unique_file
            target=unique_file(target.parent,title,str(reference['id']),'.mp3')
        (work/'ready.mp3').replace(target)
        _save_title(work, target, url)
    return target
