# FL Agent Controller — FL Studio MIDI Script
#
# Installation:
#   Copy this folder to:
#     Documents\Image-Line\FL Studio\Settings\Hardware\FL_Agent_Controller\
#   Then in FL Studio: Options → MIDI Settings → select "FL_Agent_Controller"
#   as a controller and enable it.
#
# Usage:
#   Send MIDI note 72 (C5) → starts recording (arms record + starts transport)
#   Send MIDI note 74 (D5) → stops recording (stops transport)

import midi
import transport
import ui

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NOTE_START_RECORDING = 72   # C5
NOTE_STOP_RECORDING  = 74   # D5

SCRIPT_NAME = "FL Agent Controller"

# ---------------------------------------------------------------------------
# Lifecycle callbacks
# ---------------------------------------------------------------------------

def OnInit():
    print(f"{SCRIPT_NAME} loaded.")
    print(f"  Note {NOTE_START_RECORDING} (C5) → START recording")
    print(f"  Note {NOTE_STOP_RECORDING}  (D5) → STOP  recording")
    ui.setHintMsg(f"{SCRIPT_NAME} ready")


def OnDeInit():
    print(f"{SCRIPT_NAME} unloaded.")


# ---------------------------------------------------------------------------
# MIDI message handler
# ---------------------------------------------------------------------------

def OnMidiMsg(event):
    """Called by FL Studio for every incoming MIDI message on this device."""

    # Only act on Note-On messages with non-zero velocity.
    # Note-Off messages arrive either as midiId 0x80 (MIDI_NOTEOFF)
    # or as midiId 0x90 with velocity 0 — both are ignored here.
    is_note_on = (event.midiId == midi.MIDI_NOTEON) and (event.velocity > 0)
    if not is_note_on:
        return

    if event.note == NOTE_START_RECORDING:
        event.handled = True
        _start_recording()

    elif event.note == NOTE_STOP_RECORDING:
        event.handled = True
        _stop_recording()


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------

def _start_recording():
    """Arm record mode and start the transport."""
    # Arm recording if not already armed.
    if not transport.isRecording():
        transport.record()

    # Start playback (begins the actual recording).
    if not transport.isPlaying():
        transport.start()

    msg = f"{SCRIPT_NAME}: Recording STARTED"
    ui.setHintMsg(msg)
    print(msg)


def _stop_recording():
    """Stop the transport (recording stops automatically)."""
    if transport.isPlaying():
        transport.stop()

    msg = f"{SCRIPT_NAME}: Recording STOPPED"
    ui.setHintMsg(msg)
    print(msg)
