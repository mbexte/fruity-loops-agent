# device_FL_Agent_Controller.py — FL Studio MIDI Controller Script
#
# Installation:
#   Copy this file to:
#     Documents\Image-Line\FL Studio\Settings\Hardware\
#   Restart FL Studio, then go to Options ->MIDI Settings ->Input,
#   enable the "FL Agent" port, and set its Controller type to
#   "FL Agent Controller".
#
# ══════════════════════════════════════════════════════════════════════════════
# FL MIDI Protocol — 3-Tier Architecture
# ══════════════════════════════════════════════════════════════════════════════
#
#  TIER 1 — Note-Based Transport Control (immediate, zero-latency)
#  ──────────────────────────────────────────────────────────────
#   Note 72 (C5)  velocity any  ->START recording  (arm + play)
#   Note 73 (C#5) velocity any  ->PAUSE / RESUME transport
#   Note 74 (D5)  velocity any  ->STOP  recording
#   Note 75 (D#5) velocity any  ->REWIND to bar 1
#   Note 76 (E5)  velocity any  ->LOOP  toggle
#   Note 77 (F5)  velocity any  ->METRONOME toggle
#
#  TIER 2 — CC-Based Parameter Control (7-bit parameters, instant)
#  ───────────────────────────────────────────────────────────────
#   CC  1 (Mod Wheel)  ->TEMPO: maps 0–127 ->60–187 BPM
#   CC  7 (Volume)     ->MASTER VOLUME (0–127 ->0.0–1.0)
#   CC 20              ->PATTERN SELECT (slot 0–127)
#   CC 21              ->TIME SIG numerator (raw value 1–16)
#   CC 22              ->TIME SIG denominator power (0=1, 1=2, 2=4, 3=8)
#   CC 23              ->LOOP enable (0=off, ≥64=on)
#   CC 24              ->METRONOME (0=off, ≥64=on)
#   CC 11              ->PATTERN LENGTH steps (value+1 steps)
#
#  TIER 3 — SysEx Structured Commands (14-bit data, rich payloads)
#  ───────────────────────────────────────────────────────────────
#   Header: F0 7D [CMD] [data...] F7
#   Manufacturer ID 0x7D = non-commercial / educational (MIDI spec)
#
#   Commands (host ->device):
#     0x01  PING                — request alive check
#     0x02  SET_PATTERN_NAME    [slot] [len] [ascii_bytes...]
#     0x03  PATTERN_INFO_REQ    [slot]
#     0x04  QUANTIZE            [grid]  0=1/4  1=1/8  2=1/16  3=1/32
#     0x05  SET_TEMPO           [bpm_msb7] [bpm_lsb7]  (14-bit BPM, 40–999)
#     0x06  CHANNEL_MUTE        [ch] [state]
#     0x07  CHANNEL_SOLO        [ch] [state]
#     0x10  NOTE_EVENT          [ch] [note] [vel] [dur_msb7] [dur_lsb7]
#     0x11  PATTERN_CLEAR       [slot]
#     0x12  PATTERN_SELECT      [slot]
#     0x13  REQUEST_STATUS      — full state snapshot
#
#   Responses (device ->host):
#     0x80  ACK                 [echoed_cmd]
#     0x81  NACK                [echoed_cmd] [error_code]
#     0x82  PONG                [uptime_msb7] [uptime_lsb7]
#     0x83  STATUS              [state_byte] [pattern_slot] [bpm_msb7] [bpm_lsb7]
#                state_byte bits:  0=is_playing  1=is_recording  2=loop_on  3=metronome_on

import midi
import transport
import ui
import channels
import patterns
import mixer
import device

# ── Protocol constants ────────────────────────────────────────────────────────

SCRIPT_NAME   = "FL Agent Controller"
SCRIPT_VER    = "2.0"
SYSEX_MANF_ID = 0x7D          # Non-commercial / educational manufacturer ID

# Tier-1 Transport Notes
NOTE_START_REC  = 72   # C5  — arm + start recording
NOTE_PAUSE      = 73   # C#5 — pause / resume
NOTE_STOP_REC   = 74   # D5  — stop recording
NOTE_REWIND     = 75   # D#5 — rewind to bar 1
NOTE_LOOP       = 76   # E5  — toggle loop mode
NOTE_METRONOME  = 77   # F5  — toggle metronome

