"""
Convert a sequence of PitchFrames into quantized note segments.

Algorithm:
  1. Median-filter the frequency track to suppress transient noise.
  2. Convert non-zero Hz → float MIDI (69 + 12 * log2(f / 440)).
  3. Round to nearest semitone for final pitch.
  4. Merge consecutive frames with the same rounded pitch into segments,
     starting a new segment whenever the float MIDI changes by ≥ 0.5 semitones
     OR the frame becomes unvoiced.
  5. Drop segments shorter than MIN_NOTE_DURATION or outside MIDI range.
  6. Derive velocity from mean confidence of the segment.
"""
from __future__ import annotations

from typing import TypedDict

import numpy as np
from scipy.signal import medfilt

from pitch import PitchFrame

MEDIAN_FILTER_K   = 5      # odd; 5 × 10 ms = 50 ms smoothing
MIN_NOTE_DURATION = 0.08   # seconds — discard notes < 80 ms
MIN_MIDI_NOTE     = 40     # E2 — reject implausibly low detections
MAX_MIDI_NOTE     = 84     # C6 — reject implausibly high detections
PITCH_CHANGE_SEMI = 0.5    # half-semitone float delta triggers a new note


class NoteSegment(TypedDict):
    pitch:    int    # MIDI note number 0–127
    start:    float  # seconds from audio start
    duration: float  # seconds
    velocity: int    # 1–127


def frames_to_notes(
    frames: list[PitchFrame],
    *,
    median_k: int   = MEDIAN_FILTER_K,
    min_dur:  float = MIN_NOTE_DURATION,
) -> list[NoteSegment]:
    """
    Convert PitchFrames to note segments.

    Parameters
    ----------
    frames   : output of pitch.detect_pitch()
    median_k : median filter window in frames (must be odd)
    min_dur  : minimum note duration in seconds

    Returns
    -------
    list[NoteSegment]
    """
    if not frames:
        return []

    freqs = np.array([f.frequency  for f in frames], dtype=np.float64)
    confs = np.array([f.confidence for f in frames], dtype=np.float64)
    voiced_mask = freqs > 0

    if voiced_mask.sum() < 3:
        return []

    # Median-filter only the voiced frames to preserve silence gaps
    smoothed = freqs.copy()
    voiced_idx = np.where(voiced_mask)[0]
    k = min(median_k, len(voiced_idx))
    if k % 2 == 0:
        k = max(1, k - 1)
    smoothed[voiced_idx] = medfilt(freqs[voiced_idx], kernel_size=k)

    # Hz → float MIDI (unvoiced stays 0.0)
    midi_floats = np.zeros_like(smoothed)
    midi_floats[voiced_idx] = 69.0 + 12.0 * np.log2(
        np.maximum(smoothed[voiced_idx], 1e-12) / 440.0
    )

    midi_int = np.round(midi_floats).astype(int)

    segments = _segment(frames, midi_floats, midi_int, confs, min_dur)
    return [s for s in segments if MIN_MIDI_NOTE <= s["pitch"] <= MAX_MIDI_NOTE]


def notes_to_pattern(
    notes: list[NoteSegment],
    tempo: int,
) -> list[dict]:
    """
    Convert NoteSegments to the bar-based pattern format expected by
    build_events() in midi_scheduler.py and quantize_melody_pattern()
    in music_api.py.

    Each dict: {"note": int_midi, "duration": float_bars}
    """
    bar_s = 60.0 / tempo * 4.0
    return [
        {"note": n["pitch"], "duration": round(n["duration"] / bar_s, 6)}
        for n in notes
    ]


def _segment(
    frames:      list[PitchFrame],
    midi_floats: np.ndarray,
    midi_int:    np.ndarray,
    confs:       np.ndarray,
    min_dur:     float,
) -> list[NoteSegment]:
    segments: list[NoteSegment] = []
    n = len(frames)
    if n == 0:
        return segments

    seg_pitch = int(midi_int[0])
    seg_start = float(frames[0].time)
    seg_confs: list[float] = [float(confs[0])]

    for i in range(1, n):
        pitch_changed   = abs(float(midi_floats[i]) - float(midi_floats[i - 1])) >= PITCH_CHANGE_SEMI
        become_unvoiced = int(midi_int[i]) == 0 and int(midi_int[i - 1]) != 0

        if pitch_changed or become_unvoiced:
            dur = float(frames[i].time) - seg_start
            if dur >= min_dur and seg_pitch > 0:
                segments.append(NoteSegment(
                    pitch=seg_pitch,
                    start=seg_start,
                    duration=dur,
                    velocity=_confidence_to_velocity(float(np.mean(seg_confs))),
                ))
            seg_pitch = int(midi_int[i])
            seg_start = float(frames[i].time)
            seg_confs = [float(confs[i])]
        else:
            seg_confs.append(float(confs[i]))

    # Close the final segment
    dur = float(frames[-1].time) - seg_start
    if dur >= min_dur and seg_pitch > 0:
        segments.append(NoteSegment(
            pitch=seg_pitch,
            start=seg_start,
            duration=dur,
            velocity=_confidence_to_velocity(float(np.mean(seg_confs))),
        ))

    return segments


def _confidence_to_velocity(conf: float) -> int:
    """Map confidence 0–1 → velocity 60–110."""
    return max(1, min(127, int(60 + conf * 50)))
