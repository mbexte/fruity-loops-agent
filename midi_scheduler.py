"""
midi_scheduler.py — DAW-style absolute-time MIDI event scheduler.

Architecture:
  MidiEvent        Immutable, sortable event with absolute time offset (seconds from t0)
  build_events()   Converts a bar-based note list into sorted MidiEvent pairs
  build_song()     Merges multiple named layers into one sorted event list
  run_scheduler()  Dispatches events with sub-ms accuracy (no timing sleeps)
  play_song()      High-level entry: build + schedule + FL Studio recording wrap
  preview_song()   Same as play_song() but no recording control notes

Timing strategy:
  - All event times are absolute offsets from t0 = time.perf_counter() at start.
  - A 20 ms lookahead window pre-arms events before their deadline.
  - Sleep is used ONLY for CPU yield between lookahead windows.
  - A 1 ms spin-wait gives sub-ms dispatch accuracy for each event.
  - Zero timing drift because every dispatch checks against the same t0.
"""

import time
from dataclasses import dataclass, field
from typing import Literal

import mido

# ── constants ─────────────────────────────────────────────────────────────────

PORT_NAME        = "FL Agent"
NOTE_START       = 72     # C5 → arm FL Studio recording
NOTE_STOP        = 74     # D5 → stop FL Studio recording
DEFAULT_CHANNEL  = 0
DEFAULT_VELOCITY = 100
RESERVED_NOTES   = frozenset({NOTE_START, NOTE_STOP})

LOOKAHEAD_S  = 0.020   # 20 ms look-ahead window (compensates for OS jitter)
SPIN_MARGIN  = 0.001   # spin only for the final 1 ms before dispatch


# ── MidiEvent ─────────────────────────────────────────────────────────────────

@dataclass(order=True, frozen=True)
class MidiEvent:
    """Immutable, sortable MIDI event with an absolute time offset from t0."""
    time:     float                                        # seconds from playback start
    type:     Literal["note_on", "note_off"] = field(compare=False)
    note:     int                             = field(compare=False)
    velocity: int                             = field(compare=False)
    channel:  int                             = field(compare=False, default=DEFAULT_CHANNEL)


# ── helpers ───────────────────────────────────────────────────────────────────

def _sanitize(note: int) -> int:
    """Bump reserved notes (72/74) up by one until they are safe."""
    while note in RESERVED_NOTES:
        note += 1
    return note


def _resolve(raw) -> int:
    """Accept an int MIDI number or a note-name string like 'C4', 'F#3'."""
    if isinstance(raw, int):
        return raw
    from music_api import note_to_midi  # lazy — avoids circular import at module level
    return note_to_midi(str(raw))


def _get_port() -> str:
    """Locate the loopMIDI 'FL Agent' output port; exit with an error if absent."""
    import sys
    match = next(
        (p for p in mido.get_output_names() if PORT_NAME.lower() in p.lower()),
        None,
    )
    if match is None:
        print(f"ERROR: MIDI port '{PORT_NAME}' not found.")
        print("Available:", mido.get_output_names() or ["(none)"])
        sys.exit(1)
    return match


def _control(port, note: int) -> None:
    """Immediate note_on + note_off for FL Studio transport control."""
    port.send(mido.Message("note_on",  note=note, velocity=DEFAULT_VELOCITY, channel=DEFAULT_CHANNEL))
    port.send(mido.Message("note_off", note=note, velocity=0,                channel=DEFAULT_CHANNEL))


# ── event builders ────────────────────────────────────────────────────────────

def build_events(
    layer_notes: list[dict],
    tempo: int,
    *,
    channel:  int = DEFAULT_CHANNEL,
    velocity: int = DEFAULT_VELOCITY,
) -> list[MidiEvent]:
    """Convert a bar-based note list into a sorted list of MidiEvent pairs.

    Each note_obj must have:
      "note"     — MIDI int, note-name string, or list of them (chord)
      "duration" — note length in 4/4 bars (float); 1.0 = full bar, 0.5 = half

    Returns events sorted by time (simultaneous events are ordered note_on before note_off).
    """
    bar_s   = 60.0 / tempo * 4       # seconds per 4/4 bar
    events: list[MidiEvent] = []
    cursor  = 0.0

    for note_obj in layer_notes:
        if isinstance(note_obj, dict):
            raw       = note_obj.get("note")
            dur_bars  = float(note_obj.get("duration", 0.25))
        else:
            raw       = note_obj
            dur_bars  = 0.25

        dur_s    = bar_s * dur_bars
        raw_list = raw if isinstance(raw, list) else [raw]
        midi_notes = [_sanitize(_resolve(n)) for n in raw_list]

        for midi_note in midi_notes:
            events.append(MidiEvent(cursor,          "note_on",  midi_note, velocity, channel))
            events.append(MidiEvent(cursor + dur_s,  "note_off", midi_note, 0,        channel))

        cursor += dur_s

    return sorted(events)