TRANSPORT_NOTES = frozenset({
    NOTE_START_REC, NOTE_PAUSE, NOTE_STOP_REC,
    NOTE_REWIND, NOTE_LOOP, NOTE_METRONOME
})

# Tier-2 CC controls
CC_TEMPO        = 1    # Mod Wheel ->BPM 60–187
CC_MASTER_VOL   = 7    # Master volume 0–127
CC_PATTERN_LEN  = 11   # Pattern length (steps)
CC_PATTERN_SEL  = 20   # Pattern slot select
CC_TIMESIG_NUM  = 21   # Time sig numerator
CC_TIMESIG_DEN  = 22   # Time sig denominator power-of-2
CC_LOOP         = 23   # Loop enable
CC_METRONOME_CC = 24   # Metronome enable

# Tier-3 SysEx commands  (host ->device)
CMD_PING              = 0x01
CMD_SET_PATTERN_NAME  = 0x02
CMD_PATTERN_INFO_REQ  = 0x03
CMD_QUANTIZE          = 0x04
CMD_SET_TEMPO         = 0x05
CMD_CHANNEL_MUTE      = 0x06
CMD_CHANNEL_SOLO      = 0x07
CMD_NOTE_EVENT        = 0x10
CMD_PATTERN_CLEAR     = 0x11
CMD_PATTERN_SELECT    = 0x12
CMD_REQUEST_STATUS    = 0x13

# Tier-3 SysEx responses  (device ->host)
RSP_ACK    = 0x80
RSP_NACK   = 0x81
RSP_PONG   = 0x82
RSP_STATUS = 0x83

# Error codes
ERR_UNKNOWN_CMD  = 0x01
ERR_BAD_LENGTH   = 0x02
ERR_OUT_OF_RANGE = 0x03
ERR_NOT_READY    = 0x04

# Quantize grid labels
QUANTIZE_GRIDS = {0: "1/4", 1: "1/8", 2: "1/16", 3: "1/32"}

# ── Runtime state ─────────────────────────────────────────────────────────────

_state = {
    "start_time_ms": 0,       # set in OnInit
    "current_pattern": 0,
    "bpm": 140,               # last BPM set via SET_TEMPO; transport.getTempo() doesn't exist
}

# ── Lifecycle callbacks ───────────────────────────────────────────────────────

def OnInit():
    import time as _time
    _state["start_time_ms"] = int(_time.monotonic() * 1000)

    print(f"[{SCRIPT_NAME} v{SCRIPT_VER}] Loaded")
    print("  Tier-1 Notes : 72=START  73=PAUSE  74=STOP  75=REWIND  76=LOOP  77=METRO")
    print("  Tier-2 CCs   : CC1=TEMPO  CC20=PATTERN  CC23=LOOP  CC7=VOL")
    print(f"  Tier-3 SysEx : manf=0x{SYSEX_MANF_ID:02X}  cmds=0x01–0x13")
    ui.setHintMsg(f"{SCRIPT_NAME} v{SCRIPT_VER} ready")


def OnDeInit():
    print(f"[{SCRIPT_NAME}] Unloaded.")


# ── Message router ────────────────────────────────────────────────────────────

def OnMidiMsg(event):
    """Route Note-On and CC messages to their tier handlers."""
    if event.midiId == midi.MIDI_NOTEON and event.velocity > 0:
        _handle_note_on(event)
    elif event.midiId == midi.MIDI_CONTROLCHANGE:
        _handle_cc(event)


def OnSysEx(event):
    """Route SysEx messages to the Tier-3 handler."""
    raw = event.sysex        # FL Studio includes F0 / F7 framing bytes
    # Strip F0 start and F7 end if present
    data = raw
    if data and data[0] == 0xF0:
        data = data[1:]
    if data and data[-1] == 0xF7:
        data = data[:-1]
    if len(data) < 2 or data[0] != SYSEX_MANF_ID:
        return
    cmd = data[1]
    # Ignore echoed-back responses (RSP_ACK=0x80 .. RSP_STATUS=0x83).
    # The loopMIDI port feeds our own outgoing SysEx back as input; without
    # this guard every NACK would trigger another NACK indefinitely.
    if cmd >= RSP_ACK:
        return
    event.handled = True
    payload = data[2:]
    _dispatch_sysex(cmd, payload)


