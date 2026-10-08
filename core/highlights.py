"""Cached musical novelty analysis. Mix events never claim instrument identity."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np

VERSION = 1

def analyze_samples(y, sr: int, start: float = 0.0) -> list[dict]:
    """Compare one-second texture and attack patterns with their local baseline."""
    y = np.asarray(y, dtype=np.float32)
    rows = []
    previous = None
    for index in range(0, len(y), sr):
        chunk = y[index:index + sr]
        if len(chunk) < sr // 4:
            break
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        spectrum = np.abs(np.fft.rfft(chunk * np.hanning(len(chunk))))
        spectrum = np.array([part.sum() for part in np.array_split(spectrum, 32)])
        spectrum /= max(float(spectrum.sum()), 1e-9)
        novelty = float(np.abs(spectrum - previous).sum()) if previous is not None else 0.0
        previous = spectrum
        envelope = np.array([float(np.sqrt(np.mean(part ** 2))) for part in np.array_split(chunk, 20)])
        attacks = float(np.maximum(np.diff(envelope), 0).sum())
        rows.append((rms, novelty, attacks))
    if not rows:
        return []
    values = np.asarray(rows)
    events = []
    for index, row in enumerate(values):
        local = values[max(0, index - 12):min(len(values), index + 13)]
        baseline = np.median(local, axis=0)
        spread = np.maximum(np.percentile(local, 85, axis=0) - baseline, [0.005, 0.04, 0.01])
        deviations = np.maximum((row - baseline) / spread, 0)
        score = float(np.clip(.25 * deviations[0] + .45 * deviations[1] + .30 * deviations[2], 0, 1))
        if row[0] < .003:
            score = 0.0
        events.append({"start_sec": round(start + index, 3), "end_sec": round(start + min(index + 1, len(y) / sr), 3),
                       "score": round(score, 4), "rms": round(float(row[0]), 6),
                       "kind": "musical_change", "instrument": None})
    return events

def analyze_file(path: str, cache: Path, start: float = 0, duration: float | None = None) -> list[dict]:
    from core.ffmpeg import tool_status
    import subprocess
    stat = Path(path).stat()
    signature = [VERSION, "activity-v2", str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns, start, duration]
    try:
        payload = json.loads(cache.read_text())
        if payload.get("signature") == signature:
            return payload["events"]
    except (OSError, ValueError, KeyError):
        pass
    command = [tool_status()["ffmpeg_path"], "-v", "error", "-ss", str(start), "-i", path]
    if duration is not None:
        command += ["-t", str(duration)]
    command += ["-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"]
    result = subprocess.run(command, capture_output=True, check=True, timeout=180)
    events = analyze_samples(np.frombuffer(result.stdout, dtype="<f4"), 8000, start)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix(".tmp")
    temporary.write_text(json.dumps({"signature": signature, "events": events}))
    temporary.replace(cache)
    return events

def best_start(events: list[dict], duration: float, excerpt: float) -> float:
    if excerpt >= duration or not events:
        return 0.0
    # Prefix sums avoid scanning the complete song for each possible excerpt.
    scores = np.array([row["score"] for row in events])
    prefix = np.concatenate(([0.0], np.cumsum(scores)))
    count = max(1, int(round(excerpt)))
    candidates = range(max(1, int(duration - excerpt) + 1))
    winner = max(candidates, key=lambda index: float(prefix[min(len(scores), index + count)] - prefix[min(len(scores), index)]))
    return min(float(winner), duration - excerpt)

def instrument_events(stems: dict, sr: int, start: float = 0) -> list[dict]:
    """Estimate vocal activity and unusually prominent/novel separated instruments."""
    aliases = {"vocals": "singer", "drums": "drummer", "guitar": "guitarist", "piano": "pianist", "bass": "bassist"}
    energy = {}
    novelty = {}
    for name, samples in stems.items():
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim == 2:
            samples = samples.mean(axis=0)
        energy[name] = np.array([float(np.sqrt(np.mean(samples[i:i+sr] ** 2))) for i in range(0, len(samples), sr)])
        novelty[name] = analyze_samples(samples, sr)
    if not energy:
        return []
    count = min(len(values) for values in energy.values())
    total = sum(values[:count] ** 2 for values in energy.values())
    events = []
    for name, role in aliases.items():
        if name not in energy:
            continue
        values = energy[name][:count]
        relative = values ** 2 / np.maximum(total, 1e-9)
        threshold = max(.008, float(np.percentile(values, 70)) * .28)
        for index in range(count):
            if values[index] < threshold:
                continue
            baseline = float(np.median(relative[max(0, index-16):min(count, index+17)]))
            score = novelty[name][index]['score'] if index < len(novelty[name]) else 0
            if name == 'vocals':
                active = relative[index] >= .10
                kind = 'vocal_activity'
                confidence = min(1, relative[index] * 2)
            else:
                active = relative[index] > max(.22, baseline * 1.6) and score > .4
                kind = 'possible_fill' if name == 'drums' else 'possible_solo'
                confidence = min(1, relative[index] + score * .5)
            if active:
                events.append({'start_sec': start+index, 'end_sec': start+index+1, 'kind': kind, 'instrument': role, 'score': round(float(confidence), 4), 'confidence': round(float(confidence), 4)})
    return events


def analyze_instruments(path: str, cache: Path, models: Path, start: float, duration: float, progress) -> list[dict]:
    """Opt-in six-source separation, chunked and cached; never used by Whisper."""
    import subprocess
    from core.ffmpeg import tool_status
    stat = Path(path).stat()
    signature = [VERSION, 'htdemucs_6s', str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns, start, duration]
    try:
        payload = json.loads(cache.read_text())
        if payload.get('signature') == signature:
            progress(100, 'Instrument highlights cached')
            return payload['events']
    except (OSError, ValueError, KeyError):
        pass
    progress(0, 'Preparing six-source instrument model (first use downloads the model)')
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    models.mkdir(parents=True, exist_ok=True)
    # Torch's hub is redirected to the selected external application data root.
    torch.hub.set_dir(str(models))
    model = get_model('htdemucs_6s')
    model.eval()
    sr = model.samplerate
    chunk_duration = 6
    count = max(1, int(np.ceil(duration / chunk_duration)))
    energies = {name: [] for name in model.sources}
    # Retain only 8kHz analysis signals, not six full-resolution songs or WAV files.
    for index in range(count):
        progress(100 * index / count, f'Separating instruments {index+1}/{count}')
        seconds = min(chunk_duration, duration-index*chunk_duration)
        command = [tool_status()['ffmpeg_path'], '-v', 'error', '-ss', str(start+index*chunk_duration), '-i', path, '-t', str(seconds), '-vn', '-ar', str(sr), '-ac', '2', '-f', 'f32le', 'pipe:1']
        result = subprocess.run(command, capture_output=True, check=True, timeout=60)
        mix = np.frombuffer(result.stdout, dtype='<f4').copy().reshape(-1, 2).T
        if mix.shape[1] == 0:
            break
        tensor = torch.from_numpy(mix)
        reference = tensor.mean(0)
        mean, std = reference.mean(), reference.std().clamp_min(1e-5)
        with torch.no_grad():
            separated = apply_model(model, ((tensor-mean)/std)[None], shifts=0, split=True, overlap=.1, progress=False)[0] * std + mean
        from scipy.signal import resample_poly
        for name, signal in zip(model.sources, separated):
            energies[name].append(resample_poly(signal.mean(0).cpu().numpy(), 80, 441))
    stems = {name: np.concatenate(parts) for name, parts in energies.items() if parts}
    events = instrument_events(stems, 8000, start)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix('.tmp')
    temporary.write_text(json.dumps({'signature': signature, 'events': events}))
    temporary.replace(cache)
    progress(100, 'Instrument highlights ready')
    return events


def visual_quality(path: str, duration: float, cache: Path, cancel=lambda: None, progress=lambda percent: None) -> list[dict]:
    """Sparse image-quality samples penalize black or badly blurred excerpts."""
    import cv2
    stat = Path(path).stat()
    signature = [VERSION, str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns, duration]
    try:
        payload = json.loads(cache.read_text())
        if payload.get('signature') == signature:
            progress(100)
            return payload['samples']
    except (OSError, ValueError, KeyError):
        pass
    capture = cv2.VideoCapture(path)
    samples = []
    try:
        times = np.linspace(0, max(0, duration-.05), min(120, max(2, int(duration/5)+1)))
        progress(0)
        for index, second in enumerate(times):
            cancel()
            capture.set(cv2.CAP_PROP_POS_MSEC, float(second)*1000)
            ok, frame = capture.read()
            progress(100 * (index + 1) / len(times))
            if not ok:
                continue
            gray = cv2.cvtColor(cv2.resize(frame, (320,180)), cv2.COLOR_BGR2GRAY)
            brightness = float(gray.mean())
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            quality = min(1, sharpness/100) if 8 < brightness < 245 else 0
            samples.append({'time_sec': round(float(second),3), 'quality': round(quality,4)})
    finally:
        capture.release()
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix('.tmp')
    temporary.write_text(json.dumps({'signature': signature, 'samples': samples}))
    temporary.replace(cache)
    return samples
