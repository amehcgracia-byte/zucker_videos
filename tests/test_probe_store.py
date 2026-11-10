"""ffprobe results of unchanged files survive an app restart."""
import subprocess

import pytest

from core import ffmpeg


def test_probe_is_reused_from_the_store_after_a_restart(tmp_path, monkeypatch):
    status = ffmpeg.tool_status()
    if not status.get("ffprobe_path"):
        pytest.skip("ffprobe unavailable")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(ffmpeg, "_probe_store_dir", lambda: tmp_path / "probes")
    monkeypatch.setattr(ffmpeg, "_PROBE_MEMORY", {})
    clip = tmp_path / "clip.mp4"
    subprocess.run([status["ffmpeg_path"], "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=navy:s=64x64:d=1", str(clip)], check=True)
    first = ffmpeg.ffprobe(str(clip))
    assert list((tmp_path / "probes").glob("*.json"))
    # A new process: empty in-process caches, ffprobe itself must not run.
    ffmpeg._cached_ffprobe.cache_clear()
    monkeypatch.setattr(ffmpeg, "_PROBE_MEMORY", {})
    monkeypatch.setattr(ffmpeg, "_cached_ffprobe", lambda *args: pytest.fail("ffprobe ran again"))
    assert ffmpeg.ffprobe(str(clip)) == first
    # A changed file is probed again.
    clip.write_bytes(clip.read_bytes() + b"\0")
    with pytest.raises(pytest.fail.Exception):
        ffmpeg.ffprobe(str(clip))


def test_opening_a_project_never_copies_a_proxy_between_disks(tmp_path, monkeypatch):
    from core import normalization
    from core.project import create_project
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    source = tmp_path / "C0001.MP4"
    source.write_bytes(b"camera")
    old_proxy = tmp_path / "old-root" / "proxy.mp4"
    old_proxy.parent.mkdir()
    old_proxy.write_bytes(b"proxy")
    signature = normalization._source_signature(source)
    project = create_project("Moved", str(tmp_path / "Moved.zuckervid"))
    project.data["inputs"]["videos"] = [{"path": str(source), "normalized": {
        "path": str(old_proxy), "kind": "proxy", "normalization_version": normalization.NORMALIZATION_VERSION,
        "source_size": signature["size"], "source_mtime": signature["mtime"]}}]
    monkeypatch.setattr(normalization, "_same_disk", lambda first, second: False)
    normalization.migrate_project_normalization_cache(project)
    assert old_proxy.exists()  # left for the preparation job
    record = project.data["inputs"]["videos"][0]
    key = normalization.cache_key_for_signature(source.resolve(), signature)
    destination = normalization.global_proxy_path(key)
    # The preparation job may copy it, with progress.
    assert normalization._migrate_legacy_normalized(project, record, destination, signature)
    assert destination.exists()