# ══════════════════════════════════════════════════════════════════════════════
# TIER 1 — Note-based transport
# ══════════════════════════════════════════════════════════════════════════════

def _handle_note_on(event):
    note = event.note
    if note not in TRANSPORT_NOTES:
        return
    event.handled = True

    dispatch = {
        NOTE_START_REC : _cmd_start_recording,
        NOTE_PAUSE     : _cmd_pause_resume,
        NOTE_STOP_REC  : _cmd_stop_recording,
        NOTE_REWIND    : _cmd_rewind,
        NOTE_LOOP      : _cmd_toggle_loop,
        NOTE_METRONOME : _cmd_toggle_metronome,
    }
    dispatch[note]()


def _cmd_start_recording():
    """Arm record mode and start the transport."""
    if not transport.isRecording():
        transport.record()
    if not transport.isPlaying():
        transport.start()
    _hint("Recording STARTED")


def _cmd_stop_recording():
    """Stop the transport; recording stops automatically."""
    if transport.isPlaying():
        transport.stop()
    _hint("Recording STOPPED")


def _cmd_pause_resume():
    """Pause if playing, resume if paused."""
    if transport.isPlaying():
        transport.stop()
        _hint("Transport PAUSED")
    else:
        transport.start()
        _hint("Transport RESUMED")


def _cmd_rewind():
    """Return playhead to bar 1."""
    transport.setSongPos(0)
    _hint("Rewound to bar 1")


def _cmd_toggle_loop():
    """Toggle loop mode on / off."""
    new = not transport.getLoopMode()
    transport.setLoopMode(int(new))
    _hint(f"Loop {'ON' if new else 'OFF'}")


def _cmd_toggle_metronome():
    """Toggle the FL Studio metronome."""
    transport.globalTransport(midi.FPT_Metronome, 1)
    _hint("Metronome toggled")


# ══════════════════════════════════════════════════════════════════════════════
# TIER 2 — CC-based parameters
# ══════════════════════════════════════════════════════════════════════════════

def _handle_cc(event):
    cc  = event.controlNum
    val = event.controlVal
    event.handled = True

    if   cc == CC_TEMPO        : _cc_set_tempo(val)
    elif cc == CC_MASTER_VOL   : _cc_master_volume(val)
    elif cc == CC_PATTERN_SEL  : _cc_select_pattern(val)
    elif cc == CC_TIMESIG_NUM  : _cc_timesig_num(val)
    elif cc == CC_TIMESIG_DEN  : _cc_timesig_den(val)
    elif cc == CC_LOOP         : _cc_set_loop(val >= 64)
    elif cc == CC_METRONOME_CC : _cc_set_metronome(val >= 64)
    elif cc == CC_PATTERN_LEN  : _cc_pattern_length(val)


def _cc_set_tempo(val):
    """CC 1 value 0–127 ->BPM 60–187."""
    bpm = 60 + val
    transport.setTempo(bpm)
    _hint(f"Tempo (CC) ->{bpm} BPM")


def _cc_master_volume(val):
    """CC 7 value 0–127 ->master volume 0.0–1.0."""
    mixer.setTrackVolume(0, val / 127.0)
    _hint(f"Master vol ->{int(val / 1.27)}%")


def _cc_select_pattern(val):
    slot = max(0, min(val, 127))
    _state["current_pattern"] = slot
    patterns.jumpToPattern(slot)
    _hint(f"Pattern (CC) ->{slot + 1}")


def _cc_timesig_num(val):
    num = max(1, min(val, 16))
    transport.setTimeSignature(num, transport.getTimeSig()[1])
    _hint(f"Time sig numerator ->{num}")


def _cc_timesig_den(val):
    """val 0–4 ->denominator 1, 2, 4, 8, 16."""
    den = 2 ** max(0, min(val, 4))
    transport.setTimeSignature(transport.getTimeSig()[0], den)
    _hint(f"Time sig denominator ->{den}")


def _cc_set_loop(enable):
    transport.setLoopMode(int(enable))
    _hint(f"Loop (CC) ->{'ON' if enable else 'OFF'}")


def _cc_set_metronome(enable):
    transport.globalTransport(midi.FPT_Metronome, 1 if enable else 0)
    _hint(f"Metronome (CC) ->{'ON' if enable else 'OFF'}")


