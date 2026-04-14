"""
midi_scheduler.py — DAW-style absolute-time MIDI event scheduler.

Architecture:
  MidiEvent           Immutable, sortable event with absolute time offset (seconds from t0)
  build_events()      Converts a bar-based note list into sorted MidiEvent pairs
  build_song()        Merges multiple named layers into one sorted event list
  run_scheduler()     Dispatches events with sub-ms accuracy (no timing sleeps)
  play_song()         High-level entry: build + schedule + FL Studio recording wrap
  preview_song()      Same as play_song() but no recording control messages
  list_channels()     Return all 16 MIDI channels with their names and active status
  set_active_channel() Select the active MIDI channel for recording (notifies FL Studio)

Timing strategy:
  - All event times are absolute offsets from t0 = time.perf_counter() at start.
  - A 20 ms lookahead window pre-arms events before their deadline.
  - Sleep is used ONLY for CPU yield between lookahead windows.
  - A 1 ms spin-wait gives sub-ms dispatch accuracy for each event.
  - Zero timing drift because every dispatch checks against the same t0.

SysEx control protocol:
  Every control message is a SysEx frame:

      0xF0  SYSEX_MANUFACTURER  SYSEX_DEVICE_ID  CMD  [DATA…]  0xF7

  mido handles the 0xF0 / 0xF7 framing automatically; only the inner bytes
  are supplied to mido.Message("sysex", data=[...]).

  Commands:
      CMD_START_RECORDING (0x01) — arm FL Studio recording, start transport
      CMD_STOP_RECORDING  (0x02) — stop FL Studio transport
      CMD_SET_CHANNEL     (0x03) — set active recording channel; DATA = [channel 0-15]
"""

import time
from dataclasses import dataclass, field
from typing import Literal

import mido

# ── general constants ─────────────────────────────────────────────────────────

PORT_NAME        = "FL Agent"
DEFAULT_CHANNEL  = 0
DEFAULT_VELOCITY = 100

LOOKAHEAD_S  = 0.020   # 20 ms look-ahead window (compensates for OS jitter)
SPIN_MARGIN  = 0.001   # spin only for the final 1 ms before dispatch

# ── SysEx control protocol ────────────────────────────────────────────────────

SYSEX_MANUFACTURER  = 0x7D   # Non-commercial / educational manufacturer ID
SYSEX_DEVICE_ID     = 0x01   # FL Agent device identifier

# Command bytes — every message: 0xF0 MANUFACTURER DEVICE_ID CMD [DATA…] 0xF7
CMD_START_RECORDING = 0x01   # Arm FL Studio recording and start transport
CMD_STOP_RECORDING  = 0x02   # Stop FL Studio transport
CMD_SET_CHANNEL     = 0x03   # Select active MIDI recording channel; DATA = [channel 0-15]

# ── channel registry ──────────────────────────────────────────────────────────

CHANNEL_REGISTRY: dict[int, str] = {
    0:  "Melody",
    1:  "Chords",
    2:  "Bass",
    3:  "Drums",
    4:  "Pad",
    5:  "Lead",
    6:  "Arp",
    7:  "FX",
    8:  "Aux 1",
    9:  "Aux 2",
    10: "Aux 3",
    11: "Aux 4",
    12: "Aux 5",
    13: "Aux 6",
    14: "Aux 7",
    15: "Aux 8",
}

_active_recording_channel: int = DEFAULT_CHANNEL   # writable via set_active_channel()


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


def _send_command(port, command: int, data: list[int] = ()) -> None:
    """Send a framed FL Agent SysEx command.

    Wire format:  0xF0  SYSEX_MANUFACTURER  SYSEX_DEVICE_ID  command  [data…]  0xF7

    mido omits 0xF0 / 0xF7 from the data list — it adds them automatically
    when the message is serialised to bytes.
    """
    payload = [SYSEX_MANUFACTURER, SYSEX_DEVICE_ID, command, *data]
    port.send(mido.Message("sysex", data=payload))


# ── channel management ────────────────────────────────────────────────────────

def list_channels() -> list[dict]:
    """Return all 16 MIDI channels with their names and active status.

    Example return value::

        [
            {"channel": 0, "name": "Melody", "active": True},
            {"channel": 1, "name": "Chords", "active": False},
            ...
            {"channel": 15, "name": "Aux 8", "active": False},
        ]
    """
    return [
        {
            "channel": ch,
            "name":    name,
            "active":  ch == _active_recording_channel,
        }
        for ch, name in CHANNEL_REGISTRY.items()
    ]


def set_active_channel(channel: int) -> None:
    """Set the active MIDI channel for recording and notify FL Studio via SysEx.

    Sends CMD_SET_CHANNEL so the FL Studio script can arm the correct
    instrument/channel before the next recording session starts.

    Args:
        channel: MIDI channel number 0-15.

    Raises:
        ValueError: if channel is outside 0-15.
    """
    global _active_recording_channel
    if not 0 <= channel <= 15:
        raise ValueError(f"Channel must be 0–15, got {channel}")
    _active_recording_channel = channel
    name = CHANNEL_REGISTRY[channel]
    with mido.open_output(_get_port()) as port:
        _send_command(port, CMD_SET_CHANNEL, [channel])
    print(f"Active recording channel → {channel} ({name})")


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
        midi_notes = [_resolve(n) for n in raw_list]

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

    Layers without a channel_map entry use _active_recording_channel.
    All layers start at t=0 — perfect synchronisation.
    """
    ch_map  = channel_map  or {}
    vel_map = velocity_map or {}
    events: list[MidiEvent] = []

    for name, notes in layers.items():
        ch  = ch_map.get(name,  _active_recording_channel)
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

    send_to_fl=True  → wrap in CMD_START_RECORDING / CMD_STOP_RECORDING SysEx
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

    ch   = _active_recording_channel
    name = CHANNEL_REGISTRY[ch]

    with mido.open_output(_get_port()) as port:
        if send_to_fl:
            _send_command(port, CMD_START_RECORDING)
            print(f"FL Studio: recording STARTED (channel {ch} — {name})")

        run_scheduler(events, port)

        if send_to_fl:
            _send_command(port, CMD_STOP_RECORDING)
            print("FL Studio: recording STOPPED")


def preview_song(intent: dict) -> None:
    """Play back without arming FL Studio recording (safe for local preview)."""
    play_song(intent, send_to_fl=False)
