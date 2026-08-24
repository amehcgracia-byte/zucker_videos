"""Lightweight local audio-content classification for Backstage clips."""

from __future__ import annotations

from functools import lru_cache
from typing import Any


@lru_cache(maxsize=8)
def _load_audio(path: str, mtime_ns: int) -> tuple[Any, int]:
    import librosa
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return librosa.load(path, sr=11025, mono=True)


def detect_music_window(path: str, start_sec: float, duration_sec: float) -> dict[str, Any]:
    """Estimate whether a clip window contains musical material.

    This is deliberately a conservative signal, not a source-separation
    claim. Harmonic energy, chroma movement and onset activity together are
    useful for distinguishing a music bed/instrument from speech and room
    ambience. The result is persisted so the eventual LLM pass can audit it.
    """
    try:
        import numpy as np

        import librosa

        stat = __import__("os").stat(path)
        full, sr = _load_audio(path, int(stat.st_mtime_ns))
        begin = max(0, int(max(0.0, float(start_sec)) * sr))
        end = min(len(full), begin + max(1, int(max(0.5, float(duration_sec)) * sr)))
        y = full[begin:end]
        if y.size < sr // 2:
            return {"music_present": False, "music_score": 0.0, "reason": "too_short"}
        rms = float(np.sqrt(np.mean(np.square(y))))
        if rms < 0.004:
            return {"music_present": False, "music_score": 0.0, "reason": "quiet"}
        spectrum = np.abs(librosa.stft(y, n_fft=1024, hop_length=256))
        flatness = float(np.mean(librosa.feature.spectral_flatness(S=spectrum)))
        harmonic_ratio = max(0.0, min(1.0, 1.0 - flatness * 12.0))
        chroma = librosa.feature.chroma_stft(S=spectrum, sr=sr)
        chroma_motion = float(np.mean(np.std(chroma, axis=1)))
        onset = librosa.onset.onset_strength(y=y, sr=sr)
        onset_activity = float(np.mean(onset)) if onset.size else 0.0
        periodicity = 0.0
        if onset.size > 8:
            centered = onset - float(np.mean(onset))
            correlation = np.correlate(centered, centered, mode="full")[len(centered) - 1:]
            denominator = max(float(correlation[0]), 1e-8)
            lags = [int(sr / 256 * seconds) for seconds in (0.42, 0.50, 0.60, 0.75, 0.90, 1.00)]
            periodicity = max((float(correlation[lag] / denominator) for lag in lags if lag < len(correlation)), default=0.0)
        # Speech can be harmonic, but normally has less sustained chroma
        # movement. Keep the threshold conservative to avoid muting camera
        # speech unnecessarily.
        harmonic_score = max(0.0, min(1.0, (harmonic_ratio - 0.38) / 0.38))
        chroma_score = max(0.0, min(1.0, (chroma_motion - 0.08) / 0.16))
        onset_score = max(0.0, min(1.0, (onset_activity - 0.08) / 0.30))
        periodicity_score = max(0.0, min(1.0, (periodicity - 0.12) / 0.35))
        score = round(0.40 * harmonic_score + 0.25 * chroma_score + 0.20 * periodicity_score + 0.15 * onset_score, 3)
        return {
            "music_present": bool(score >= 0.58),
            "music_score": score,
            "harmonic_ratio": round(harmonic_ratio, 3),
            "chroma_motion": round(chroma_motion, 3),
            "onset_activity": round(onset_activity, 3),
            "periodicity": round(periodicity, 3),
        }
    except Exception as exc:  # pragma: no cover - optional media dependency
        return {"music_present": False, "music_score": 0.0, "reason": f"unavailable: {exc}"}
