from __future__ import annotations

import shutil
import subprocess

import pytest

from core.spherical_metadata import (
    SphericalMetadataError,
    inject_spherical_metadata,
    verify_spherical_metadata,
)


@pytest.fixture
def plain_video(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    path = tmp_path / "plain.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x320:duration=1:rate=25",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return path


def test_inject_spherical_metadata_writes_real_boxes_and_passes_ffprobe(tmp_path, plain_video):
    """ffmpeg's own -metadata flags never create real uuid/sv3d/st3d boxes
    (confirmed empirically: even with +use_metadata_tags a stream-copied
    file has zero real spherical box structures). This vendored injector
    must write the real boxes, and ffprobe -- the same tool a real player's
    demuxer logic mirrors -- must report a "Spherical Mapping" side_data
    entry afterward.
    """
    output = tmp_path / "spherical.mp4"

    inject_spherical_metadata(str(plain_video), str(output))

    assert output.exists()
    data = output.read_bytes()
    assert b"sv3d" in data
    assert b"st3d" in data

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    import json

    streams = json.loads(probe.stdout)["streams"]
    side_data_types = {
        entry.get("side_data_type")
        for stream in streams
        for entry in (stream.get("side_data_list") or [])
    }
    assert "Spherical Mapping" in side_data_types

    # verify_spherical_metadata must independently confirm this, not just
    # the injector's own success.
    verify_spherical_metadata(str(output))


def test_verify_spherical_metadata_fails_loudly_on_a_plain_file(plain_video):
    """The automated guard must refuse a file with no spherical metadata --
    this is what stops a 360 export from ever shipping silently broken.
    """
    with pytest.raises(SphericalMetadataError):
        verify_spherical_metadata(str(plain_video))
