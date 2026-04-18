"""
Monophonic pitch detection.

Preferred backend: CREPE (deep-learning, more accurate for voice/hum).
Fallback backend:  librosa.pyin (probabilistic YIN, no ML dependency).

Both backends return a list of PitchFrame named tuples with identical fields
so callers (notes.py) are backend-agnostic.

Frames where frequency is 0.0 represent unvoiced / low-confidence regions.
"""
from __future__ import annotations

from typing import NamedTuple

import librosa
import numpy as np

CONF_THRESHOLD_CREPE = 0.50   # CREPE confidence is 0–1
CONF_THRESHOLD_PYIN  = 0.60   # pyin voiced_flag probability


class PitchFrame(NamedTuple):
    time:       float   # seconds from audio start
    frequency:  float   # Hz (0.0 = unvoiced)
    confidence: float   # 0.0–1.0


def detect_pitch(
    wav_path: str,
    *,
    backend: str        = "auto",      # "crepe" | "pyin" | "auto"
    crepe_model: str    = "small",     # tiny/small/medium/large/full
    hop_length_ms: float = 10.0,       # analysis frame hop in milliseconds
    fmin: float         = 80.0,        # minimum expected pitch (vocal low end)
    fmax: float         = 1000.0,      # maximum expected pitch (vocal high end)
) -> list[PitchFrame]:
    """
    Run pitch detection on a mono WAV file.

    Parameters
    ----------
    wav_path      : path to WAV (any sample rate; resampled internally)
    backend       : "auto" tries CREPE first, falls back to pyin on ImportError
    crepe_model   : CREPE model size (speed vs. accuracy trade-off)
    hop_length_ms : hop between analysis frames in milliseconds
    fmin, fmax    : frequency search bounds in Hz

    Returns
    -------
    list[PitchFrame] sorted by time
    """
    audio, sr = librosa.load(wav_path, sr=None, mono=True)

    if backend == "auto":
        backend = _choose_backend()

    if backend == "crepe":
        return _detect_crepe(audio, sr, crepe_model, hop_length_ms)
    else:
        return _detect_pyin(audio, sr, hop_length_ms, fmin, fmax)


def _choose_backend() -> str:
    try:
        import crepe  # noqa: F401
        return "crepe"
    except ImportError:
        return "pyin"


def _detect_crepe(
    audio: np.ndarray,
    sr: int,
    model: str,
    hop_length_ms: float,
) -> list[PitchFrame]:
    import crepe

    # CREPE was trained at 16 kHz; resample if needed
    if sr != 16000:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)

    _, frequency, confidence, _ = crepe.predict(
        audio,
        16000,
        model_capacity=model,
        step_size=hop_length_ms,
        viterbi=True,   # Viterbi smoothing reduces octave-jump artifacts on hummed input
        verbose=0,
    )
    times = np.arange(len(frequency)) * hop_length_ms / 1000.0
    return [
        PitchFrame(
            time=float(t),
            frequency=float(f) if c >= CONF_THRESHOLD_CREPE else 0.0,
            confidence=float(c),
        )
        for t, f, c in zip(times, frequency, confidence)
    ]


def _detect_pyin(
    audio: np.ndarray,
    sr: int,
    hop_length_ms: float,
    fmin: float,
    fmax: float,
) -> list[PitchFrame]:
    hop_length = max(1, int(sr * hop_length_ms / 1000.0))
    f0, voiced_flag, voiced_probs = librosa.pyin(
        audio,
        fmin=fmin,
        fmax=fmax,
        sr=sr,
        hop_length=hop_length,
        fill_na=0.0,
    )
    times = librosa.times_like(f0, sr=sr, hop_length=hop_length)
    return [
        PitchFrame(
            time=float(t),
            frequency=float(f) if (vf and vp >= CONF_THRESHOLD_PYIN) else 0.0,
            confidence=float(vp),
        )
        for t, f, vf, vp in zip(times, f0, voiced_flag, voiced_probs)
    ]