def _cc_pattern_length(val):
    """CC 11 value 0–127 ->1–128 steps."""
    steps = max(1, val + 1)
    _hint(f"Pattern length ->{steps} steps")


# ══════════════════════════════════════════════════════════════════════════════
# TIER 3 — SysEx structured commands
# ══════════════════════════════════════════════════════════════════════════════

def _dispatch_sysex(cmd, payload):
    handlers = {
        CMD_PING             : _sx_ping,
        CMD_SET_PATTERN_NAME : _sx_set_pattern_name,
        CMD_PATTERN_INFO_REQ : _sx_pattern_info,
        CMD_QUANTIZE         : _sx_quantize,
        CMD_SET_TEMPO        : _sx_set_tempo,
        CMD_CHANNEL_MUTE     : _sx_channel_mute,
        CMD_CHANNEL_SOLO     : _sx_channel_solo,
        CMD_NOTE_EVENT       : _sx_note_event,
        CMD_PATTERN_CLEAR    : _sx_pattern_clear,
        CMD_PATTERN_SELECT   : _sx_pattern_select,
        CMD_REQUEST_STATUS   : _sx_request_status,
    }
    handler = handlers.get(cmd)
    if handler is None:
        # Silently discard unknown commands — sending NACK would loop back
        # through the loopMIDI port and trigger another unknown-command cycle.
        print(f"[{SCRIPT_NAME}] Ignored unknown SysEx cmd=0x{cmd:02X}")
        return
    try:
        handler(payload)
    except Exception as exc:
        print(f"[{SCRIPT_NAME}] SysEx 0x{cmd:02X} error: {exc}")
        _send_nack(cmd, ERR_NOT_READY)


def _sx_ping(payload):
    """Respond with PONG + 14-bit uptime in ms."""
    import time as _time
    up = int(_time.monotonic() * 1000) - _state["start_time_ms"]
    up &= 0x3FFF
    _send_sysex([RSP_PONG, (up >> 7) & 0x7F, up & 0x7F])
    _hint("PING -> PONG")


def _sx_set_pattern_name(payload):
    if len(payload) < 2:
        _send_nack(CMD_SET_PATTERN_NAME, ERR_BAD_LENGTH); return
    slot   = payload[0]
    length = payload[1]
    name   = bytes(payload[2:2 + length]).decode("ascii", errors="replace")
    patterns.setPatternName(slot, name)
    _send_ack(CMD_SET_PATTERN_NAME)
    _hint(f"Pattern {slot + 1} ->'{name}'")


def _sx_pattern_info(payload):
    slot = payload[0] if payload else _state["current_pattern"]
    name = patterns.getPatternName(slot)
    nb   = [ord(c) for c in name[:32]]
    _send_sysex([RSP_ACK, CMD_PATTERN_INFO_REQ, slot, len(nb)] + nb)


def _sx_quantize(payload):
    grid = payload[0] if payload else 2
    transport.globalTransport(midi.FPT_Quantize, 1)
    _send_ack(CMD_QUANTIZE)
    _hint(f"Quantize ->{QUANTIZE_GRIDS.get(grid, '1/16')}")


def _sx_set_tempo(payload):
    if len(payload) < 2:
        _send_nack(CMD_SET_TEMPO, ERR_BAD_LENGTH); return
    bpm = (payload[0] << 7) | payload[1]    # 14-bit
    bpm = max(40, min(bpm, 999))
    transport.setTempo(bpm)
    _state["bpm"] = bpm
    _send_ack(CMD_SET_TEMPO)
    _hint(f"Tempo (SysEx) -> {bpm} BPM")


def _sx_channel_mute(payload):
    if len(payload) < 2:
        _send_nack(CMD_CHANNEL_MUTE, ERR_BAD_LENGTH); return
    ch, state = payload[0], payload[1]
    channels.muteChannel(ch, state > 0)
    _send_ack(CMD_CHANNEL_MUTE)
    _hint(f"Ch {ch} mute ->{'on' if state else 'off'}")


def _sx_channel_solo(payload):
    if len(payload) < 2:
        _send_nack(CMD_CHANNEL_SOLO, ERR_BAD_LENGTH); return
    ch, state = payload[0], payload[1]
    channels.soloChannel(ch, state > 0)
    _send_ack(CMD_CHANNEL_SOLO)
    _hint(f"Ch {ch} solo ->{'on' if state else 'off'}")


