"""
Voice-to-MIDI pipeline orchestration.

Entry points
------------
process_audio(wav_path, ...)  — full pipeline, returns ProcessResult
detect_bpm(wav_path)          — onset-based BPM estimation
detect_key(notes)             — chromagram key detection

handle_record_button(...)     — factory for GUI integration: starts a Recorder
                                and wires it to process_audio() in a daemon thread.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional, TypedDict

from recorder import Recorder
from pitch    import detect_pitch
from notes    import frames_to_notes, notes_to_pattern, NoteSegment
from midi     import notes_to_midi


class ProcessResult(TypedDict):
    midi_path: str
    notes:     list[NoteSegment]    # raw NoteSegments (seconds)
    pattern:   list[dict]           # bar-based, quantized — ready for build_events()
    tempo:     int
    key:       Optional[str]        # e.g. "C major", or None


# ── main pipeline ─────────────────────────────────────────────────────────────

def process_audio(
    wav_path: str,
    *,
    tempo: Optional[int]            = None,
    backend: str                    = "auto",
    quantize_grid: float            = 0.0625,   # 16th-note grid
    on_progress: Optional[Callable[[str], None]] = None,
) -> ProcessResult:
    """
    Run the full voice-to-MIDI pipeline on a WAV file.

    Parameters
    ----------
    wav_path      : path to the recorded WAV
    tempo         : BPM override; auto-detected via detect_bpm() if None
    backend       : "auto" | "crepe" | "pyin" — passed to pitch.detect_pitch()
    quantize_grid : bar-fraction grid for quantize_melody_pattern()
    on_progress   : optional status callback (called from the calling thread)

    Returns
    -------
    ProcessResult
    """
    def _prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    _prog("Detecting pitch…")
    frames = detect_pitch(wav_path, backend=backend)

    if tempo is None:
        _prog("Estimating BPM…")
        tempo = detect_bpm(wav_path) or 120

    _prog("Segmenting notes…")
    raw_notes = frames_to_notes(frames)

    if not raw_notes:
        raise ValueError(
            "No pitched content detected. Try humming louder or for longer."
        )

    _prog("Converting to bar pattern…")
    pattern = notes_to_pattern(raw_notes, tempo)

    _prog("Quantizing…")
    from music_api import quantize_melody_pattern   # reuse existing utility
    pattern = quantize_melody_pattern(pattern, grid_bars=quantize_grid)

    _prog("Detecting key…")
    key = detect_key(raw_notes)

    _prog("Writing MIDI…")
    midi_path = notes_to_midi(raw_notes, tempo)

    return ProcessResult(
        midi_path=midi_path,
        notes=raw_notes,
        pattern=pattern,
        tempo=tempo,
        key=key,
    )


# ── bonus: BPM detection ──────────────────────────────────────────────────────

def detect_bpm(wav_path: str) -> Optional[int]:
    """
    Estimate BPM from onset strength via librosa.beat.beat_track.

    Returns nearest integer BPM clamped to 60–200, or None on failure.
    """
    try:
        import librosa
        import numpy as np

        audio, sr = librosa.load(wav_path, sr=None, mono=True)
        tempo, _  = librosa.beat.beat_track(y=audio, sr=sr)
        bpm = int(round(float(np.atleast_1d(tempo)[0])))
        return max(60, min(200, bpm))
    except Exception:
        return None


# ── bonus: key detection ──────────────────────────────────────────────────────

def detect_key(notes: list[NoteSegment]) -> Optional[str]:
    """
    Detect the musical key via a Krumhansl-Schmuckler style pitch-class
    histogram correlation.  No extra library required beyond numpy.

    Returns a string like "C major" or "A minor", or None on failure.
    """
    try:
        import numpy as np

        _MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                           2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        _MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                           2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
        _NAMES = ["C", "C#", "D", "D#", "E", "F",
                  "F#", "G", "G#", "A", "A#", "B"]

        hist = np.zeros(12)
        for n in notes:
            hist[int(n["pitch"]) % 12] += float(n["duration"])

        if hist.sum() == 0:
            return None

        hist = hist / hist.sum()

        best_corr = -2.0
        best_key  = "C major"

        for root in range(12):
            for mode, profile in [("major", _MAJOR), ("minor", _MINOR)]:
                rotated = np.roll(profile, root)
                corr    = float(np.corrcoef(hist, rotated / rotated.sum())[0, 1])
                if corr > best_corr:
                    best_corr = corr
                    best_key  = f"{_NAMES[root]} {mode}"

        return best_key
    except Exception:
        return None


# ── GUI integration factory ───────────────────────────────────────────────────

def handle_record_button(
    on_status: Callable[[str], None],
    on_result: Callable[[ProcessResult], None],
    on_error:  Callable[[str], None],
    *,
    tempo: Optional[int] = None,
) -> Recorder:
    """
    Create and start a Recorder for the GUI melody-record button.

    When the user clicks Stop, the caller calls recorder.stop().
    This fires on_complete → spawns a daemon thread → calls process_audio().

    Threading contract
    ------------------
    on_status / on_result / on_error are invoked from a background thread.
    The GUI must dispatch these to the main thread before touching Tkinter
    widgets (e.g. via ui_q.put or root.after).

    Returns the active Recorder so the caller can call recorder.stop().
    """
    def _on_complete(wav_path: str) -> None:
        def _run() -> None:
            try:
                result = process_audio(
                    wav_path,
                    tempo=tempo,
                    on_progress=on_status,
                )
                on_result(result)
            except Exception as exc:
                on_error(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    recorder = Recorder(on_complete=_on_complete)
    recorder.start()
    return recorder


# ── CLI convenience ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python main.py <wav_file> [tempo_bpm]")
        sys.exit(1)

    wav  = sys.argv[1]
    bpm  = int(sys.argv[2]) if len(sys.argv) > 2 else None

    def _progress(msg: str) -> None:
        print(f"  {msg}")

    print(f"Processing: {wav}")
    result = process_audio(wav, tempo=bpm, backend="auto", on_progress=_progress)
    print(f"\nDone!")
    print(f"  MIDI: {result['midi_path']}")
    print(f"  Notes: {len(result['notes'])}")
    print(f"  Tempo: {result['tempo']} BPM")
    print(f"  Key: {result['key']}")
    import json
    print("\nDetected notes:")
    for n in result["notes"]:
        print(f"  {json.dumps(n)}")
