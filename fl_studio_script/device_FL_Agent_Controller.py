# FL Agent Controller — FL Studio MIDI Script
#
# Installation:
#   Copy this file to:
#     Documents\Image-Line\FL Studio\Settings\Hardware\
#   Restart FL Studio, then go to Options → MIDI Settings → Input,
#   enable the "FL Agent" port, and set its Controller type to
#   "FL Agent Controller".
#
# SysEx control protocol (must match midi_scheduler.py):
#
#   Every message is a SysEx frame:
#       0xF0  SYSEX_MANUFACTURER  SYSEX_DEVICE_ID  CMD  [DATA…]  0xF7
#
#   CMD 0x01  CMD_START_RECORDING — arm record + start transport
#   CMD 0x02  CMD_STOP_RECORDING  — stop transport
#   CMD 0x03  CMD_SET_CHANNEL     — DATA[0] = MIDI channel (0-15)

import midi
import transport
import channels
import ui

# ---------------------------------------------------------------------------
# Protocol constants  (must stay in sync with midi_scheduler.py)
# ---------------------------------------------------------------------------

SYSEX_MANUFACTURER  = 0x7D   # Non-commercial / educational manufacturer ID
SYSEX_DEVICE_ID     = 0x01   # FL Agent device identifier

CMD_START_RECORDING = 0x01
CMD_STOP_RECORDING  = 0x02
CMD_SET_CHANNEL     = 0x03

SCRIPT_NAME = "FL Agent Controller"

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_active_channel: int = 0   # MIDI channel currently selected for recording

# ---------------------------------------------------------------------------
# Lifecycle callbacks
# ---------------------------------------------------------------------------

def OnInit():
    print(f"{SCRIPT_NAME} loaded.")
    print("  SysEx protocol:  0xF0 0x7D 0x01 CMD [DATA…] 0xF7")
    print(f"  CMD {CMD_START_RECORDING:#04x}  → START recording")
    print(f"  CMD {CMD_STOP_RECORDING:#04x}  → STOP  recording")
    print(f"  CMD {CMD_SET_CHANNEL:#04x}  → SET active channel  (DATA[0] = 0-15)")
    ui.setHintMsg(f"{SCRIPT_NAME} ready")


def OnDeInit():
    print(f"{SCRIPT_NAME} unloaded.")


# ---------------------------------------------------------------------------
# SysEx message handler
# ---------------------------------------------------------------------------

def OnSysEx(event):
    """Parse and dispatch FL Agent SysEx control messages.

    Expected frame (bytes, including start/end):
        0xF0  SYSEX_MANUFACTURER  SYSEX_DEVICE_ID  CMD  [DATA…]  0xF7

    The method validates the manufacturer/device IDs before dispatching so
    that unrelated SysEx traffic is silently ignored.
    """
    data = list(event.sysex)   # convert bytes/tuple to a plain list of ints

    # Minimum valid frame: [F0, MFR, DEV, CMD, F7] = 5 bytes
    if len(data) < 5:
        return
    if data[0] != 0xF0 or data[-1] != 0xF7:
        return
    if data[1] != SYSEX_MANUFACTURER or data[2] != SYSEX_DEVICE_ID:
        return   # not our message — ignore silently

    cmd     = data[3]
    payload = data[4:-1]   # bytes between CMD and the trailing 0xF7

    event.handled = True

    if cmd == CMD_START_RECORDING:
        _start_recording()
    elif cmd == CMD_STOP_RECORDING:
        _stop_recording()
    elif cmd == CMD_SET_CHANNEL:
        if payload:
            _set_channel(payload[0])


# ---------------------------------------------------------------------------
# Transport / channel helpers
# ---------------------------------------------------------------------------

def _start_recording():
    """Arm record mode and start the transport."""
    if not transport.isRecording():
        transport.record()

    if not transport.isPlaying():
        transport.start()

    msg = f"{SCRIPT_NAME}: Recording STARTED (ch {_active_channel})"
    ui.setHintMsg(msg)
    print(msg)


def _stop_recording():
    """Stop the transport (recording stops automatically)."""
    if transport.isPlaying():
        transport.stop()

    msg = f"{SCRIPT_NAME}: Recording STOPPED"
    ui.setHintMsg(msg)
    print(msg)


def _set_channel(channel: int):
    """Update the active recording channel."""
    global _active_channel
    if not 0 <= channel <= 15:
        print(f"{SCRIPT_NAME}: Invalid channel {channel} (must be 0-15)")
        return
    _active_channel = channel
    msg = f"{SCRIPT_NAME}: Active channel → {channel}"
    ui.setHintMsg(msg)
    print(msg)
