# FL Agent Controller — FL Studio MIDI Script
#
# Installation:
#   Copy this file to:
#     Documents\Image-Line\FL Studio\Settings\Hardware\
#   Restart FL Studio, then go to Options → MIDI Settings → Input,
#   enable the "FL Agent" port, and set its Controller type to
#   "FL Agent Controller".
#
# ── Inbound SysEx protocol (Python → FL Studio) ──────────────────────────────
#   Every command is a SysEx frame:
#       0xF0  SYSEX_MANUFACTURER  SYSEX_DEVICE_ID  CMD  [DATA…]  0xF7
#
#   CMD 0x01  CMD_START_RECORDING — arm record + start transport
#   CMD 0x02  CMD_STOP_RECORDING  — stop transport
#   CMD 0x03  CMD_SET_CHANNEL     — DATA[0] = channel rack index to activate
#   CMD 0x04  CMD_LIST_CHANNELS   — respond with one RSP_CHANNEL_ENTRY per channel
#
# ── Outbound SysEx responses (FL Studio → Python) ────────────────────────────
#   RSP 0x10  RSP_CHANNEL_ENTRY   — DATA = [index, name as 7-bit ASCII bytes]
#   RSP 0x11  RSP_CHANNEL_DONE    — DATA = [selected_index] — marks end of list

import midi
import transport
import channels
import device
import ui

# ---------------------------------------------------------------------------
# Protocol constants  (must stay in sync with midi_scheduler.py)
# ---------------------------------------------------------------------------

SYSEX_MANUFACTURER  = 0x7D   # Non-commercial / educational manufacturer ID
SYSEX_DEVICE_ID     = 0x01   # FL Agent device identifier

# Inbound commands
CMD_START_RECORDING = 0x01
CMD_STOP_RECORDING  = 0x02
CMD_SET_CHANNEL     = 0x03
CMD_LIST_CHANNELS   = 0x04

# Outbound response codes
RSP_CHANNEL_ENTRY   = 0x10   # one message per channel: [index, name bytes…]
RSP_CHANNEL_DONE    = 0x11   # end-of-list marker: [selected_index]

SCRIPT_NAME = "FL Agent Controller"

# ---------------------------------------------------------------------------
# Lifecycle callbacks
# ---------------------------------------------------------------------------

def OnInit():
    print(f"{SCRIPT_NAME} loaded.")
    print("  Inbound SysEx:  0xF0 0x7D 0x01 CMD [DATA…] 0xF7")
    print(f"  CMD {CMD_START_RECORDING:#04x}  START recording")
    print(f"  CMD {CMD_STOP_RECORDING:#04x}  STOP  recording")
    print(f"  CMD {CMD_SET_CHANNEL:#04x}  SET channel   (DATA[0] = channel rack index)")
    print(f"  CMD {CMD_LIST_CHANNELS:#04x}  LIST channels (responds with RSP_CHANNEL_ENTRY/DONE)")
    _print_channel_rack()
    ui.setHintMsg(f"{SCRIPT_NAME} ready")


def OnDeInit():
    print(f"{SCRIPT_NAME} unloaded.")


# ---------------------------------------------------------------------------
# Inbound SysEx handler
# ---------------------------------------------------------------------------

def OnSysEx(event):
    """Parse and dispatch FL Agent SysEx control messages.

    Expected frame (bytes, including start/end delimiters):
        0xF0  SYSEX_MANUFACTURER  SYSEX_DEVICE_ID  CMD  [DATA…]  0xF7

    Manufacturer and device IDs are validated; unrelated SysEx traffic is
    silently ignored so other controller scripts coexist without conflict.
    """
    data = list(event.sysex)   # bytes / tuple → plain list of ints

    # Minimum valid frame: [F0, MFR, DEV, CMD, F7] = 5 bytes
    if len(data) < 5:
        return
    if data[0] != 0xF0 or data[-1] != 0xF7:
        return
    if data[1] != SYSEX_MANUFACTURER or data[2] != SYSEX_DEVICE_ID:
        return   # not our device — ignore silently

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
    elif cmd == CMD_LIST_CHANNELS:
        _respond_channel_list()


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------

