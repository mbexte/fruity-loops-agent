# name= FL Agent Controller
# device_FL_Agent_Controller.py — FL Agent MIDI Controller Script
# Version: 2.2
# Author:  FL Agent
#
# INSTALLATION (required folder structure):
#   1. Create folder:
#        Documents\Image-Line\FL Studio\Settings\Hardware\FL Agent Controller\
#   2. Copy THIS file into that folder.
#   3. Restart FL Studio.
#   4. Go to Options > MIDI Settings > Input tab.
#   5. Select your loopMIDI "FL Agent" port.
#   6. Set "Controller type" dropdown to "FL Agent Controller".
#   7. Enable the port (checkbox on the left).
#
# Protocol — Tier 1 (note-based transport):
#   Note 72 (C5)   -> START recording
#   Note 73 (C#5)  -> PAUSE / RESUME
#   Note 74 (D5)   -> STOP recording
#   Note 75 (D#5)  -> REWIND to bar 1
#   Note 76 (E5)   -> LOOP toggle
#   Note 77 (F5)   -> METRONOME toggle
#
# Protocol — Tier 2 (CC):
#   CC 1  -> tempo (value 0-127 -> BPM 60-187)
#
# Protocol — Tier 3 (SysEx, header: F0 7D [cmd] [payload...] F7):
#   0x01  PING
#   0x02  SET_PATTERN_NAME  [slot] [len] [ascii...]
#   0x03  PATTERN_INFO_REQ  [slot]
#   0x05  SET_TEMPO         [bpm_msb7] [bpm_lsb7]
#   0x06  CHANNEL_MUTE      [ch] [state]
#   0x07  CHANNEL_SOLO      [ch] [state]
#   0x11  PATTERN_CLEAR     [slot]
#   0x12  PATTERN_SELECT    [slot]
#   0x13  REQUEST_STATUS
#
# Only midi, transport, ui are imported at module level.
# All other FL modules (patterns, channels, device) are imported lazily
# inside each function so a missing module cannot prevent the script loading.

import midi
import transport
import ui

# ── constants ─────────────────────────────────────────────────────────────────

SCRIPT_NAME   = "FL Agent Controller"
SYSEX_MANF_ID = 0x7D

NOTE_START_REC = 72
NOTE_PAUSE     = 73
NOTE_STOP_REC  = 74
NOTE_REWIND    = 75
NOTE_LOOP      = 76
NOTE_METRONOME = 77
TRANSPORT_NOTES = frozenset({72, 73, 74, 75, 76, 77})

CMD_PING             = 0x01
CMD_SET_PATTERN_NAME = 0x02
CMD_PATTERN_INFO_REQ = 0x03
CMD_SET_TEMPO        = 0x05
CMD_CHANNEL_MUTE     = 0x06
CMD_CHANNEL_SOLO     = 0x07
CMD_PATTERN_CLEAR    = 0x11
CMD_PATTERN_SELECT   = 0x12
CMD_REQUEST_STATUS   = 0x13

RSP_ACK    = 0x80
RSP_NACK   = 0x81
RSP_PONG   = 0x82
RSP_STATUS = 0x83

_state = {
    "start_ms":       0,
    "current_pattern": 0,
    "bpm":            120,   # tracked locally; transport.getTempo() may not exist
}

# ── lifecycle ─────────────────────────────────────────────────────────────────

def OnInit():
    import time as _t
    _state["start_ms"] = int(_t.monotonic() * 1000)
    print("[FL Agent] v2.2 loaded — notes 72-77 reserved, SysEx manf=0x7D")
    ui.setHintMsg(SCRIPT_NAME + " ready")


def OnDeInit():
    print("[FL Agent] unloaded")


# ── MIDI routing ──────────────────────────────────────────────────────────────

def OnMidiMsg(event):
    mid  = event.midiId
    note = event.note

    # Tier 1 — transport notes
    if mid == midi.MIDI_NOTEON or mid == midi.MIDI_NOTEOFF:
        if note in TRANSPORT_NOTES:
            event.handled = True
            if mid == midi.MIDI_NOTEON and event.velocity > 0:
                _on_transport(note)
        return

    # Tier 2 — CC 1 sets tempo
    if mid == midi.MIDI_CONTROLCHANGE and event.controlNum == 1:
        bpm = 60 + event.controlVal
        _state["bpm"] = bpm
        transport.setTempo(bpm)
        print("[FL Agent] CC1 tempo -> " + str(bpm) + " BPM")
        ui.setHintMsg(SCRIPT_NAME + ": tempo " + str(bpm) + " BPM")
        event.handled = True


