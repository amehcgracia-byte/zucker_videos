"""Reproject one decoded spherical frame once, without live v360 commands."""
from __future__ import annotations

import math
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable

import cv2
import numpy as np

from core.ffmpeg import FFmpegError
from core.spherical_view import view_parameters

RECIPE_VERSION = 3
MOVEMENTS = ("push_in", "pull_out", "pan_left", "pan_right", "close_hold", "reveal", "settle", "hold")


def motion_pose(shot: dict[str, Any], duration: float, seconds: float) -> tuple[float, float, float]:
    yaw, pitch, fov = (float(shot.get(key) or default) for key, default in (("yaw", 0), ("pitch", 0), ("fov", 82)))
    style = str(shot.get("music_style") or "tranquilo")
    if not shot.get("runtime_motion_enabled") or duration < (1 if style == "frenetico" else 2):
        return yaw, pitch, fov
    amount = min(1., max(0., seconds / max(.001, duration - 1 / 30)))
    eased = amount * amount * (3 - 2 * amount)
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
    mw, mh = (width, height) if exact else (max(64, width // 3), max(36, height // 3))
    xx, yy = np.meshgrid((np.arange(mw, dtype=np.float32) + .5) * 2 / mw - 1,
                         (np.arange(mh, dtype=np.float32) + .5) * 2 / mh - 1)
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


def run_reprojected_command(command: list[str], source: str, source_size: tuple[int, int],
                            start: float, frame_count: int, shot: dict[str, Any],
                            progress: Callable | None = None, *,
                            pose_sampler: Callable | None = None) -> None:
    """Pipe atomic reprojected BGR frames directly into the final encoder graph."""
    ffmpeg = command[0]; sw, sh = source_size
    if frame_count <= 0:
        raise ValueError("A 360 segment must contain at least one frame")
    duration = frame_count / 30
    decoder_command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-threads', '2', '-ss', str(start), '-i', source,
                       '-vf', 'fps=30,format=bgr24', '-an', '-frames:v', str(frame_count), '-f', 'rawvideo', 'pipe:1']
    stopped = threading.Event(); failures: list[BaseException] = []; completed = [0]; last_frame_at = [time.monotonic()]
    with tempfile.TemporaryFile() as decode_log, tempfile.TemporaryFile() as encode_log:
        decoder = subprocess.Popen(decoder_command, stdout=subprocess.PIPE, stderr=decode_log)
        encoder = None
        watcher = None
        try:
            encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encode_log)
            assert decoder.stdout and encoder.stdin
            def notify() -> None:
                if progress: progress(min(99, int(completed[0] / frame_count * 100)), f"360 {shot.get('movement') or 'hold'} — {completed[0]}/{frame_count} frames")
            def monitor() -> None:
                while not stopped.wait(1):
                    try:
                        if time.monotonic() - last_frame_at[0] > 180:
                            raise FFmpegError("360 source stopped producing frames for 180 seconds")
                        notify()
                    except BaseException as exc:
                        failures.append(exc)
                        for process in (decoder, encoder):
                            if process.poll() is None: process.kill()
                        return
            watcher = threading.Thread(target=monitor, daemon=True); watcher.start()
            for index in range(frame_count):
                chunks = bytearray()
                while len(chunks) < sw * sh * 3:
                    chunk = decoder.stdout.read(sw * sh * 3 - len(chunks))
                    if not chunk: break
                    chunks.extend(chunk)
                if failures: raise failures[0]
                if len(chunks) != sw * sh * 3: raise FFmpegError(f"360 decode ended at frame {index}/{frame_count}")
                frame = np.frombuffer(chunks, np.uint8).reshape(sh, sw, 3)
                maps = reproject_maps((sw, sh), (1920, 1080), shot, pose_sampler(index / 30) if pose_sampler else motion_pose(shot, duration, index / 30))
                pixels = cv2.remap(frame, *maps, interpolation=cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)
                encoder.stdin.write(pixels.tobytes())
                completed[0] = index + 1
                last_frame_at[0] = time.monotonic()
                notify()
            encoder.stdin.close(); encoder.stdin = None
            if encoder.wait(timeout=120) or decoder.wait(timeout=30):
                encode_log.seek(0); decode_log.seek(0)
                raise FFmpegError((encode_log.read() + decode_log.read()).decode(errors='replace')[-4000:])
            if failures: raise failures[0]
        except OSError as exc:
            if failures: raise failures[0]
            encode_log.seek(0); decode_log.seek(0)
            raise FFmpegError((encode_log.read() + decode_log.read()).decode(errors='replace')[-4000:] or str(exc)) from exc
        except BaseException:
            for process in (decoder, encoder):
                if process is not None and process.poll() is None: process.kill()
            raise
        finally:
            stopped.set()
            if watcher: watcher.join(timeout=2)
            for process in (decoder, encoder):
                if process is not None:
                    if process.poll() is None: process.kill()
                    process.wait()
                    if process.stdout: process.stdout.close()
                    if process.stdin: process.stdin.close()
        if progress: progress(100, "360 movement complete")
