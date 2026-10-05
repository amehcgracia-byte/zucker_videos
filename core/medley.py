"""Render independent song highlights with black separation and audio fades."""
from __future__ import annotations
import json
import math
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from core.ffmpeg import ffprobe, tool_status
from core.highlights import analyze_file, best_start, visual_quality

def media_info(path):
    probe = ffprobe(str(path))
    streams = probe.get("streams", [])
    duration = float(probe.get("format", {}).get("duration") or 0)
    return duration, any(s.get("codec_type") == "video" for s in streams), any(s.get("codec_type") == "audio" for s in streams)

def allocate(capacities, content_duration):
    if not math.isfinite(content_duration) or content_duration <= 0:
        raise ValueError("Medley duration must be positive")
    if content_duration > sum(capacities) + .05:
        raise ValueError("The requested duration exceeds the available source footage")
    remaining = content_duration
    lengths = [0.0] * len(capacities)
    pending = set(range(len(capacities)))
    while pending:
        share = remaining / len(pending)
        saturated = {i for i in pending if capacities[i] <= share}
        if not saturated:
            for i in pending:
                lengths[i] = share
            break
        for i in saturated:
            lengths[i] = capacities[i]
            remaining -= capacities[i]
        pending -= saturated
    if any(value < 1 / 30 for value in lengths):
        raise ValueError("Choose at least one video frame per song, plus the black gaps")
    return lengths

def _run(command, duration, progress, cancel):
    """Read measured FFmpeg timestamps while retaining responsive cancellation."""
    messages = queue.Queue()
    with tempfile.TemporaryFile(mode="w+b") as errors:
        process = subprocess.Popen(command + ["-progress", "pipe:1", "-nostats"], stdout=subprocess.PIPE, stderr=errors, text=True)
        def read():
            for line in process.stdout:
                messages.put(line)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            while process.poll() is None or not messages.empty():
                cancel()
                try:
                    line = messages.get(timeout=.15)
                except queue.Empty:
                    continue
                if line.startswith("out_time_us="):
                    try:
                        timestamp = float(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    progress(min(99, max(0, timestamp / 1e6 / max(duration, .001) * 100)))
            if process.returncode:
                errors.seek(0)
                raise RuntimeError(errors.read().decode(errors="replace")[-3000:])
            progress(100)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            reader.join(timeout=2)
            process.stdout.close()

def render(project, entries, duration, gap, fade, progress, cancel):
    if not entries:
        raise ValueError("Add at least one video")
    if not all(math.isfinite(float(v)) and float(v) >= 0 for v in [gap, fade]):
        raise ValueError("Black gap and fade must be finite, positive durations")
    records = []
    for index, entry in enumerate(entries):
        cancel()
        source = str(Path(entry["video"]).resolve())
        seconds, video, embedded = media_info(source)
        audio = str(entry.get("audio") or source)
        if not video or seconds <= 0:
            raise ValueError("Medley needs a readable video: " + source)
        if audio != source:
            audio_seconds, _, has_audio = media_info(audio)
            seconds = min(seconds, audio_seconds)
        else:
            has_audio = embedded
        if not has_audio:
            raise ValueError("Choose an audio file for: " + Path(source).name)
        records.append({"video": source, "audio": audio, "available": seconds})
        progress(5 * (index + 1) / len(entries), "inspect", index, "Checking source duration and audio")
    lengths = allocate([r["available"] for r in records], float(duration) - gap * (len(records) - 1))
    for index, (record, length) in enumerate(zip(records, lengths)):
        cancel()
        progress(5 + 15 * index / len(records), "highlights", index, "Analyzing musical changes")
        events = analyze_file(record["audio"], project.cache_dir / "medley" / f"highlights-{index}.json", duration=record["available"])
        pictures = visual_quality(record["video"], record["available"], project.cache_dir / "medley" / f"visual-{index}.json", cancel)
        if pictures:
            for event in events:
                image = min(pictures, key=lambda row: abs(row["time_sec"]-event["start_sec"]))
                event["score"] = .8 * event["score"] + .2 * image["quality"]
        record.update(start=best_start(events, record["available"], length), duration=length)
    project.exports_dir.mkdir(parents=True, exist_ok=True)
    destination = project.exports_dir / f"Medley-Populi-{time.time_ns()}.mp4"
    ffmpeg = str(tool_status()["ffmpeg_path"])
    # Every chunk shares encoding, layout and sample rate, so final assembly copies streams.
    encoding = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "2", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "15360", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    with tempfile.TemporaryDirectory(prefix="medley-", dir=project.cache_dir) as temporary:
        root = Path(temporary)
        pieces = []
        units = len(records) + (len(records) - 1 if gap > 0 else 0) + 1
        completed = 0
        def encode(args, seconds, output, label):
            nonlocal completed
            command = [ffmpeg, "-y", "-v", "error"] + args + encoding + [str(output)]
            _run(command, seconds, lambda value: progress(20 + 80 * (completed + value / 100) / units, "render", completed, label, value), cancel)
            completed += 1
            pieces.append(output)
        for index, record in enumerate(records):
            seconds = record["duration"]
            f = min(float(fade), seconds / 2)
            vf = f"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,fade=t=in:st=0:d={f},fade=t=out:st={seconds-f}:d={f}" if f else "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
            af = f"asetpts=PTS-STARTPTS,afade=t=in:st=0:d={f},afade=t=out:st={seconds-f}:d={f},apad" if f else "asetpts=PTS-STARTPTS,apad"
            args = ["-ss", str(record["start"]), "-i", record["video"]]
            audio_index = 0
            if record["audio"] != record["video"]:
                args += ["-ss", str(record["start"]), "-i", record["audio"]]
                audio_index = 1
            args += ["-map", "0:v:0", "-map", f"{audio_index}:a:0", "-vf", vf, "-af", af, "-t", str(seconds)]
            encode(args, seconds, root / f"song-{index}.mp4", f"Rendering song {index+1}/{len(records)}")
            if gap > 0 and index < len(records) - 1:
                encode(["-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=30", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(gap)], gap, root / f"gap-{index}.mp4", "Rendering black separation")
        listing = root / "concat.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in pieces))
        final = root / "complete.mp4"
        _run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "1", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(final)], duration, lambda value: progress(20 + 80 * (completed + value / 100) / units, "assemble", 0, "Assembling and validating Medley", value), cancel)
        actual, has_video, has_audio = media_info(final)
        if not has_video or not has_audio or abs(actual - duration) > max(.5, .08 * len(records)):
            raise RuntimeError("Medley validation failed; no incomplete video was published")
        cancel()
        final.replace(destination)
    manifest = {"platform": "medley", "exports": [{"path": str(destination), "platform": "medley", "duration_sec": actual}], "songs": records, "black_gap_sec": gap, "fade_sec": fade}
    (project.artifacts_dir / "export_manifest.json").write_text(json.dumps(manifest, indent=2))
    return destination, manifest
