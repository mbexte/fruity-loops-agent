"""
Export a list of NoteSegments to a Standard MIDI File (SMF type 0).

Uses pretty_midi for clean absolute-time construction.
Falls back to mido if pretty_midi is not installed.

Output directory priority:
  1. FL_STUDIO_MIDI_OUT environment variable (set to any path you prefer)
  2. ~/Documents/FL Studio/Projects/Scores  (FL Studio default Scores folder)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notes import NoteSegment

MIDI_FILENAME = "melody.mid"

_DEFAULT_FL_SCORES = (
    Path.home() / "Documents" / "FL Studio" / "Projects" / "Scores"
)


def _fl_output_dir() -> Path:
    env = os.environ.get("FL_STUDIO_MIDI_OUT")
    if env:
        return Path(env)
    return _DEFAULT_FL_SCORES


def notes_to_midi(
    notes: list["NoteSegment"],
    tempo: int,
    *,
    out_path: str | None = None,
) -> str:
    """
    Write NoteSegments to a MIDI file.

    Parameters
    ----------
    notes    : list of NoteSegment dicts from notes.frames_to_notes()
    tempo    : BPM used for the tempo track meta-event
    out_path : explicit destination; if None, uses FL_STUDIO_MIDI_OUT / melody.mid

    Returns
    -------
    Absolute path of the saved .mid file.
    """
    if out_path is None:
        dest_dir = _fl_output_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(dest_dir / MIDI_FILENAME)

    try:
        return _write_pretty_midi(notes, tempo, out_path)
    except ImportError:
        return _write_mido_fallback(notes, tempo, out_path)


def _write_pretty_midi(
    notes: list["NoteSegment"],
    tempo: int,
    out_path: str,
) -> str:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    instrument = pretty_midi.Instrument(program=0, name="Voice Melody")

    for n in notes:
        note = pretty_midi.Note(
            velocity=int(n["velocity"]),
            pitch=int(n["pitch"]),
            start=float(n["start"]),
            end=float(n["start"]) + float(n["duration"]),
        )
        instrument.notes.append(note)

    pm.instruments.append(instrument)
    pm.write(out_path)
    return out_path


def _write_mido_fallback(
    notes: list["NoteSegment"],
    tempo: int,
    out_path: str,
) -> str:
    """mido fallback: builds absolute-tick event list then converts to delta-time."""
    import mido

    ticks_per_beat = 480
    us_per_beat    = mido.bpm2tempo(tempo)
    secs_per_tick  = (us_per_beat / 1_000_000) / ticks_per_beat

    mid   = mido.MidiFile(type=0, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=us_per_beat, time=0))

    # Build (abs_tick, msg_type, note, velocity) list, sort, then emit delta-time
    events: list[tuple[int, str, int, int]] = []
    for n in notes:
        on_tick  = int(float(n["start"]) / secs_per_tick)
        off_tick = int((float(n["start"]) + float(n["duration"])) / secs_per_tick)
        events.append((on_tick,  "note_on",  int(n["pitch"]), int(n["velocity"])))
        events.append((off_tick, "note_off", int(n["pitch"]), 0))

    events.sort()
    prev_tick = 0
    for abs_tick, msg_type, note, vel in events:
        delta = max(0, abs_tick - prev_tick)
        track.append(mido.Message(msg_type, note=note, velocity=vel, time=delta))
        prev_tick = abs_tick

    mid.save(out_path)
    return out_path
