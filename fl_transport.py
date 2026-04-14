"""
fl_transport.py — MIDI communication with FL Studio via loopMIDI.

  Note 72 (C5) → Start recording
  Note 74 (D5) → Stop recording
  Note 76 (E5) → List channels (FL writes %TEMP%/fl_agent_channels.json)

  play_melody(intent) — single-layer playback (legacy API, delegates to scheduler)
  play_song(intent)   — multi-layer playback via the absolute-time scheduler
  list_channels()     — ask FL Studio to dump its channel rack, return as list

Requirements:
  loopMIDI running with a port named "FL Agent".
  FL Studio MIDI Settings: "FL Agent" enabled, Controller type = FL Agent Controller.

Usage (CLI):
  python fl_transport.py start
  python fl_transport.py stop
  python fl_transport.py list-channels
"""

import json
import os
import sys
import tempfile
import time

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

NOTE_LIST_CHANNELS = 76  # E5 — must match device_FL Agent Controller.py
CHANNELS_OUTPUT_FILE = os.path.join(tempfile.gettempdir(), "fl_agent_channels.json")

# Re-export for callers that import directly from this module
__all__ = [
    "start_recording",
    "stop_recording",
    "play_melody",
    "play_song",
    "preview_song",
    "list_channels",
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


def list_channels(timeout: float = 3.0) -> list[dict]:
    """Ask FL Studio to dump its channel rack and return the parsed list.

    Sends MIDI note 76 (E5); the controller script writes a JSON file to
    %TEMP%/fl_agent_channels.json which we then read back.
    """
    # Remove any stale dump so we only accept a fresh one.
    try:
        os.remove(CHANNELS_OUTPUT_FILE)
    except FileNotFoundError:
        pass

    with mido.open_output(_get_port()) as port:
        _control(port, NOTE_LIST_CHANNELS)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(CHANNELS_OUTPUT_FILE):
            with open(CHANNELS_OUTPUT_FILE, "r") as fh:
                return json.load(fh)
        time.sleep(0.05)

    raise TimeoutError(
        f"FL Studio did not write {CHANNELS_OUTPUT_FILE} within {timeout}s. "
        "Is FL Studio open with the FL Agent Controller enabled?"
    )


def play_melody(intent: dict) -> None:
    """Play a single-layer melody and arm FL Studio recording.

    Delegates to midi_scheduler.play_song() for accurate absolute-time scheduling.

    intent keys:
      tempo          (int)       — BPM
      melody_pattern (list[dict]) — dicts with note (MIDI number or name) and duration (bars)
    """
    play_song(intent, send_to_fl=True)


def _cli_list_channels() -> None:
    channels = list_channels()
    print(f"FL Studio: {len(channels)} channel(s)")
    for ch in channels:
        print(f"  [{ch['index']:>2}] {ch['name']}")


if __name__ == "__main__":
    commands = {
        "start": start_recording,
        "stop": stop_recording,
        "list-channels": _cli_list_channels,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print("Usage: python fl_transport.py <start|stop|list-channels>")
        sys.exit(1)
    commands[sys.argv[1]]()