def OnSysEx(event):
    raw  = event.sysex
    data = list(raw)

    # Log raw hex so we can see exactly what arrived (including F0/F7 if present)
    print("[FL Agent] SysEx raw (" + str(len(data)) + "b): " +
          " ".join(format(b, "02X") for b in data))

    # Strip F0 / F7 framing bytes if present — FL Studio includes them, mido may not
    if data and data[0] == 0xF0:
        data = data[1:]
    if data and data[-1] == 0xF7:
        data = data[:-1]

    print("[FL Agent] SysEx stripped: " + " ".join(format(b, "02X") for b in data))

    if len(data) < 2 or data[0] != SYSEX_MANF_ID:
        print("[FL Agent] SysEx: not ours (manf=" +
              (format(data[0], "02X") if data else "empty") + ")")
        return

    # Mark handled for ALL our messages before any early return — prevents FL Studio
    # MIDI-through from re-routing these bytes back out the port into an echo loop.
    event.handled = True

    cmd = data[1]
    print("[FL Agent] SysEx cmd=0x" + format(cmd, "02X") +
          (" (response, ignored)" if cmd >= RSP_ACK else ""))

    # Ignore loopback responses (0x80+)
    if cmd >= RSP_ACK:
        return

    payload = data[2:]
    print("[FL Agent] SysEx payload (" + str(len(payload)) + "b): " +
          " ".join(format(b, "02X") for b in payload))

    try:
        _dispatch_sysex(cmd, payload)
    except Exception as exc:
        print("[FL Agent] SysEx dispatch error cmd=0x" + format(cmd, "02X") + ": " + str(exc))


# ── SysEx dispatch ────────────────────────────────────────────────────────────

def _dispatch_sysex(cmd, payload):
    if   cmd == CMD_PING:             _sx_ping()
    elif cmd == CMD_SET_PATTERN_NAME: _sx_set_pattern_name(payload)
    elif cmd == CMD_PATTERN_INFO_REQ: _sx_pattern_info(payload)
    elif cmd == CMD_SET_TEMPO:        _sx_set_tempo(payload)
    elif cmd == CMD_CHANNEL_MUTE:     _sx_channel_mute(payload)
    elif cmd == CMD_CHANNEL_SOLO:     _sx_channel_solo(payload)
    elif cmd == CMD_PATTERN_CLEAR:    _sx_pattern_clear(payload)
    elif cmd == CMD_PATTERN_SELECT:   _sx_pattern_select(payload)
    elif cmd == CMD_REQUEST_STATUS:   _sx_request_status()
    else:
        print("[FL Agent] SysEx: unknown cmd=0x" + format(cmd, "02X"))


def _send_sysex(data):
    try:
        import device as _dev
        msg = bytes([0xF0, SYSEX_MANF_ID] + list(data) + [0xF7])
        _dev.midiOutSysex(msg)
        print("[FL Agent] SysEx out: " + str(list(data)[:8]))
    except Exception as exc:
        print("[FL Agent] SysEx send error: " + str(exc))


# ── SysEx handlers ────────────────────────────────────────────────────────────

def _sx_ping():
    import time as _t
    up = int(_t.monotonic() * 1000) - _state["start_ms"]
    up &= 0x3FFF
    _send_sysex([RSP_PONG, (up >> 7) & 0x7F, up & 0x7F])
    print("[FL Agent] PING -> PONG uptime=" + str(up) + "ms")
    ui.setHintMsg(SCRIPT_NAME + ": PONG")


def _sx_set_tempo(payload):
    if len(payload) < 2:
        print("[FL Agent] SET_TEMPO: payload too short (" + str(len(payload)) + " bytes)")
        return
    bpm = (payload[0] << 7) | payload[1]
    bpm = max(40, min(bpm, 999))
    _state["bpm"] = bpm
    transport.setTempo(bpm)
    print("[FL Agent] SET_TEMPO -> " + str(bpm) + " BPM")
    ui.setHintMsg(SCRIPT_NAME + ": tempo " + str(bpm) + " BPM")
    _send_sysex([RSP_ACK, CMD_SET_TEMPO])