def _sx_note_event(payload):
    """Insert a note into the current pattern on the given channel."""
    if len(payload) < 5:
        _send_nack(CMD_NOTE_EVENT, ERR_BAD_LENGTH); return
    ch       = payload[0]
    note     = payload[1]
    velocity = payload[2]
    duration = (payload[3] << 7) | payload[4]   # 14-bit ticks
    channels.addNote(ch, note, velocity, 0, duration, -1)
    _send_ack(CMD_NOTE_EVENT)


def _sx_pattern_clear(payload):
    slot = payload[0] if payload else _state["current_pattern"]
    patterns.clearPattern(slot)
    _send_ack(CMD_PATTERN_CLEAR)
    _hint(f"Pattern {slot + 1} cleared")


def _sx_pattern_select(payload):
    slot = payload[0] if payload else 0
    _state["current_pattern"] = slot
    patterns.jumpToPattern(slot)
    _send_ack(CMD_PATTERN_SELECT)
    _hint(f"Pattern (SysEx) ->{slot + 1}")


def _sx_request_status(payload):
    """Send full transport + pattern state snapshot."""
    is_playing   = 1 if transport.isPlaying()   else 0
    is_recording = 1 if transport.isRecording() else 0
    loop_on      = 1 if transport.getLoopMode() else 0
    state_byte   = is_playing | (is_recording << 1) | (loop_on << 2)
    slot         = _state["current_pattern"] & 0x7F
    bpm          = _state.get("bpm", 140) & 0x3FFF
    _send_sysex([RSP_STATUS, state_byte, slot, (bpm >> 7) & 0x7F, bpm & 0x7F])


# ── SysEx helpers ─────────────────────────────────────────────────────────────

def _send_sysex(data):
    """Emit a SysEx response: F0 7D [data...] F7"""
    msg = bytes([0xF0, SYSEX_MANF_ID] + list(data) + [0xF7])
    device.midiOutSysex(msg)


def _send_ack(cmd):
    _send_sysex([RSP_ACK, cmd])


def _send_nack(cmd, error_code):
    _send_sysex([RSP_NACK, cmd, error_code])
    print(f"[{SCRIPT_NAME}] NACK cmd=0x{cmd:02X} err=0x{error_code:02X}")


def _hint(msg):
    ui.setHintMsg(f"{SCRIPT_NAME}: {msg}")
    print(f"[{SCRIPT_NAME}] {msg}")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE — Full protocol demo
# ══════════════════════════════════════════════════════════════════════════════
#
# This function is never called by FL Studio automatically; it exists as
# living documentation of every message shape the host sends via fl_transport.py.
#
# Run it manually from the FL Studio script console to test connectivity:
#   >>> import device_FL_Agent_Controller as ctrl
#   >>> ctrl.run_protocol_example()

