"""
fl_transport.py — MIDI communication with FL Studio via loopMIDI.

SysEx control protocol (matches midi_scheduler.py and FL Studio script):

    Frame:  0xF0  SYSEX_MANUFACTURER  SYSEX_DEVICE_ID  CMD  [DATA…]  0xF7

    CMD_START_RECORDING (0x01) — arm FL Studio recording, start transport
    CMD_STOP_RECORDING  (0x02) — stop FL Studio transport
    CMD_SET_CHANNEL     (0x03) — set active MIDI recording channel; DATA = [channel 0-15]

Public API:
  start_recording()           — send CMD_START_RECORDING
  stop_recording()            — send CMD_STOP_RECORDING
  list_channels()             — return all 16 channels with names and active status
  set_active_channel(channel) — select channel 0-15 for recording (sends CMD_SET_CHANNEL)
  play_melody(intent)         — single-layer playback (legacy API, delegates to scheduler)
  play_song(intent)           — multi-layer playback via the absolute-time scheduler

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
    CMD_START_RECORDING,
    CMD_STOP_RECORDING,
    _get_port,
    _send_command,
    list_channels,
    set_active_channel,
    query_fl_channels,
    play_song,
    preview_song,
)

# Re-export for callers that import directly from this module
__all__ = [
    "start_recording",
    "stop_recording",
    "list_channels",
    "set_active_channel",
    "query_fl_channels",
    "play_melody",
    "play_song",
    "preview_song",
]


def start_recording() -> None:
    """Send CMD_START_RECORDING SysEx → FL Studio arms and starts recording."""
    with mido.open_output(_get_port()) as port:
        _send_command(port, CMD_START_RECORDING)
    print("FL Studio: recording started")


def stop_recording() -> None:
    """Send CMD_STOP_RECORDING SysEx → FL Studio stops recording."""
    with mido.open_output(_get_port()) as port:
        _send_command(port, CMD_STOP_RECORDING)
    print("FL Studio: recording stopped")


def play_melody(intent: dict) -> None:
    """Play a single-layer melody and arm FL Studio recording.

    Delegates to midi_scheduler.play_song() for accurate absolute-time scheduling.

    intent keys:
      tempo          (int)        — BPM
      melody_pattern (list[dict]) — dicts with note (MIDI number or name) and duration (bars)
    """
    play_song(intent, send_to_fl=True)


if __name__ == "__main__":
    commands = {"start": start_recording, "stop": stop_recording}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print("Usage: python fl_transport.py <start|stop>")
        sys.exit(1)
    commands[sys.argv[1]]()