def _sx_pattern_select(payload):
    slot = payload[0] if payload else 0
    _state["current_pattern"] = slot
    print("[FL Agent] PATTERN_SELECT slot=" + str(slot))
    try:
        import patterns as _pat
        _pat.jumpToPattern(slot)
        print("[FL Agent] jumpToPattern(" + str(slot) + ") OK")
        ui.setHintMsg(SCRIPT_NAME + ": pattern " + str(slot + 1))
        _send_sysex([RSP_ACK, CMD_PATTERN_SELECT])
    except Exception as exc:
        print("[FL Agent] PATTERN_SELECT error: " + str(exc))
        _send_sysex([RSP_NACK, CMD_PATTERN_SELECT, 0x04])


def _sx_set_pattern_name(payload):
    if len(payload) < 2:
        print("[FL Agent] SET_PATTERN_NAME: payload too short (" + str(len(payload)) + "), need slot+len")
        _send_sysex([RSP_NACK, CMD_SET_PATTERN_NAME, 0x02])
        return
    slot   = payload[0]
    length = payload[1]
    name   = bytes(payload[2:2 + length]).decode("ascii", errors="replace")
    print("[FL Agent] SET_PATTERN_NAME slot=" + str(slot) + " name='" + name + "'")
    try:
        import patterns as _pat
        # Try the most common API name first
        if hasattr(_pat, "setPatternName"):
            _pat.setPatternName(slot, name)
            print("[FL Agent] setPatternName OK")
        elif hasattr(_pat, "setGroupName"):
            _pat.setGroupName(slot, name)
            print("[FL Agent] setGroupName OK (fallback)")
        else:
            attrs = [a for a in dir(_pat) if "name" in a.lower() or "set" in a.lower()]
            print("[FL Agent] SET_PATTERN_NAME: no setter found, available: " + str(attrs))
            _send_sysex([RSP_NACK, CMD_SET_PATTERN_NAME, 0x01])
            return
        ui.setHintMsg(SCRIPT_NAME + ": pattern " + str(slot + 1) + " = " + name)
        _send_sysex([RSP_ACK, CMD_SET_PATTERN_NAME])
    except Exception as exc:
        print("[FL Agent] SET_PATTERN_NAME error: " + str(exc))
        _send_sysex([RSP_NACK, CMD_SET_PATTERN_NAME, 0x04])


def _sx_pattern_clear(payload):
    slot = payload[0] if payload else _state["current_pattern"]
    print("[FL Agent] PATTERN_CLEAR slot=" + str(slot))
    try:
        import patterns as _pat
        if hasattr(_pat, "clearPattern"):
            _pat.clearPattern(slot)
            print("[FL Agent] clearPattern(" + str(slot) + ") OK")
        else:
            attrs = [a for a in dir(_pat) if "clear" in a.lower() or "delete" in a.lower()]
            print("[FL Agent] PATTERN_CLEAR: no clear fn found, available: " + str(attrs))
            _send_sysex([RSP_NACK, CMD_PATTERN_CLEAR, 0x01])
            return
        ui.setHintMsg(SCRIPT_NAME + ": pattern " + str(slot + 1) + " cleared")
        _send_sysex([RSP_ACK, CMD_PATTERN_CLEAR])
    except Exception as exc:
        print("[FL Agent] PATTERN_CLEAR error: " + str(exc))
        _send_sysex([RSP_NACK, CMD_PATTERN_CLEAR, 0x04])


def _sx_pattern_info(payload):
    slot = payload[0] if payload else _state["current_pattern"]
    print("[FL Agent] PATTERN_INFO_REQ slot=" + str(slot))
    try:
        import patterns as _pat
        name = _pat.getPatternName(slot)
        nb   = [ord(c) for c in str(name)[:32]]
        _send_sysex([RSP_ACK, CMD_PATTERN_INFO_REQ, slot, len(nb)] + nb)
        print("[FL Agent] PATTERN_INFO slot=" + str(slot) + " name='" + str(name) + "'")
    except Exception as exc:
        print("[FL Agent] PATTERN_INFO error: " + str(exc))


