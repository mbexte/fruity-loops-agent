"""
fl_transport.py — MIDI communication with FL Studio via loopMIDI.

  Note 72 (C5) → Start recording
  Note 74 (D5) → Stop recording

  play_melody(intent) — single-layer playback (legacy API, delegates to scheduler)
  play_song(intent)   — multi-layer playback via the absolute-time scheduler

Requirements:
  loopMIDI running with a port named "FL Agent".
  FL Studio MIDI Settings: "FL Agent" enabled, Controller type = FL Agent Controller.

Usage (CLI):
  python fl_transport.py start
  python fl_transport.py stop
"""

import sys
import mido

from midi_scheduler import (
    DEFAULT_VELOCITY,
    NOTE_START,
    NOTE_STOP,
    _get_port,
    _control,
    play_song,
    preview_song,
)

# Re-export for callers that import directly from this module
__all__ = [
    "start_recording",
    "stop_recording",
    "play_melody",
    "play_song",
    "preview_song",
]


def start_recording() -> None:
    """Send MIDI note 72 (C5) → FL Studio arms and starts recording."""
    with mido.open_output(_get_port()) as port:
        _control(port, NOTE_START)
    print("FL Studio: recording started")


def stop_recording() -> None:
    """Send MIDI note 74 (D5) → FL Studio stops recording."""
    with mido.open_output(_get_port()) as port:
        _control(port, NOTE_STOP)
    print("FL Studio: recording stopped")


def play_melody(intent: dict) -> None:
    """Play a single-layer melody and arm FL Studio recording.

    Delegates to midi_scheduler.play_song() for accurate absolute-time scheduling.

    intent keys:
      tempo          (int)       — BPM
      melody_pattern (list[dict]) — dicts with note (MIDI number or name) and duration (bars)
    """
    play_song(intent, send_to_fl=True)


if __name__ == "__main__":
    commands = {"start": start_recording, "stop": stop_recording}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print("Usage: python fl_transport.py <start|stop>")
        sys.exit(1)
    commands[sys.argv[1]]()