def build_song(
    layers: dict[str, list[dict]],
    tempo: int,
    *,
    channel_map:  dict[str, int] | None = None,
    velocity_map: dict[str, int] | None = None,
) -> list[MidiEvent]:
    """Merge multiple named layers into one sorted event list.

    layers       = {"melody": [...], "chords": [...], "bass": [...]}
    channel_map  = {"melody": 0, "chords": 1, "bass": 2}    (optional)
    velocity_map = {"melody": 100, "chords": 80, "bass": 90} (optional)

    All layers start at t=0 — perfect synchronisation.
    """
    ch_map  = channel_map  or {}
    vel_map = velocity_map or {}
    events: list[MidiEvent] = []

    for name, notes in layers.items():
        ch  = ch_map.get(name,  DEFAULT_CHANNEL)
        vel = vel_map.get(name, DEFAULT_VELOCITY)
        events.extend(build_events(notes, tempo, channel=ch, velocity=vel))

    return sorted(events)


# ── scheduler ─────────────────────────────────────────────────────────────────

def run_scheduler(events: list[MidiEvent], port: mido.ports.BaseOutput) -> None:
    """Dispatch MIDI events with sub-millisecond timing accuracy.

    Algorithm:
      1. Record t0 = perf_counter() at entry — this is the global origin.
      2. Loop until all events are dispatched.
      3. If the next event is further than LOOKAHEAD_S away, yield CPU (sleep).
      4. Once inside the lookahead window, sleep until SPIN_MARGIN before the event.
      5. Spin-wait for the final millisecond → sub-ms dispatch accuracy.
      6. Send the MIDI message and advance the index.

    No timing drift: every deadline is computed as t0 + event.time (absolute),
    not as cumulative incremental sleeps.
    """
    if not events:
        return

    t0    = time.perf_counter()
    total = len(events)
    idx   = 0

    while idx < total:
        now        = time.perf_counter() - t0
        target     = events[idx].time
        until_abs  = t0 + target

        # ── CPU yield: sleep until we enter the lookahead window ─────────────
        gap = target - now - LOOKAHEAD_S
        if gap > 0:
            time.sleep(gap)
            continue

        # ── pre-sleep: sleep until SPIN_MARGIN before dispatch ───────────────
        pre_sleep = until_abs - SPIN_MARGIN - time.perf_counter()
        if pre_sleep > 0:
            time.sleep(pre_sleep)

        # ── spin-wait: final millisecond precision ───────────────────────────
        while time.perf_counter() < until_abs:
            pass

        ev = events[idx]
        port.send(mido.Message(
            ev.type, note=ev.note, velocity=ev.velocity, channel=ev.channel
        ))
        idx += 1


# ── high-level entry points ───────────────────────────────────────────────────

def play_song(intent: dict, *, send_to_fl: bool = True) -> None:
    """Build all MIDI events and dispatch them via the absolute-time scheduler.

    Accepted intent formats:

    Multi-layer (preferred):
        {
          "tempo": 120,
          "layers": {
              "melody": [{"note": "C4", "duration": 0.5}, ...],
              "chords": [{"note": ["C3","E3","G3"], "duration": 1.0}, ...],
              "bass":   [{"note": 36, "duration": 0.5}, ...]
          }
        }

    Single-layer legacy (melody_pattern):
        {"tempo": 120, "melody_pattern": [...]}

    send_to_fl=True  → wrap in NOTE_START / NOTE_STOP (triggers FL Studio recording)
    send_to_fl=False → preview-only; no side-effects in FL Studio
    """
    tempo  = int(intent.get("tempo", 120))
    layers = intent.get("layers")

    if not layers:
        layers = {"melody": intent.get("melody_pattern", [])}

    events = build_song(layers, tempo)
    if not events:
        print("play_song: no events to play.")
        return

    with mido.open_output(_get_port()) as port:
        if send_to_fl:
            _control(port, NOTE_START)
            print("FL Studio: recording STARTED")

        run_scheduler(events, port)

        if send_to_fl:
            _control(port, NOTE_STOP)
            print("FL Studio: recording STOPPED")


def preview_song(intent: dict) -> None:
    """Play back without arming FL Studio recording (safe for local preview)."""
    play_song(intent, send_to_fl=False)
