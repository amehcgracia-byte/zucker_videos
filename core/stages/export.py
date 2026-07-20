"""ffmpeg export stage for wizard edit plans."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import json
from pathlib import Path
from typing import Any

from core.ffmpeg import FFmpegError, tool_status
from core.messages import t
from core.media_validation import record_is_usable_camera_video, record_media_path
from core.normalization import global_cache_root, source_cache_key
from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import load_coverage
from core.stages.edit import load_edit_plan

MAX_EXPORT_BYTES = int(1.9 * 1024 * 1024 * 1024)
AUDIO_BITRATE = 192_000
MIN_ACCEPTABLE_VIDEO_BITRATE = 2_500_000
MAX_VIDEO_BITRATE = 18_000_000


class ExportStage(Stage):
    """Render the wizard edit plan to MP4."""

    name = "export"
    dependencies = ["edit"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint edit output and export settings."""
        return stable_fingerprint(
            {
                "edit": project.data["stages"]["edit"].get("fingerprint"),
                "wizard": project.data["settings"].get("wizard", {}),
                "settings": project.data["settings"].get(self.name, {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return the export manifest artifact path."""
        return {"export_manifest": str(artifact_path(project, "export_manifest.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Render the wizard export with streamed ffmpeg progress."""
        progress_callback(5, t("preparing_export"))
        try:
            plan = load_edit_plan(project)
        except FileNotFoundError:
            plan = load_coverage(project)
        segments = plan.get("segments") or []
        if not segments:
            raise ValueError(t("missing_segments"))
        master = project.data["inputs"].get("master")
        if not master:
            raise ValueError(t("missing_master_for_export"))
        platform = plan.get("platform") or project.data["settings"].get("wizard", {}).get("platform") or "youtube"
        output_path = _output_path(project, platform)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = _plan_duration(segments)
        bitrate_info = _bitrate_for_duration(duration)
        warnings = list(plan.get("warnings") or [])
        if bitrate_info["warning"]:
            progress_callback(6, bitrate_info["warning"])
        _render_plan(project, segments, master["path"], output_path, platform, bitrate_info["video_bitrate"], warnings, progress_callback)
        progress_callback(95, t("saving_result"))
        path = artifact_path(project, "export_manifest.json")
        if bitrate_info["warning"]:
            warnings.append(bitrate_info["warning"])
        clip_fates = _clip_fates(project, plan, segments, duration)
        write_artifact_json(
            path,
            {
                "stage": self.name,
                "render_logic": "edit-plan segments with master-audio slices; concat",
                "warnings": warnings,
                "max_export_bytes": MAX_EXPORT_BYTES,
                "target_video_bitrate": bitrate_info["video_bitrate"],
                "exports": [
                    {
                        "platform": platform,
                        "path": str(output_path),
                        "filename": output_path.name,
                        "duration_sec": duration,
                        "warnings": warnings,
                        "cut_count": int(plan.get("cut_count") or max(0, len(segments) - 1)),
                        "camera_usage": plan.get("camera_usage") or _camera_usage(segments),
                        "excluded_clips": plan.get("excluded_clips") or [],
                        "clip_fates": clip_fates,
                    }
                ],
            },
        )
        progress_callback(100, t("export_ready"))
        return self.outputs(project)


def _output_path(project: Project, platform: str) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in " ._-" else "-" for ch in project.data["name"]).strip() or "video"
    return project.exports_dir / f"{safe_name}-{platform}.mp4"


def _render_plan(
    project: Project,
    segments: list[dict[str, Any]],
    master_path: str,
    output_path: Path,
    platform: str,
    video_bitrate: int,
    warnings: list[str],
    progress_callback: ProgressCallback,
) -> None:
    ffmpeg = _ffmpeg_path()
    temp_dir = output_path.parent / f".{output_path.stem}-segments"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    segment_paths: list[Path] = []
    total_duration = _plan_duration(segments)
    rendered_duration = 0.0
    color_profiles = _color_profiles_for_segments(project, segments, warnings)
    overlay_config = _overlay_config(platform, segments[0])
    try:
        for index, segment in enumerate(segments, start=1):
            segment_path = temp_dir / f"segment-{index:04d}.mp4"
            segment_duration = max(0.1, float(segment["duration_sec"]))
            base_percent = 10 + int(70 * rendered_duration / max(total_duration, 0.1))

            def segment_progress(local_percent: int, detail: str, *, index: int = index) -> None:
                segment_share = 70 * segment_duration / max(total_duration, 0.1)
                percent = base_percent + int(segment_share * local_percent / 100)
                progress_callback(min(84, percent), f"Rendering segment {index}/{len(segments)}: {detail}")

            color_profile = color_profiles.get(str(segment.get("clip_path")), {})
            _render_segment(
                segment,
                master_path,
                segment_path,
                platform,
                video_bitrate,
                overlay_config,
                color_profile,
                segment_progress,
                intro_fade=index == 1,
                outro_fade=index == len(segments),
            )
            segment_paths.append(segment_path)
            rendered_duration += segment_duration
        concat_path = temp_dir / "concat.txt"
        concat_path.write_text("".join(_concat_file_line(path) for path in segment_paths), encoding="utf-8")
        _run_ffmpeg_progress(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-progress",
                "pipe:1",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c",
                "copy",
                str(output_path),
            ],
            total_duration,
            t("joining_segments"),
            lambda percent, detail: progress_callback(84 + int(percent * 11 / 100), detail),
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _render_segment(
    segment: dict[str, Any],
    master_path: str,
    output_path: Path,
    platform: str,
    video_bitrate: int,
    overlay_config: dict[str, Any] | None = None,
    color_profile: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
    intro_fade: bool = False,
    outro_fade: bool = False,
) -> None:
    ffmpeg = _ffmpeg_path()
    duration = max(0.1, float(segment["duration_sec"]))
    watermark = _watermark_path()
    filter_complex = _segment_filtergraph(
        platform,
        duration,
        overlay_config or {},
        color_profile or {},
        bool(watermark),
        _ffmpeg_supports_filter("drawtext"),
        intro_fade=intro_fade,
        outro_fade=outro_fade,
    )
    command_base = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-ss",
        f"{float(segment['clip_start_sec']):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(Path(segment["clip_path"])),
        "-ss",
        f"{float(segment.get('master_start_sec') or 0.0):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(Path(master_path)),
    ]
    if watermark:
        command_base.extend(["-loop", "1", "-i", str(watermark)])
    command_base.extend(
        [
            "-filter_complex",
            filter_complex,
        "-map",
            "[v]",
        "-map",
            "1:a:0",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
        ]
    )
    hardware = _video_encode_args("h264_videotoolbox", video_bitrate)
    software = _video_encode_args("libx264", video_bitrate)
    try:
        _run_ffmpeg_progress(command_base + hardware + [str(output_path)], duration, Path(str(segment["clip_path"])).name, progress_callback)
    except FFmpegError as exc:
        if output_path.exists():
            output_path.unlink()
        if progress_callback:
            progress_callback(0, t("hardware_fallback"))
        try:
            _run_ffmpeg_progress(command_base + software + [str(output_path)], duration, Path(str(segment["clip_path"])).name, progress_callback)
        except FFmpegError as fallback_exc:
            raise FFmpegError(f"{fallback_exc}\nFallback after h264_videotoolbox failed with: {exc}") from fallback_exc


def _run_ffmpeg_progress(command: list[str], duration: float, label: str, progress: ProgressCallback | None) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    current = -1
    for line in process.stdout:
        match = re.match(r"out_time_ms=(\d+)", line.strip())
        if not match or duration <= 0:
            continue
        seconds = int(match.group(1)) / 1_000_000
        percent = max(current, min(99, int(seconds / duration * 100)))
        if progress and percent > current:
            current = percent
            progress(percent, f"{label} — {percent}%")
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise FFmpegError((stderr or "").strip() or "ffmpeg export failed")
    if progress:
        progress(100, f"{label} — 100%")


def _base_video_filter(platform: str) -> str:
    if platform in {"instagram", "tiktok"}:
        return "scale=608:1080:force_original_aspect_ratio=increase,crop=608:1080,setsar=1,format=yuv420p"
    return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"


def _segment_filtergraph(
    platform: str,
    duration: float,
    overlay_config: dict[str, Any],
    color_profile: dict[str, Any],
    has_watermark: bool = True,
    text_enabled: bool = True,
    intro_fade: bool = False,
    outro_fade: bool = False,
) -> str:
    filters = [_base_video_filter(platform), _color_filter(color_profile)]
    if intro_fade:
        filters.append("fade=t=in:st=0:d=0.5")
    if outro_fade:
        filters.append(f"fade=t=out:st={max(0.0, duration - 0.5):.3f}:d=0.5")
    if text_enabled:
        filters.extend(_text_filters(platform, duration, overlay_config))
    filters.append("format=yuv420p")
    graph = f"[0:v]{','.join(filter for filter in filters if filter)}[base]"
    if not has_watermark:
        return f"{graph};[base]copy[v]"
    margin = 40 if platform == "youtube" else 28
    wm_height = 65
    return (
        f"{graph};"
        f"[2:v]format=rgba,scale=-1:{wm_height},colorchannelmixer=aa=0.70[wm];"
        f"[base][wm]overlay=W-w-{margin}:H-h-{margin}:format=auto[v]"
    )


def _color_filter(color_profile: dict[str, Any]) -> str:
    brightness = max(-0.08, min(0.08, float(color_profile.get("brightness_adjust") or 0.0)))
    saturation = max(0.90, min(1.10, float(color_profile.get("saturation_adjust") or 1.0)))
    return f"eq=brightness={brightness:.4f}:saturation={saturation:.4f}"


def _clip_fates(project: Project, plan: dict[str, Any], segments: list[dict[str, Any]], total_duration: float) -> list[dict[str, Any]]:
    """Summarize every registered clip as used, excluded, or not covering the edit."""
    used_seconds: dict[str, float] = {}
    names_by_path: dict[str, str] = {}
    for segment in segments:
        path = str(segment.get("clip_path") or "")
        if not path:
            continue
        used_seconds[path] = used_seconds.get(path, 0.0) + float(segment.get("duration_sec") or 0.0)
        names_by_path[path] = str(segment.get("filename") or Path(path).name)

    excluded_by_path: dict[str, dict[str, Any]] = {}
    excluded_by_name: dict[str, dict[str, Any]] = {}
    for item in plan.get("excluded_clips") or []:
        diagnostic = item.get("diagnostic") or {}
        for key in (diagnostic.get("path"), diagnostic.get("source_path")):
            if key:
                excluded_by_path[str(key)] = item
        excluded_by_name[str(item.get("filename") or diagnostic.get("filename") or "")] = item

    diagnostics = list(plan.get("clip_diagnostics") or [])
    diagnostic_keys = {
        str(value)
        for diagnostic in diagnostics
        for value in (diagnostic.get("path"), diagnostic.get("source_path"))
        if value
    }
    for record in project.data.get("inputs", {}).get("videos", []):
        media_path = record_media_path(record)
        if str(media_path) in diagnostic_keys or str(record.get("path")) in diagnostic_keys:
            continue
        reason = record.get("not_a_video_reason") or "not covering this song"
        diagnostics.append(
            {
                "clip_id": None,
                "filename": Path(str(record.get("path") or media_path)).name,
                "path": media_path,
                "source_path": record.get("path"),
                "valid_video": record_is_usable_camera_video(record),
                "reason": reason,
            }
        )

    fates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        path = str(diagnostic.get("path") or diagnostic.get("source_path") or "")
        filename = str(diagnostic.get("filename") or names_by_path.get(path) or Path(path).name or "clip")
        excluded = excluded_by_path.get(path) or excluded_by_name.get(filename)
        if excluded:
            status = "excluded"
            used_percent = 0.0
            reason = str(excluded.get("reason") or "excluded")
        elif path in used_seconds:
            status = "used"
            used_percent = used_seconds[path] / max(total_duration, 0.1) * 100
            reason = "used in final edit"
        elif diagnostic.get("valid_video") is False:
            status = "excluded"
            used_percent = 0.0
            reason = str(diagnostic.get("reason") or "not a usable camera video")
        else:
            status = "not_covering"
            used_percent = 0.0
            reason = "not covering this song"
        seen.add(path or filename)
        fates.append(
            {
                "clip_id": diagnostic.get("clip_id"),
                "filename": filename,
                "status": status,
                "reason": reason,
                "used_percent": round(used_percent, 1),
                "confidence": diagnostic.get("confidence"),
                "threshold": diagnostic.get("threshold"),
                "offset_sec": diagnostic.get("offset_sec"),
                "verification": diagnostic.get("verification"),
                "manual_override": diagnostic.get("manual_override"),
            }
        )
    for path, seconds in used_seconds.items():
        key = path or names_by_path.get(path, "")
        if key in seen:
            continue
        fates.append(
            {
                "filename": names_by_path.get(path) or Path(path).name,
                "status": "used",
                "reason": "used in final edit",
                "used_percent": round(seconds / max(total_duration, 0.1) * 100, 1),
            }
        )
    return fates


def _text_filters(platform: str, duration: float, config: dict[str, Any]) -> list[str]:
    title = str(config.get("title") or "").strip()
    band = str(config.get("band_name") or "").strip()
    handle = str(config.get("handle") or "").strip()
    filters: list[str] = []
    if title:
        if platform == "youtube":
            text = title if not band else f"{title} • {band}"
            filters.append(_drawtext(text, "x=64:y=h-th-92:fontsize=46:enable='between(t,0,4)'"))
        else:
            filters.append(_drawtext(title, "x=(w-tw)/2:y=(h-th)/2:fontsize=54:enable='between(t,0,4)'"))
    if platform in {"instagram", "tiktok"} and handle:
        filters.append(_drawtext(handle, f"x=(w-tw)/2:y=h-th-130:fontsize=38:enable='gte(t,{max(0.0, duration - 4):.3f})'"))
    return filters


def _drawtext(text: str, placement: str) -> str:
    font = _font_path()
    font_part = f"fontfile='{_escape_filter_value(str(font))}':" if font else ""
    return (
        "drawtext="
        f"{font_part}text='{_escape_filter_value(text)}':"
        "fontcolor=white:shadowcolor=black@0.60:shadowx=2:shadowy=2:"
        f"{placement}"
    )


def _overlay_config(platform: str, first_segment: dict[str, Any]) -> dict[str, Any]:
    config = _global_config()
    return {
        "platform": platform,
        "title": first_segment.get("title") or config.get("project_name"),
        "band_name": config.get("band_name") or "",
        "handle": config.get("handle") or "",
    }


def _global_config() -> dict[str, Any]:
    path = Path.home() / "ZuckerVideos" / "config.json"
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _watermark_path() -> Path | None:
    candidates = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "watermark.png",
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "logo_editor_blue.png",
        Path(__file__).resolve().parents[2] / "assets" / "watermark.png",
        Path(__file__).resolve().parents[2] / "assets" / "logo_editor_blue.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _font_path() -> Path | None:
    for candidate in (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/SFNS.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
    ):
        if candidate.exists():
            return candidate
    return None


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def _concat_file_line(path: Path) -> str:
    return "file '{}'\n".format(path.as_posix().replace("'", "'\\''"))


def _color_profiles_for_segments(project: Project, segments: list[dict[str, Any]], warnings: list[str] | None = None) -> dict[str, dict[str, Any]]:
    raw: dict[str, dict[str, float]] = {}
    warnings = warnings if warnings is not None else []
    for segment in segments:
        clip_path = str(segment.get("clip_path"))
        if clip_path and clip_path not in raw:
            profile, warning = cached_or_measure_clip_color(project, clip_path)
            if warning:
                warnings.append(warning)
            raw[clip_path] = profile
    valid = [profile for profile in raw.values() if profile]
    if not valid:
        return {path: {} for path in raw}
    target_luma = sum(profile["luma"] for profile in valid) / len(valid)
    target_sat = sum(profile["saturation"] for profile in valid) / len(valid)
    corrected: dict[str, dict[str, Any]] = {}
    for path, profile in raw.items():
        if not profile:
            corrected[path] = {}
            continue
        corrected[path] = color_correction_for_profile(profile, target_luma, target_sat)
    return corrected


def cached_or_measure_clip_color(project: Project, path: str) -> tuple[dict[str, float], str | None]:
    """Return cached color profile or measure bounded samples."""
    key = _color_cache_key(project, path)
    cache_path = global_cache_root() / "color" / f"{key}.json"
    if cache_path.exists():
        try:
            with cache_path.open("r", encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict) and "luma" in cached and "saturation" in cached:
                return {"luma": float(cached["luma"]), "saturation": float(cached["saturation"])}, None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    profile, warning = measure_clip_color(path)
    if profile:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w", encoding="utf-8") as fh:
            json.dump(profile, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return profile, warning


def _color_cache_key(project: Project, path: str) -> str:
    for record in project.data.get("inputs", {}).get("videos", []):
        normalized = record.get("normalized") or {}
        if path in {record.get("path"), normalized.get("path")}:
            return str(record.get("cache_key") or normalized.get("cache_key") or source_cache_key(record))
    return stable_fingerprint({"path": str(Path(path).expanduser().resolve())})[:24]


def measure_clip_color(path: str) -> tuple[dict[str, float], str | None]:
    """Measure sampled luma/saturation using short ffmpeg signalstats windows."""
    try:
        duration = _media_duration(path)
    except Exception as exc:
        return {}, t("color_skipped", filename=Path(path).name, reason=str(exc))
    y_values: list[float] = []
    sat_values: list[float] = []
    for command in color_sample_commands(path, duration):
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
        except subprocess.TimeoutExpired:
            return {}, t("color_skipped", filename=Path(path).name, reason="sample timed out")
        if result.returncode != 0:
            return {}, t("color_skipped", filename=Path(path).name, reason=(result.stderr or "sample failed").strip()[:180])
        text = f"{result.stdout}\n{result.stderr}"
        y_values.extend(float(value) for value in re.findall(r"lavfi.signalstats.YAVG=([0-9.]+)", text))
        sat_values.extend(float(value) for value in re.findall(r"lavfi.signalstats.SATAVG=([0-9.]+)", text))
    if not y_values or not sat_values:
        return {}, t("color_skipped", filename=Path(path).name, reason="no signalstats samples")
    return {"luma": sum(y_values) / len(y_values), "saturation": sum(sat_values) / len(sat_values)}, None


def color_sample_commands(path: str, duration: float) -> list[list[str]]:
    """Build bounded color-sampling ffmpeg commands."""
    window = min(2.0, max(0.25, duration / 5.0))
    commands: list[list[str]] = []
    for fraction in (0.10, 0.30, 0.50, 0.70, 0.90):
        start = max(0.0, min(duration - window, duration * fraction))
        commands.append(
            [
                _ffmpeg_path(),
                "-hide_banner",
                "-nostdin",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{window:.3f}",
                "-i",
                path,
                "-vf",
                "signalstats,metadata=print",
                "-an",
                "-f",
                "null",
                "-",
            ]
        )
    return commands


def _media_duration(path: str) -> float:
    status = tool_status()
    ffprobe = status.get("ffprobe_path")
    if not ffprobe:
        raise FFmpegError("ffprobe is missing. Install it with: brew install ffmpeg")
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or "ffprobe failed")
    return max(0.1, float((result.stdout or "0").strip() or 0.0))


def color_correction_for_profile(profile: dict[str, float], target_luma: float, target_sat: float) -> dict[str, float]:
    """Return subtle eq corrections toward a shared target."""
    luma = max(1.0, float(profile.get("luma") or target_luma or 128.0))
    sat = max(1.0, float(profile.get("saturation") or target_sat or 64.0))
    brightness = max(-0.08, min(0.08, (target_luma - luma) / 255.0 * 0.35))
    saturation = max(0.90, min(1.10, 1.0 + (target_sat - sat) / 255.0 * 0.60))
    return {"brightness_adjust": brightness, "saturation_adjust": saturation}


def _video_encode_args(codec: str, video_bitrate: int) -> list[str]:
    if codec == "h264_videotoolbox":
        return [
            "-c:v",
            codec,
            "-b:v",
            str(video_bitrate),
            "-maxrate",
            str(int(video_bitrate * 1.2)),
            "-bufsize",
            str(int(video_bitrate * 2)),
            "-profile:v",
            "high",
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        str(video_bitrate),
        "-maxrate",
        str(int(video_bitrate * 1.2)),
        "-bufsize",
        str(int(video_bitrate * 2)),
    ]


def _bitrate_for_duration(duration: float) -> dict[str, Any]:
    seconds = max(1.0, duration)
    budget_bits = MAX_EXPORT_BYTES * 8 * 0.94
    target = int(max(300_000, budget_bits / seconds - AUDIO_BITRATE))
    warning = None
    if target < MIN_ACCEPTABLE_VIDEO_BITRATE:
        warning = t("bitrate_cap_warning")
    return {"video_bitrate": min(target, MAX_VIDEO_BITRATE), "warning": warning}


def _plan_duration(segments: list[dict[str, Any]]) -> float:
    return sum(max(0.0, float(segment.get("duration_sec") or 0.0)) for segment in segments)


def _camera_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        name = Path(str(segment.get("clip_path"))).name
        usage[name] = usage.get(name, 0) + 1
    return usage


def _ffmpeg_path() -> str:
    status = tool_status()
    ffmpeg = status.get("ffmpeg_path")
    if not ffmpeg:
        raise FFmpegError("ffmpeg is missing. Install it with: brew install ffmpeg")
    return str(ffmpeg)


_FILTER_SUPPORT_CACHE: dict[str, bool] = {}


def _ffmpeg_supports_filter(name: str) -> bool:
    if name in _FILTER_SUPPORT_CACHE:
        return _FILTER_SUPPORT_CACHE[name]
    try:
        result = subprocess.run([_ffmpeg_path(), "-hide_banner", "-filters"], capture_output=True, text=True, check=False)
        supported = result.returncode == 0 and re.search(rf"\b{name}\b", result.stdout) is not None
    except Exception:
        supported = False
    _FILTER_SUPPORT_CACHE[name] = supported
    return supported