def run_protocol_example():
    """
    Demonstrate the complete 3-tier FL MIDI Protocol.

    Shows the exact mido message objects that fl_transport.FLTransport sends
    for every supported operation.  The mock device (tests/mock_fl_studio.py)
    replicates the processing below without needing a real loopMIDI port.
    """
    import sys

    # Detect whether we can actually open a MIDI port
    try:
        import mido
        available = mido.get_output_names()
        port_name = next((p for p in available if "FL Agent" in p), None)
    except ImportError:
        port_name = None

    if port_name is None:
        print("No 'FL Agent' loopMIDI port found — running in dry-run mode.")
        print("Wire up loopMIDI or run the tests (pytest tests/) to simulate.\n")
        _print_example_messages()
        return

    with mido.open_output(port_name) as port:

        # ── Tier-1: Transport notes ────────────────────────────────────────────
        print("=== Tier 1: Transport Notes ===")

        print("  Sending START RECORDING (note 72)")
        port.send(mido.Message("note_on", note=72, velocity=100))

        import time
        time.sleep(0.1)

        print("  Sending PAUSE (note 73)")
        port.send(mido.Message("note_on", note=73, velocity=100))

        time.sleep(0.05)

        print("  Sending RESUME (note 73)")
        port.send(mido.Message("note_on", note=73, velocity=100))

        # ── Tier-2: CC parameters ──────────────────────────────────────────────
        print("\n=== Tier 2: CC Parameters ===")

        print("  CC 1  val=60  ->Tempo 120 BPM")
        port.send(mido.Message("control_change", control=1, value=60))

        print("  CC 20 val=2   ->Pattern slot 3")
        port.send(mido.Message("control_change", control=20, value=2))

        print("  CC 23 val=127 ->Loop ON")
        port.send(mido.Message("control_change", control=23, value=127))

        print("  CC 7  val=100 ->Master volume ~78%")
        port.send(mido.Message("control_change", control=7, value=100))

        # ── Tier-3: SysEx commands ─────────────────────────────────────────────
        print("\n=== Tier 3: SysEx Commands ===")

        def sysex(payload):
            port.send(mido.Message("sysex", data=bytes([SYSEX_MANF_ID] + payload)))

        print("  PING")
        sysex([0x01])
        time.sleep(0.01)

        # SET_TEMPO 140 BPM: 140 = 0b0000001_0001100 ->msb7=1, lsb7=12
        print("  SET_TEMPO 140 BPM")
        sysex([0x05, 1, 12])

        print("  PATTERN_SELECT slot=2")
        sysex([0x12, 2])

        # SET_PATTERN_NAME slot=0, name="Lead"
        name = "Lead"
        encoded = [ord(c) for c in name]
        print(f"  SET_PATTERN_NAME slot=0 ->'{name}'")
        sysex([0x02, 0, len(encoded)] + encoded)

        print("  CHANNEL_MUTE ch=3 enable=1")
        sysex([0x06, 3, 1])

        print("  QUANTIZE grid=2 (1/16)")
        sysex([0x04, 2])

        print("  NOTE_EVENT ch=0 note=60 vel=100 dur=480 ticks")
        dur_msb = (480 >> 7) & 0x7F
        dur_lsb = 480 & 0x7F
        sysex([0x10, 0, 60, 100, dur_msb, dur_lsb])

        print("  REQUEST_STATUS")
        sysex([0x13])

        time.sleep(0.05)

        # Stop recording
        print("\n  Sending STOP RECORDING (note 74)")
        port.send(mido.Message("note_on", note=74, velocity=100))

    print("\nExample complete.")


def _print_example_messages():
    """Print the wire-format of every protocol message for reference."""
    MANF = 0x7D
    print("FL MIDI Protocol — wire-format reference\n")

    tbl = [
        ("Tier-1", "START REC",        "note_on  note=72 vel=100"),
        ("Tier-1", "PAUSE/RESUME",      "note_on  note=73 vel=100"),
        ("Tier-1", "STOP REC",          "note_on  note=74 vel=100"),
        ("Tier-1", "REWIND",            "note_on  note=75 vel=100"),
        ("Tier-1", "LOOP TOGGLE",       "note_on  note=76 vel=100"),
        ("Tier-1", "METRO TOGGLE",      "note_on  note=77 vel=100"),
        ("Tier-2", "TEMPO 120",         "control_change ctrl=1  val=60"),
        ("Tier-2", "PATTERN SEL 3",     "control_change ctrl=20 val=2"),
        ("Tier-2", "LOOP ON",           "control_change ctrl=23 val=127"),
        ("Tier-2", "MASTER VOL 78%",    "control_change ctrl=7  val=100"),
        ("Tier-3", "PING",              f"sysex F0 {MANF:02X} 01 F7"),
        ("Tier-3", "SET_TEMPO 140",     f"sysex F0 {MANF:02X} 05 01 0C F7"),
        ("Tier-3", "PAT_SELECT 2",      f"sysex F0 {MANF:02X} 12 02 F7"),
        ("Tier-3", "SET_PAT_NAME Lead", f"sysex F0 {MANF:02X} 02 00 04 4C 65 61 64 F7"),
        ("Tier-3", "CH_MUTE 3 on",      f"sysex F0 {MANF:02X} 06 03 01 F7"),
        ("Tier-3", "QUANTIZE 1/16",     f"sysex F0 {MANF:02X} 04 02 F7"),
        ("Tier-3", "NOTE_EVENT C4",     f"sysex F0 {MANF:02X} 10 00 3C 64 03 60 F7"),
        ("Tier-3", "REQ_STATUS",        f"sysex F0 {MANF:02X} 13 F7"),
    ]
    for tier, label, wire in tbl:
        print(f"  [{tier}] {label:<22} {wire}")
