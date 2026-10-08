"""Reproject one decoded spherical frame once, without live v360 commands."""
from __future__ import annotations

import math
import logging
import sys
from pathlib import Path
from functools import lru_cache
import subprocess
import threading
import time
from typing import Any, Callable

import cv2
import numpy as np

from core.render_watchdog import ProcessWatchdog
from core.ffmpeg import FFmpegError, ffprobe
from core.storage import working_temporary_file
from core.spherical_view import view_parameters

RECIPE_VERSION = 4
MOVEMENTS = ("push_in", "pull_out", "pan_left", "pan_right", "close_hold", "reveal", "settle", "hold")


def motion_pose(shot: dict[str, Any], duration: float, seconds: float) -> tuple[float, float, float]:
    yaw, pitch, fov = (float(shot.get(key) or default) for key, default in (("yaw", 0), ("pitch", 0), ("fov", 82)))
    style = str(shot.get("music_style") or "tranquilo")
    if not shot.get("runtime_motion_enabled") or duration < (1 if style == "frenetico" else 2):
        return yaw, pitch, fov
    amount = min(1., max(0., seconds / max(.001, duration - 1 / 30)))
    eased = amount * amount * (3 - 2 * amount)
    if shot.get("movement") == "planet_to_stage" and isinstance(shot.get("reveal_target"), dict):
        target = shot["reveal_target"]
        target_yaw = float(target.get("yaw", yaw))
        delta_yaw = (target_yaw-yaw+180) % 360-180
        return (yaw + delta_yaw*eased,
                pitch + (float(target.get("pitch", 0))-pitch)*eased,
                fov + (float(target.get("fov", 130))-fov)*eased)
    if shot.get("motion_easing") == "accelerate":
        eased = amount ** 1.8
    movement = str(shot.get("movement") or "pan_right")
    minimum_fov = min(55., fov)
    delta = min(10., fov * .12, max(0., fov - minimum_fov))
    if style in {"animado", "frenetico"}:
        delta = min(22., fov * (.23 if style == "animado" else .32), max(0., fov - minimum_fov))
    if movement == "push_in": fov -= delta * eased
    elif movement == "pull_out": fov -= delta * (1 - eased)
    elif movement == "close_hold": fov = max(minimum_fov, fov * .82)
    elif movement == "reveal": fov -= delta * (1 - eased); yaw += 2 * eased
    elif movement == "settle": fov -= delta * eased; yaw -= 2 * eased
    elif movement in {"pan_left", "pan_right"}:
        span = min(4., duration * .6) if style == "tranquilo" else min(fov * .14, duration * (3 if style == "animado" else 6))
        yaw += (-1 if movement == "pan_left" else 1) * span * eased
    return yaw, pitch, max(minimum_fov, fov)