def _start_recording():
    """Arm record mode and start the transport.

    Uses channels.selectedChannel() and channels.getChannelName() to log
    which FL Studio channel rack instrument is currently active.
    """
    if not transport.isRecording():
        transport.record()

    if not transport.isPlaying():
        transport.start()

    idx  = channels.selectedChannel()
    name = channels.getChannelName(idx) if idx >= 0 else "—"
    msg  = f"{SCRIPT_NAME}: Recording STARTED  [{idx}] {name}"
    ui.setHintMsg(msg)
    print(msg)


def _stop_recording():
    """Stop the transport (recording stops automatically)."""
    if transport.isPlaying():
        transport.stop()

    msg = f"{SCRIPT_NAME}: Recording STOPPED"
    ui.setHintMsg(msg)
    print(msg)


# ---------------------------------------------------------------------------
# Channel helpers
# ---------------------------------------------------------------------------

def _set_channel(index: int):
    """Activate a channel rack channel by index using channels.setActiveChannel().

    Validates the index against the live channel count so out-of-range
    requests are rejected cleanly rather than crashing.
    """
    count = channels.channelCount()
    if not 0 <= index < count:
        msg = (
            f"{SCRIPT_NAME}: Channel index {index} out of range "
            f"(channel rack has {count} channel(s), indices 0-{count - 1})"
        )
        ui.setHintMsg(msg)
        print(msg)
        return

    channels.setActiveChannel(index)
    name = channels.getChannelName(index)
    msg  = f"{SCRIPT_NAME}: Active channel → [{index}] {name}"
    ui.setHintMsg(msg)
    print(msg)


def _respond_channel_list():
    """Send the FL Studio channel rack contents back to Python via SysEx.

    For each channel rack channel, emits one RSP_CHANNEL_ENTRY frame:
        0xF0  MFR  DEV  RSP_CHANNEL_ENTRY  index  name_byte…  0xF7

    After all channels, emits one RSP_CHANNEL_DONE frame carrying the
    currently selected channel index:
        0xF0  MFR  DEV  RSP_CHANNEL_DONE  selected_index  0xF7

    Channel names are encoded as 7-bit ASCII (high bit stripped) because
    SysEx data bytes must be in the range 0x00-0x7F.
    """
    count    = channels.channelCount()
    selected = channels.selectedChannel()

    for i in range(count):
        name       = channels.getChannelName(i)
        name_bytes = [ord(c) & 0x7F for c in name]   # strip high bit
        _sysex_out([RSP_CHANNEL_ENTRY, i] + name_bytes)

    # Trailing marker includes the currently selected channel index
    _sysex_out([RSP_CHANNEL_DONE, selected & 0x7F])

    print(f"{SCRIPT_NAME}: Sent channel list  ({count} channels, selected={selected})")


def _sysex_out(payload: list):
    """Transmit a SysEx response frame: 0xF0  MFR  DEV  [payload…]  0xF7."""
    frame = bytes([0xF0, SYSEX_MANUFACTURER, SYSEX_DEVICE_ID] + payload + [0xF7])
    device.midiOutSysex(frame)


# ---------------------------------------------------------------------------
# Diagnostic helper — called from OnInit
# ---------------------------------------------------------------------------

def _print_channel_rack():
    """Print the full channel rack to the FL Studio script console."""
    count    = channels.channelCount()
    selected = channels.selectedChannel()
    print(f"{SCRIPT_NAME}: Channel rack — {count} channel(s):")
    for i in range(count):
        name   = channels.getChannelName(i)
        marker = "  ← active" if i == selected else ""
        print(f"  [{i:2d}] {name}{marker}")
