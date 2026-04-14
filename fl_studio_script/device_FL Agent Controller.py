# FL Agent Controller — FL Studio MIDI Script
#
# Installation:
#   Copy this file to:
#     Documents\Image-Line\FL Studio\Settings\Hardware\FL Agent Controller\
#   and copy "FL Agent Controller.ini" next to that folder.
#   Restart FL Studio, then go to Options → MIDI Settings → Input,
#   enable the "FL Agent" port, and set its Controller type to
#   "FL Agent Controller".
#
# Usage:
#   Send MIDI note 72 (C5) → starts recording
#   Send MIDI note 74 (D5) → stops recording
#   Send MIDI note 76 (E5) → dumps channel list to %TEMP%\fl_agent_channels.json

import json
import os
import tempfile

import channels
import midi
import transport
import ui

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NOTE_START_RECORDING = 72   # C5
NOTE_STOP_RECORDING  = 74   # D5
NOTE_LIST_CHANNELS   = 76   # E5

SCRIPT_NAME = "FL Agent Controller"
CHANNELS_OUTPUT_FILE = os.path.join(tempfile.gettempdir(), "fl_agent_channels.json")

# ---------------------------------------------------------------------------
# Lifecycle callbacks
# ---------------------------------------------------------------------------

def OnInit():
    print(f"{SCRIPT_NAME} loaded.")
    print(f"  Note {NOTE_START_RECORDING} (C5) -> START recording")
    print(f"  Note {NOTE_STOP_RECORDING}  (D5) -> STOP  recording")
    print(f"  Note {NOTE_LIST_CHANNELS}  (E5) -> LIST  channels -> {CHANNELS_OUTPUT_FILE}")
    ui.setHintMsg(f"{SCRIPT_NAME} ready")


def OnDeInit():
    print(f"{SCRIPT_NAME} unloaded.")


# ---------------------------------------------------------------------------
# MIDI message handler
# ---------------------------------------------------------------------------

def OnMidiMsg(event):
    is_note_on = (event.midiId == midi.MIDI_NOTEON) and (event.velocity > 0)
    if not is_note_on:
        return

    if event.note == NOTE_START_RECORDING:
        event.handled = True
        _start_recording()

    elif event.note == NOTE_STOP_RECORDING:
        event.handled = True
        _stop_recording()

    elif event.note == NOTE_LIST_CHANNELS:
        event.handled = True
        _list_channels()


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------

def _start_recording():
    if not transport.isRecording():
        transport.record()
    if not transport.isPlaying():
        transport.start()

    msg = f"{SCRIPT_NAME}: Recording STARTED"
    ui.setHintMsg(msg)
    print(msg)


def _stop_recording():
    if transport.isPlaying():
        transport.stop()

    msg = f"{SCRIPT_NAME}: Recording STOPPED"
    ui.setHintMsg(msg)
    print(msg)


# ---------------------------------------------------------------------------
# Channel listing
# ---------------------------------------------------------------------------

def _list_channels():
    """Dump the current channel rack to a JSON file the host can read back."""
    data = [
        {"index": i, "name": channels.getChannelName(i)}
        for i in range(channels.channelCount())
    ]
    with open(CHANNELS_OUTPUT_FILE, "w") as fh:
        json.dump(data, fh)

    msg = f"{SCRIPT_NAME}: listed {len(data)} channels"
    ui.setHintMsg(msg)
    print(msg)
