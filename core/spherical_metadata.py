"""Injects real Google Spherical Video V2 metadata into 360 exports.

ffmpeg's ``-metadata`` flags only produce cosmetic string tags in the udta
atom; they never create the ``uuid``/``sv3d``/``st3d`` box structures that
spherical-aware players (VLC, YouTube) actually look for, so a 360 export
muxed with those flags plays back as a flat rectangle everywhere.

This module vendors Google's spatial-media injector
(``core/vendor/spatialmedia``, Apache 2.0, pure Python, no external
dependencies) to write the real boxes as the final post-processing pass of
a 360 export, then verifies the result by re-parsing the written file. It
never shells out to an external ``spatialmedia`` install — the whole
library ships inside ``core/vendor`` and is bundled into the packaged app.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# In a PyInstaller-frozen app, `core/vendor` is bundled as data under
# sys._MEIPASS (see tools/build_app.sh's --add-data), not importable via
# this file's own on-disk location -- mirrors the _MEIPASS fallback pattern
# used by core/build_info.py and app.py for other bundled resources.
_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
_VENDOR_DIR = str(_ROOT / "core" / "vendor")
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

from spatialmedia import metadata_utils  # noqa: E402  (sys.path must be set first)

from core import ffmpeg as ffmpeg_tools

SV3D_BOX_MARKER = b"sv3d"
ST3D_BOX_MARKER = b"st3d"


class SphericalMetadataError(RuntimeError):
    """Raised when spherical metadata could not be injected or verified."""


def _collecting_console(messages: list[str]):
    def _log(text: str) -> None:
        messages.append(text)

    return _log


def inject_spherical_metadata(input_path: str, output_path: str) -> None:
    """Writes `input_path` to `output_path` with real spherical metadata boxes.

    Injects both the legacy XML v1 ``uuid`` box (widest player compatibility)
    and the modern ``sv3d``/``st3d`` boxes (what YouTube's ingestion expects).
    Raises SphericalMetadataError if injection fails or the result does not
    actually carry spherical metadata — 360 exports must never ship silently
    without it.
    """
    messages: list[str] = []
    console = _collecting_console(messages)

    metadata = metadata_utils.Metadata(projection="equirectangular", stereo_mode="mono")
    metadata.video = metadata_utils.generate_spherical_xml(
        projection="equirectangular", stereo=None
    )

    try:
        metadata_utils.inject_metadata(input_path, output_path, metadata, console)
    except Exception as exc:  # noqa: BLE001 - re-raise as our own error type
        raise SphericalMetadataError(
            f"Spherical metadata injection crashed: {exc}"
        ) from exc

    if any("error" in m.lower() for m in messages):
        raise SphericalMetadataError(
            "Spherical metadata injection reported an error: " + " | ".join(messages)
        )

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise SphericalMetadataError(
            "Spherical metadata injection did not produce an output file: "
            + input_path
        )

    verify_spherical_metadata(output_path)


def verify_spherical_metadata(path: str) -> None:
    """Raises SphericalMetadataError unless `path` contains real spherical metadata.

    Checks two independent signals so a partial/broken injection can't slip
    through:
      1. Structured parse: re-parses the file with the same vendored library
         used to inject, and requires the v1 GSpherical XML ``uuid`` box to
         be present and readable on at least one video track.
      2. Raw box scan: confirms the modern ``sv3d``/``st3d`` box type tags
         exist in the file bytes (the actual boxes ffmpeg's stream copy
         otherwise drops).

    This is the automated "fail loudly" guard the export pipeline calls
    after every 360 export, so a spherical export can never ship without
    metadata YouTube/VLC recognize.
    """
    messages: list[str] = []
    console = _collecting_console(messages)

    parsed = metadata_utils.parse_metadata(path, console)

    if parsed is None or not getattr(parsed, "video", None):
        raise SphericalMetadataError(
            "Exported 360 file is missing spherical video metadata (no "
            "GSpherical uuid box found) — refusing to ship a 360 export "
            "that players won't recognize as 360. Parser log: "
            + " | ".join(messages)
        )

    with open(path, "rb") as fh:
        data = fh.read()

    if SV3D_BOX_MARKER not in data or ST3D_BOX_MARKER not in data:
        raise SphericalMetadataError(
            "Exported 360 file is missing modern sv3d/st3d spherical boxes "
            "— refusing to ship a 360 export that YouTube won't recognize "
            "as 360."
        )

    probe = ffmpeg_tools.ffprobe(path)
    side_data = []
    for stream in probe.get("streams", []):
        side_data.extend(stream.get("side_data_list", []) or [])
    has_spherical_side_data = any(
        entry.get("side_data_type") == "Spherical Mapping" for entry in side_data
    )
    if not has_spherical_side_data:
        raise SphericalMetadataError(
            "ffprobe does not report a 'Spherical Mapping' side_data entry "
            "for the exported file — refusing to ship a 360 export that "
            "ffmpeg-based players won't recognize as spherical."
        )
