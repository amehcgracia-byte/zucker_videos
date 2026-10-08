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
import numpy as np
from core.ffmpeg import ffprobe, tool_status
from core.highlights import analyze_file, visual_quality

def select_highlights(events, pictures, available, length):
    """Select up to three disjoint, chronological excerpts, preserving the budget.

    Score complete windows rather than isolated attacks. Image quality gates
    musical novelty so black/blurred footage cannot win just by being louder.
    Very short budgets retain one coherent excerpt instead of tiny fragments.
    """
    count = min(3, max(1, int(length // 6)))
    seconds = length / count
    size = max(1, int(np.ceil(available)))
    novelty = np.zeros(size)
    activity = np.ones(size)
    for event in events:
        index = min(size - 1, max(0, int(event["start_sec"])))
        novelty[index] = max(novelty[index], float(event["score"]))
        if "rms" in event:
            activity[index] = min(1, max(0, float(event["rms"]) / .02))
    if pictures:
        rows = sorted(pictures, key=lambda row: row["time_sec"])
        quality = np.interp(np.arange(size) + .5,
                            [row["time_sec"] for row in rows],
                            [row["quality"] for row in rows])
    else:
        quality = np.ones(size)
    values = activity * quality * (.85 * novelty + .15)
    prefix = np.concatenate(([0.0], np.cumsum(values)))
    timeline = np.arange(size + 1)
    def integral(second):
        return float(np.interp(second, timeline, prefix))
    def score(start):
        return (integral(start + seconds) - integral(start)) / seconds
    # Keep enough footage on each side to fit the remaining excerpts. This
    # avoids greedy choices stranding unused duration on short sources.
    selected = []
    free = [(0.0, float(available))]
    for remaining in range(count, 0, -1):
        candidates = []
        for left, right in free:
            if right - left + 1e-6 < seconds:
                continue
            starts = {left, max(left, right - seconds)}
            starts.update(float(t) for t in range(int(np.ceil(left)), int(np.floor(right - seconds)) + 1))
            # Capacity boundaries matter when the selected content nearly fills
            # a source; integer timestamps alone would prevent exact allocation.
            starts.update(left + k * seconds for k in range(remaining)
                          if left + (k + 1) * seconds <= right + 1e-6)
            for start in starts:
                spaces = [(a, b) for a, b in free if (a, b) != (left, right)]
                spaces += [(left, start), (start + seconds, right)]
                capacity = sum(int((b - a + 1e-6) // seconds) for a, b in spaces)
                if capacity >= remaining - 1:
                    distance = min((max(start - (row["start"] + seconds),
                                        row["start"] - (start + seconds), 0)
                                    for row in selected), default=seconds)
                    diversity = .5 + .5 * min(1, distance / seconds)
                    candidates.append((score(start) * diversity, start, spaces))
        if not candidates:
            raise ValueError("Unable to fit non-overlapping Medley highlights")
        _, start, free = max(candidates, key=lambda row: (row[0], -row[1]))
        selected.append({"start": start, "duration": seconds, "score": score(start)})
    return sorted(selected, key=lambda row: row["start"])

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
            if not has_audio:
                raise ValueError("The selected external file has no audio: " + Path(audio).name)
        else:
            has_audio = embedded
        records.append({"video": source, "audio": audio, "has_audio": has_audio, "available": seconds})
        progress(5 * (index + 1) / len(entries), "inspect", index, f"Checked {Path(source).name}: {'original/external audio' if has_audio else 'silent video'}", 100)
    lengths = allocate([r["available"] for r in records], float(duration) - gap * (len(records) - 1))
    for index, (record, length) in enumerate(zip(records, lengths)):
        cancel()
        base = 5 + 15 * index / len(records)
        progress(base, "music", index, f"Finding musical highlights: {Path(record['video']).name}")
        events = analyze_file(record["audio"], project.cache_dir / "medley" / f"highlights-{index}.json", duration=record["available"]) if record["has_audio"] else []
        progress(base + 6 / len(records), "music", index, "Musical highlights ready" if record["has_audio"] else "No audio: selecting visual highlights", 100)
        pictures = visual_quality(record["video"], record["available"], project.cache_dir / "medley" / f"visual-{index}.json", cancel,
            progress=lambda percent: progress(base + (6 + 9 * percent / 100) / len(records), "visual", index, f"Checking visual highlights: {Path(record['video']).name}", percent))
        if not record["has_audio"]:
            events = [{"start_sec": row["time_sec"], "score": row["quality"]} for row in pictures]
        highlights = select_highlights(events, pictures, record["available"], length)
        record.update(start=highlights[0]["start"], duration=length, highlights=highlights)
    project.exports_dir.mkdir(parents=True, exist_ok=True)
    destination = project.exports_dir / f"Medley-Populi-{time.time_ns()}.mp4"
    ffmpeg = str(tool_status()["ffmpeg_path"])
    # Every chunk shares encoding, layout and sample rate, so final assembly copies streams.
    encoding = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "2", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "15360", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    with tempfile.TemporaryDirectory(prefix="medley-", dir=project.cache_dir) as temporary:
        root = Path(temporary)
        pieces = []
        units = sum(len(row["highlights"]) for row in records) + (len(records) - 1 if gap > 0 else 0) + 1
        completed = 0
        def encode(args, seconds, output, label):
            nonlocal completed
            command = [ffmpeg, "-y", "-v", "error"] + args + encoding + [str(output)]
            _run(command, seconds, lambda value: progress(20 + 80 * (completed + value / 100) / units, "render", completed, label, value), cancel)
            completed += 1
            pieces.append(output)
        for index, record in enumerate(records):
            highlights = record["highlights"]
            for highlight_index, highlight in enumerate(highlights):
                seconds = highlight["duration"]
                f = min(float(fade), seconds / 2)
                vf = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
                af = "asetpts=PTS-STARTPTS"
                # Internal highlights have hard cuts. Only song boundaries fade
                # to/from black and silence, including the first/last song.
                if f and highlight_index == 0:
                    vf += f",fade=t=in:st=0:d={f}"
                    af += f",afade=t=in:st=0:d={f}"
                if f and highlight_index == len(highlights) - 1:
                    vf += f",fade=t=out:st={seconds-f}:d={f}"
                    af += f",afade=t=out:st={seconds-f}:d={f}"
                af += ",apad"
                args = ["-ss", str(highlight["start"]), "-i", record["video"]]
                audio_index = 0
                if not record["has_audio"]:
                    args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                    audio_index = 1
                elif record["audio"] != record["video"]:
                    args += ["-ss", str(highlight["start"]), "-i", record["audio"]]
                    audio_index = 1
                args += ["-map", "0:v:0", "-map", f"{audio_index}:a:0", "-vf", vf, "-af", af, "-t", str(seconds)]
                encode(args, seconds, root / f"song-{index}-highlight-{highlight_index}.mp4",
                       f"Rendering song {index+1}/{len(records)} · highlight {highlight_index+1}/{len(highlights)}")
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
    manifest = {"platform": "medley", "exports": [{"path": str(destination), "platform": "medley", "duration_sec": actual}], "songs": records, "black_gap_sec": gap, "fade_sec": fade, "warnings": ["Silent excerpt: " + Path(row["video"]).name for row in records if not row["has_audio"]]}
    (project.artifacts_dir / "export_manifest.json").write_text(json.dumps(manifest, indent=2))
    return destination, manifest