def _sx_channel_mute(payload):
    if len(payload) < 2:
        print("[FL Agent] CHANNEL_MUTE: payload too short")
        return
    ch, state = payload[0], payload[1]
    print("[FL Agent] CHANNEL_MUTE ch=" + str(ch) + " state=" + str(state))
    try:
        import channels as _ch
        sig = str(type(_ch.muteChannel))
        print("[FL Agent] muteChannel signature hint: " + sig)
        try:
            _ch.muteChannel(ch, state > 0)
            print("[FL Agent] muteChannel(ch, state) OK")
        except TypeError:
            _ch.muteChannel(ch)
            print("[FL Agent] muteChannel(ch) toggle OK")
        ui.setHintMsg(SCRIPT_NAME + ": ch" + str(ch) + " mute=" + str(bool(state)))
        _send_sysex([RSP_ACK, CMD_CHANNEL_MUTE])
    except Exception as exc:
        print("[FL Agent] CHANNEL_MUTE error: " + str(exc))
        _send_sysex([RSP_NACK, CMD_CHANNEL_MUTE, 0x04])


def _sx_channel_solo(payload):
    if len(payload) < 2:
        print("[FL Agent] CHANNEL_SOLO: payload too short")
        return
    ch, state = payload[0], payload[1]
    print("[FL Agent] CHANNEL_SOLO ch=" + str(ch) + " state=" + str(state))
    try:
        import channels as _ch
        try:
            _ch.soloChannel(ch, state > 0)
            print("[FL Agent] soloChannel(ch, state) OK")
        except TypeError:
            _ch.soloChannel(ch)
            print("[FL Agent] soloChannel(ch) toggle OK")
        ui.setHintMsg(SCRIPT_NAME + ": ch" + str(ch) + " solo=" + str(bool(state)))
        _send_sysex([RSP_ACK, CMD_CHANNEL_SOLO])
    except Exception as exc:
        print("[FL Agent] CHANNEL_SOLO error: " + str(exc))
        _send_sysex([RSP_NACK, CMD_CHANNEL_SOLO, 0x04])


def _sx_request_status():
    is_playing   = 1 if transport.isPlaying()   else 0
    is_recording = 1 if transport.isRecording() else 0
    loop_on      = 0
    try:
        loop_on = 1 if transport.getLoopMode() else 0
    except Exception as exc:
        print("[FL Agent] getLoopMode error (ignored): " + str(exc))
    state_byte = is_playing | (is_recording << 1) | (loop_on << 2)
    slot       = _state.get("current_pattern", 0) & 0x7F
    bpm        = _state.get("bpm", 120) & 0x3FFF
    _send_sysex([RSP_STATUS, state_byte, slot, (bpm >> 7) & 0x7F, bpm & 0x7F])
    print("[FL Agent] STATUS: playing=" + str(is_playing) +
          " recording=" + str(is_recording) + " bpm=" + str(_state.get("bpm", 120)))


# ── transport actions (Tier 1) ────────────────────────────────────────────────

def _on_transport(note):
    if note == NOTE_START_REC:
        if not transport.isRecording():
            transport.record()
        transport.start()
        print("[FL Agent] Recording STARTED")
        ui.setHintMsg(SCRIPT_NAME + ": Recording STARTED")

    elif note == NOTE_STOP_REC:
        transport.stop()
        print("[FL Agent] Recording STOPPED")
        ui.setHintMsg(SCRIPT_NAME + ": Recording STOPPED")

    elif note == NOTE_PAUSE:
        if transport.isPlaying():
            transport.stop()
            print("[FL Agent] Paused")
            ui.setHintMsg(SCRIPT_NAME + ": Paused")
        else:
            transport.start()
            print("[FL Agent] Resumed")
            ui.setHintMsg(SCRIPT_NAME + ": Resumed")

    elif note == NOTE_REWIND:
        transport.setSongPos(0)
        print("[FL Agent] Rewound to bar 1")
        ui.setHintMsg(SCRIPT_NAME + ": Rewound")

    elif note == NOTE_LOOP:
        transport.globalTransport(midi.FPT_LoopRecord, 1)
        print("[FL Agent] Loop toggled")
        ui.setHintMsg(SCRIPT_NAME + ": Loop toggled")

    elif note == NOTE_METRONOME:
        transport.globalTransport(midi.FPT_Metronome, 1)
        print("[FL Agent] Metronome toggled")
        ui.setHintMsg(SCRIPT_NAME + ": Metronome toggled")
