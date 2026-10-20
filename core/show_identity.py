"""The Show's opening: a title card (optional logo) and an optional intro video,
joined in front of the edited show with matching encoding.

The card is drawn here (hand lettering, the logo as it is: never redrawn) and
silent; the intro keeps its own sound (silence is added if it has none). All
pieces are encoded alike (H.264 yuv420p, 30 fps, AAC 48 kHz stereo) so they join
without re-encoding the show.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from core.gag_stickers import hand_font

FPS = 30
VIDEO_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS)]
AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def card_picture(title: str, logo: str | Path | None, width: int = 1920, height: int = 1080) -> Image.Image:
    canvas = Image.new("RGB", (width, height), (16, 18, 20))
    draw = ImageDraw.Draw(canvas)
    top = height // 2
    if logo and Path(logo).is_file():
        with Image.open(logo) as source:
            mark = source.convert("RGBA")
        mark.thumbnail((int(width * 0.32), int(height * 0.38)), Image.LANCZOS)
        position = ((width - mark.width) // 2, int(height * 0.44) - mark.height)
        canvas.paste(mark, position, mark)
        top = int(height * 0.5)
    if title.strip():
        font = hand_font(int(height * 0.09))
        box = draw.textbbox((0, 0), title.strip(), font=font, stroke_width=6)
        x = (width - (box[2] - box[0])) // 2 - box[0]
        draw.text((x, top + (0 if logo else -(box[3] - box[1]) // 2)), title.strip(), font=font, fill=(250, 246, 235),
                  stroke_width=6, stroke_fill=(0, 0, 0))
    return canvas


def render_card(title: str, logo: str | Path | None, output: Path, *, seconds: float = 2.0, ffmpeg: str = "ffmpeg",
                width: int = 1920, height: int = 1080) -> Path:
    with tempfile.TemporaryDirectory() as scratch:
        picture = Path(scratch) / "card.png"
        card_picture(title, logo, width, height).save(picture)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-loop", "1", "-framerate", str(FPS), "-i", str(picture),
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{seconds:.3f}",
                        *VIDEO_ARGS, *AUDIO_ARGS, "-shortest", str(output)], check=True, capture_output=True)
    return output


def render_intro(source: str | Path, output: Path, *, ffmpeg: str = "ffmpeg", width: int = 1920, height: int = 1080) -> Path:
    """The person's intro video, framed to the show's size (black bars, never cropped)."""
    from core.ffmpeg import ffprobe
    has_audio = any(stream.get("codec_type") == "audio" for stream in ffprobe(str(source)).get("streams", []))
    video = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}"
    command = [ffmpeg, "-v", "error", "-y", "-nostdin", "-i", str(source)]
    if has_audio:
        command += ["-vf", video, *VIDEO_ARGS, *AUDIO_ARGS, str(output)]
    else:
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-vf", video, "-map", "0:v", "-map", "1:a",
                    *VIDEO_ARGS, *AUDIO_ARGS, "-shortest", str(output)]
    subprocess.run(command, check=True, capture_output=True)
    return output


def join(pieces: list[Path], output: Path, *, ffmpeg: str = "ffmpeg") -> Path:
    """Pieces back to back (same encoding): no re-encode of the show."""
    with tempfile.TemporaryDirectory() as scratch:
        listing = Path(scratch) / "pieces.txt"
        listing.write_text("".join(f"file '{str(piece).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for piece in pieces), encoding="utf-8")
        subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                        "-movflags", "+faststart", str(output)], check=True, capture_output=True)
    return output


def with_opening(show: Path, output: Path, *, title: str, logo: str | Path | None, intro: str | Path | None,
                 seconds: float = 2.0, ffmpeg: str = "ffmpeg", cancel: Callable[[], None] | None = None) -> float:
    """``show`` with the title card and intro in front, written to ``output``.
    Returns how many seconds were added before the show (captions move by that)."""
    from core.ffmpeg import ffprobe
    with tempfile.TemporaryDirectory() as scratch:
        folder = Path(scratch)
        pieces = []
        if title.strip() or logo:
            pieces.append(render_card(title, logo, folder / "card.mp4", seconds=seconds, ffmpeg=ffmpeg))
        if cancel:
            cancel()
        if intro:
            pieces.append(render_intro(intro, folder / "intro.mp4", ffmpeg=ffmpeg))
        if cancel:
            cancel()
        added = sum(float(ffprobe(str(piece))["format"]["duration"]) for piece in pieces)
        join([*pieces, show], output, ffmpeg=ffmpeg)
    return round(added, 3)