@lru_cache(maxsize=4)
def _projection_grid(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    grid = np.meshgrid((np.arange(width, dtype=np.float32) + .5) * 2 / width - 1,
                       (np.arange(height, dtype=np.float32) + .5) * 2 / height - 1)
    for item in grid:
        item.flags.writeable = False
    return grid[0], grid[1]


def reproject_maps(source_size: tuple[int, int], output_size: tuple[int, int], shot: dict[str, Any],
                   pose: tuple[float, float, float] | None = None, *, exact: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Return continuous longitude/latitude maps; unwrap before interpolation."""
    sw, sh = source_size; width, height = output_size
    yaw, pitch, fov = pose or (float(shot.get("yaw") or 0), float(shot.get("pitch") or 0), float(shot.get("fov") or 82))
    preset = shot.get("projection_preset")
    if pose is not None and shot.get("runtime_motion_enabled") and preset == "dewarp" and fov < 70:
        # DEWARP is rectilinear too. Its editor's 70-degree preset floor must
        # not silently cancel an authored close-up zoom during native rendering.
        preset = "linear"
    view = view_parameters(yaw, pitch, fov, width / height, str(shot.get("type") or ""),
                           projection_preset=preset, roll=float(shot.get("roll") or 0))
    if pose is not None and shot.get("movement") == "planet_to_stage":
        # Keep one continuous stereographic lens through the whole reveal.
        # A static planet's preset floor would freeze the zoom below 220°.
        horizontal = min(300., max(55., fov))
        view.update(projection="sg", h_fov=horizontal,
                    v_fov=math.degrees(4*math.atan(math.tan(math.radians(horizontal)/4)/(width/height))))
    mw, mh = (width, height) if exact else (max(64, width // 3), max(36, height // 3))
    xx, yy = _projection_grid(mw, mh)
    if view['projection'] == 'sg':
        x = xx * math.tan(math.radians(float(view['h_fov'])) / 4)
        y = yy * math.tan(math.radians(float(view['v_fov'])) / 4)
        square = x * x + y * y
        z = (1 - square) / (1 + square)
        x = 2 * x / (1 + square); y = 2 * y / (1 + square)
    else:
        x = xx * math.tan(math.radians(float(view['h_fov'])) / 2)
        y = yy * math.tan(math.radians(float(view['v_fov'])) / 2)
        z = np.ones_like(x)
        norm = np.sqrt(x * x + y * y + z * z)
        x /= norm; y /= norm; z /= norm
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(float(view['pitch']))), math.sin(math.radians(float(view['pitch'])))
    cr, sr = math.cos(math.radians(float(view['roll']))), math.sin(math.radians(float(view['roll'])))
    x, y = cr * x - sr * y, sr * x + cr * y
    y, z = cp * y - sp * z, sp * y + cp * z
    x, z = cy * x + sy * z, -sy * x + cy * z
    longitude = np.unwrap(np.arctan2(x, z), axis=1)
    longitude = np.unwrap(longitude, axis=0)
    mx = ((longitude / (2 * np.pi) + .5) * sw - .5).astype(np.float32)
    my = ((np.arcsin(np.clip(y, -1, 1)) / np.pi + .5) * sh - .5).astype(np.float32)
    if (mw, mh) != (width, height):
        mx = cv2.resize(mx, (width, height), interpolation=cv2.INTER_CUBIC)
        my = cv2.resize(my, (width, height), interpolation=cv2.INTER_CUBIC)
    mx %= sw
    np.clip(my, 0, sh - 1, out=my)
    return mx, my


LOGGER = logging.getLogger(__name__)


class HardwareDecodeError(FFmpegError):
    """A hardware decoder failure; encoder and cancellation errors stay separate."""


@lru_cache(maxsize=64)
def _verified_hardware_format(source: str, identity: tuple) -> str | None:
    # Metadata only, not a cached content hash. Be conservative: this is the
    # format/range/codec combination validated against the real 360 originals.
    try:
        streams = ffprobe(source).get("streams") or []
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
    except (OSError, FFmpegError, StopIteration, ValueError):
        return None
    if (video.get("codec_name") == "hevc" and video.get("pix_fmt") == "yuvj420p"
            and video.get("color_range") == "pc" and video.get("color_space") == "bt709"):
        return "yuvj420p"
    return None


def run_reprojected_command(command: list[str], source: str, source_size: tuple[int, int],
                            start: float, frame_count: int, shot: dict[str, Any],
                            progress: Callable | None = None, *,
                            pose_sampler: Callable | None = None,
                            hardware_decode: bool = True, remap_backend: str = "cpu") -> None:
    pixel_format = None
    if hardware_decode and sys.platform == "darwin":
        try:
            stat = Path(source).stat()
            pixel_format = _verified_hardware_format(source,
                (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        except OSError:
            pass
    if pixel_format:
        try:
            _run_reprojected_command(command, source, source_size, start, frame_count, shot,
                progress, pose_sampler=pose_sampler, decoder_pixel_format=pixel_format, remap_backend=remap_backend)
            return
        except HardwareDecodeError as exc:
            LOGGER.warning("360 hardware decoder failed; retrying with CPU: %s", exc)
    _run_reprojected_command(command, source, source_size, start, frame_count, shot,
        progress, pose_sampler=pose_sampler, remap_backend=remap_backend)


def _run_reprojected_command(command: list[str], source: str, source_size: tuple[int, int],
                            start: float, frame_count: int, shot: dict[str, Any],
                            progress: Callable | None = None, *,
                            pose_sampler: Callable | None = None,
                            decoder_pixel_format: str | None = None, remap_backend: str = "cpu") -> None:
    """Pipe atomic reprojected BGR frames directly into the final encoder graph."""
    ffmpeg = command[0]; sw, sh = source_size
    if frame_count <= 0:
        raise ValueError("A 360 segment must contain at least one frame")
    duration = frame_count / 30
    LOGGER.info("360 decoder backend=%s source=%s start=%.6f frames=%d format=%s",
                "videotoolbox" if decoder_pixel_format else "cpu", source, start, frame_count, decoder_pixel_format)
    decoder_options = ['-hwaccel', 'videotoolbox'] if decoder_pixel_format else []
    decoder_filter = f'format={decoder_pixel_format},fps=30,format=bgr24' if decoder_pixel_format else 'fps=30,format=bgr24'
    decoder_command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-threads', '2', '-ss', str(start), *decoder_options, '-i', source,
                       '-vf', decoder_filter, '-an', '-frames:v', str(frame_count), '-f', 'rawvideo', 'pipe:1']
    stopped = threading.Event(); failures: list[BaseException] = []; completed = [0]; last_frame_at = [time.monotonic()]
    with working_temporary_file() as decode_log, working_temporary_file() as encode_log:
        decoder = subprocess.Popen(decoder_command, stdout=subprocess.PIPE, stderr=decode_log)
        encoder = None
        watcher = None
        guard = None
        blocked = [decoder]
        try:
            encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encode_log)
            assert decoder.stdout and encoder.stdin
            guard = ProcessWatchdog(encoder, command[-1], peers=(decoder,), blocked_process=lambda: blocked[0]).start()
            def notify() -> None:
                if progress: progress(min(99, int(completed[0] / frame_count * 100)), f"360 {shot.get('movement') or 'hold'} — {completed[0]}/{frame_count} frames")
            def monitor() -> None:
                while not stopped.wait(1):
                    try:
                        if not guard.enabled and time.monotonic() - last_frame_at[0] > 180:
                            raise FFmpegError("360 source stopped producing frames for 180 seconds")
                        notify()
                    except BaseException as exc:
                        failures.append(exc)
                        for process in (decoder, encoder):
                            if process.poll() is None: process.kill()
                        return
            watcher = threading.Thread(target=monitor, daemon=True); watcher.start()
            # Reuse one frame buffer. A 5K sphere is tens of MB: read/extend
            # previously copied that payload twice for every decoded frame.
            frame = np.empty((sh, sw, 3), dtype=np.uint8)
            pixels = np.empty((1080, 1920, 3), dtype=np.uint8)
            frame_bytes = memoryview(frame).cast("B")
            previous_pose = None
            maps = None
            for index in range(frame_count):
                blocked[0] = decoder
                received = 0
                while received < len(frame_bytes):
                    count = decoder.stdout.readinto(frame_bytes[received:])
                    if not count: break
                    received += count
                if failures: raise failures[0]
                guard.raise_if_failed()
                if received != len(frame_bytes):
                    decode_log.seek(0)
                    diagnostics = decode_log.read().decode(errors='replace')[-2000:]
                    error_type = HardwareDecodeError if decoder_pixel_format else FFmpegError
                    raise error_type(f"360 decode ended at frame {index}/{frame_count}: {diagnostics}")
                blocked[0] = None  # reprojection is active in Python, not waiting on a child
                pose = pose_sampler(index / 30) if pose_sampler else motion_pose(shot, duration, index / 30)
                if maps is None or pose != previous_pose:
                    maps = reproject_maps((sw, sh), (1920, 1080), shot, pose)
                    previous_pose = pose
                metal_done = False
                if remap_backend == "metal":
                    from core.metal_remap import try_remap
                    metal_done = try_remap(frame, maps, pixels)
                if not metal_done:
                    cv2.remap(frame, *maps, interpolation=cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP, dst=pixels)
                blocked[0] = encoder
                encoder.stdin.write(memoryview(pixels).cast("B"))
                guard.touch()
                completed[0] = index + 1
                last_frame_at[0] = time.monotonic()
                notify()
            encoder.stdin.close(); encoder.stdin = None
            blocked[0] = encoder
            encoder_code = encoder.wait(timeout=None if guard.enabled else 120)
            guard.raise_if_failed()
            blocked[0] = decoder
            decoder_code = decoder.wait(timeout=None if guard.enabled else 30)
            guard.raise_if_failed()
            if encoder_code or decoder_code:
                encode_log.seek(0); decode_log.seek(0)
                error_type = HardwareDecodeError if decoder_pixel_format and decoder_code and not encoder_code else FFmpegError
                raise error_type((encode_log.read() + decode_log.read()).decode(errors='replace')[-4000:])
            if failures: raise failures[0]
        except OSError as exc:
            if guard: guard.raise_if_failed()
            if failures: raise failures[0]
            encode_log.seek(0); decode_log.seek(0)
            raise FFmpegError((encode_log.read() + decode_log.read()).decode(errors='replace')[-4000:] or str(exc)) from exc
        except BaseException:
            for process in (decoder, encoder):
                if process is not None and process.poll() is None: process.kill()
            raise
        finally:
            stopped.set()
            if guard: guard.close()
            if watcher: watcher.join(timeout=2)
            for process in (decoder, encoder):
                if process is not None:
                    if process.poll() is None: process.kill()
                    process.wait()
                    for stream in (process.stdout, process.stdin):
                        if stream:
                            try:
                                stream.close()
                            except OSError:
                                pass
        if progress: progress(100, "360 movement complete")
