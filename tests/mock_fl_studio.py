"""
tests/mock_fl_studio.py — In-process FL Studio Simulator
=========================================================

Provides a faithful in-process simulation of FL Studio with the
device_FL_Agent_Controller.py script loaded.  No real MIDI ports,
no loopMIDI, no Windows-only dependencies needed.

Architecture
────────────

  ┌──────────────────────────────────────┐
  │         Test / Client code           │
  │  MockFLTransport.start_recording()   │
  └─────────────────┬────────────────────┘
                    │ receive(mido.Message)
  ┌─────────────────▼────────────────────┐
  │         MockFLStudio                 │
  │                                      │
  │  _handle_message()  ← mirrors        │
  │  OnMidiMsg() / OnSysEx() exactly     │
  │                                      │
  │  TransportState  (mutable dataclass) │
  │   • is_playing / is_recording        │
  │   • loop_on / metronome_on           │
  │   • bpm / current_pattern            │
  │   • pattern_names / muted_channels   │
  │   • events_received / cleared_pats   │
  │                                      │
  │  _responses []  — SysEx reply queue  │
  └──────────────────────────────────────┘

Usage
─────

  from tests.mock_fl_studio import MockFLStudio
  from tests.mock_fl_transport import MockFLTransport

  with MockFLStudio() as fl:
      client = MockFLTransport(fl)
      client.start_recording()
      assert fl.state.is_playing

  # Or without context manager:
  fl = MockFLStudio()
  fl.start()
  # ... drive via receive() directly ...
  fl.stop()
"""

import threading
import queue
import time
from dataclasses import dataclass, field
from typing import Optional

import mido

# ── Protocol constants (mirrors device_FL_Agent_Controller.py) ─────────────────

SYSEX_MANF_ID = 0x7D

# Tier-1 Transport Notes
NOTE_START_REC  = 72
NOTE_PAUSE      = 73
NOTE_STOP_REC   = 74
NOTE_REWIND     = 75
NOTE_LOOP       = 76
NOTE_METRONOME  = 77

# Tier-2 CC controls
CC_TEMPO        = 1
CC_MASTER_VOL   = 7
CC_PATTERN_LEN  = 11
CC_PATTERN_SEL  = 20
CC_TIMESIG_NUM  = 21
CC_TIMESIG_DEN  = 22
CC_LOOP         = 23
CC_METRONOME_CC = 24

# Tier-3 SysEx commands
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

# Tier-3 SysEx responses
RSP_ACK    = 0x80
RSP_NACK   = 0x81
RSP_PONG   = 0x82
RSP_STATUS = 0x83

# Error codes
ERR_UNKNOWN_CMD  = 0x01
ERR_BAD_LENGTH   = 0x02
ERR_OUT_OF_RANGE = 0x03
ERR_NOT_READY    = 0x04


# ── Transport state dataclass ─────────────────────────────────────────────────

@dataclass
class TransportState:
    """Mutable state snapshot of the simulated FL Studio transport."""
    is_playing:      bool  = False
    is_recording:    bool  = False
    loop_on:         bool  = False
    metronome_on:    bool  = False
    bpm:             int   = 120
    current_pattern: int   = 0
    master_volume:   float = 1.0

    # Pattern metadata
    pattern_names:   dict  = field(default_factory=dict)  # slot → name str

    # Channel state
    muted_channels:  set   = field(default_factory=set)   # set of channel ints
    soloed_channels: set   = field(default_factory=set)

    # History (accumulated across all messages)
    events_received:   list = field(default_factory=list)  # list of note dicts
    cleared_patterns:  list = field(default_factory=list)  # list of slot ints
    quantize_calls:    list = field(default_factory=list)  # list of grid ints
    rewind_count:      int  = 0


# ══════════════════════════════════════════════════════════════════════════════
# MockFLStudio
# ══════════════════════════════════════════════════════════════════════════════

class MockFLStudio:
    """
    In-process simulation of FL Studio + FL Agent Controller.

    Runs a background thread that processes injected mido.Message objects
    exactly as device_FL_Agent_Controller.py does in OnMidiMsg / OnSysEx.

    Thread safety
    ─────────────
    - receive() is safe to call from any thread.
    - state is mutated only from the background thread, so reads from the
      test thread should add a small sleep / call flush() to avoid races.
    - _responses is protected by a lock; get_responses() / wait_for_response()
      are safe from any thread.
    """

    def __init__(self):
        self.state = TransportState()
        self._start_time = time.monotonic()

        self._responses: list[bytes] = []
        self._response_lock = threading.Lock()

        self._msg_queue: "queue.Queue[Optional[mido.Message]]" = queue.Queue()
        self._running   = False
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def __enter__(self) -> "MockFLStudio":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    def start(self) -> "MockFLStudio":
        """Start the background processing thread."""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="MockFLStudio")
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the background thread gracefully."""
        self._running = False
        self._msg_queue.put(None)   # sentinel
        if self._thread:
            self._thread.join(timeout=2.0)

    def flush(self, ms: float = 50) -> None:
        """Block until the message queue drains (up to ms milliseconds)."""
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            if self._msg_queue.empty():
                time.sleep(0.005)
                break
            time.sleep(0.002)

    # ── Message injection ─────────────────────────────────────────────────────

    def receive(self, msg: mido.Message) -> None:
        """Inject a mido.Message for processing.  Safe from any thread."""
        self._msg_queue.put(msg)

    # ── Response access ───────────────────────────────────────────────────────

    def get_responses(self) -> list[bytes]:
        """Return and clear all pending SysEx responses."""
        with self._response_lock:
            r = list(self._responses)
            self._responses.clear()
            return r

    def wait_for_response(self, timeout: float = 1.0) -> Optional[bytes]:
        """Block until at least one response arrives or timeout expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._response_lock:
                if self._responses:
                    return self._responses.pop(0)
            time.sleep(0.005)
        return None

    def clear_responses(self) -> None:
        """Discard all buffered responses."""
        with self._response_lock:
            self._responses.clear()

    # ── Background loop ───────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                msg = self._msg_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if msg is None:
                break
            self._handle_message(msg)

    # ── Message dispatch (mirrors device_FL_Agent_Controller.py) ──────────────

    def _handle_message(self, msg: mido.Message) -> None:
        if msg.type == "note_on" and msg.velocity > 0:
            self._handle_note_on(msg)
        elif msg.type == "control_change":
            self._handle_cc(msg)
        elif msg.type == "sysex":
            self._handle_sysex(msg)

    # ══════════════════════════════════════════════════════════════════════════
    # Tier-1: Note-based transport (mirrors _handle_note_on)
    # ══════════════════════════════════════════════════════════════════════════

    def _handle_note_on(self, msg: mido.Message) -> None:
        note = msg.note
        dispatch = {
            NOTE_START_REC : self._t1_start_recording,
            NOTE_PAUSE     : self._t1_pause_resume,
            NOTE_STOP_REC  : self._t1_stop_recording,
            NOTE_REWIND    : self._t1_rewind,
            NOTE_LOOP      : self._t1_toggle_loop,
            NOTE_METRONOME : self._t1_toggle_metronome,
        }
        fn = dispatch.get(note)
        if fn:
            fn()

    def _t1_start_recording(self):
        self.state.is_recording = True
        self.state.is_playing   = True

    def _t1_stop_recording(self):
        self.state.is_recording = False
        self.state.is_playing   = False

    def _t1_pause_resume(self):
        self.state.is_playing = not self.state.is_playing

    def _t1_rewind(self):
        self.state.rewind_count += 1

    def _t1_toggle_loop(self):
        self.state.loop_on = not self.state.loop_on

    def _t1_toggle_metronome(self):
        self.state.metronome_on = not self.state.metronome_on

    # ══════════════════════════════════════════════════════════════════════════
    # Tier-2: CC-based parameters (mirrors _handle_cc)
    # ══════════════════════════════════════════════════════════════════════════

    def _handle_cc(self, msg: mido.Message) -> None:
        cc, val = msg.control, msg.value
        if   cc == CC_TEMPO       : self.state.bpm = 60 + val
        elif cc == CC_MASTER_VOL  : self.state.master_volume = val / 127.0
        elif cc == CC_PATTERN_SEL : self.state.current_pattern = val
        elif cc == CC_LOOP        : self.state.loop_on = (val >= 64)
        elif cc == CC_METRONOME_CC: self.state.metronome_on = (val >= 64)

    # ══════════════════════════════════════════════════════════════════════════
    # Tier-3: SysEx commands (mirrors OnSysEx → _dispatch_sysex)
    # ══════════════════════════════════════════════════════════════════════════

    def _handle_sysex(self, msg: mido.Message) -> None:
        # mido stores SysEx data without F0/F7, as a tuple of ints
        data = list(msg.data)
        if len(data) < 2 or data[0] != SYSEX_MANF_ID:
            return
        cmd     = data[1]
        payload = data[2:]
        self._dispatch_sysex(cmd, payload)

    def _dispatch_sysex(self, cmd: int, payload: list) -> None:
        handlers = {
            CMD_PING             : self._sx_ping,
            CMD_SET_PATTERN_NAME : self._sx_set_pattern_name,
            CMD_PATTERN_INFO_REQ : self._sx_pattern_info,
            CMD_QUANTIZE         : self._sx_quantize,
            CMD_SET_TEMPO        : self._sx_set_tempo,
            CMD_CHANNEL_MUTE     : self._sx_channel_mute,
            CMD_CHANNEL_SOLO     : self._sx_channel_solo,
            CMD_NOTE_EVENT       : self._sx_note_event,
            CMD_PATTERN_CLEAR    : self._sx_pattern_clear,
            CMD_PATTERN_SELECT   : self._sx_pattern_select,
            CMD_REQUEST_STATUS   : self._sx_request_status,
        }
        handler = handlers.get(cmd)
        if handler is None:
            # Silently discard unknown commands (mirrors device_FL_Agent_Controller.py)
            return
        try:
            handler(payload)
        except Exception as exc:
            print(f"[MockFLStudio] SysEx 0x{cmd:02X} error: {exc}")
            self._push(bytes([RSP_NACK, cmd, ERR_NOT_READY]))

    def _sx_ping(self, _):
        uptime = int((time.monotonic() - self._start_time) * 1000) & 0x3FFF
        self._push(bytes([RSP_PONG, (uptime >> 7) & 0x7F, uptime & 0x7F]))

    def _sx_set_pattern_name(self, payload):
        if len(payload) < 2:
            self._push(bytes([RSP_NACK, CMD_SET_PATTERN_NAME, ERR_BAD_LENGTH]))
            return
        slot   = payload[0]
        length = payload[1]
        name   = bytes(payload[2:2 + length]).decode("ascii", errors="replace")
        self.state.pattern_names[slot] = name
        self._push(bytes([RSP_ACK, CMD_SET_PATTERN_NAME]))

    def _sx_pattern_info(self, payload):
        slot = payload[0] if payload else self.state.current_pattern
        name = self.state.pattern_names.get(slot, "")
        nb   = [ord(c) for c in name]
        self._push(bytes([RSP_ACK, CMD_PATTERN_INFO_REQ, slot, len(nb)] + nb))

    def _sx_quantize(self, payload):
        grid = payload[0] if payload else 2
        self.state.quantize_calls.append(grid)
        self._push(bytes([RSP_ACK, CMD_QUANTIZE]))

    def _sx_set_tempo(self, payload):
        if len(payload) < 2:
            self._push(bytes([RSP_NACK, CMD_SET_TEMPO, ERR_BAD_LENGTH]))
            return
        bpm = (payload[0] << 7) | payload[1]
        self.state.bpm = max(40, min(bpm, 999))
        self._push(bytes([RSP_ACK, CMD_SET_TEMPO]))

    def _sx_channel_mute(self, payload):
        if len(payload) < 2:
            self._push(bytes([RSP_NACK, CMD_CHANNEL_MUTE, ERR_BAD_LENGTH]))
            return
        ch, state = payload[0], payload[1]
        if state:
            self.state.muted_channels.add(ch)
        else:
            self.state.muted_channels.discard(ch)
        self._push(bytes([RSP_ACK, CMD_CHANNEL_MUTE]))

    def _sx_channel_solo(self, payload):
        if len(payload) < 2:
            self._push(bytes([RSP_NACK, CMD_CHANNEL_SOLO, ERR_BAD_LENGTH]))
            return
        ch, state = payload[0], payload[1]
        if state:
            self.state.soloed_channels.add(ch)
        else:
            self.state.soloed_channels.discard(ch)
        self._push(bytes([RSP_ACK, CMD_CHANNEL_SOLO]))

    def _sx_note_event(self, payload):
        if len(payload) < 5:
            self._push(bytes([RSP_NACK, CMD_NOTE_EVENT, ERR_BAD_LENGTH]))
            return
        ch       = payload[0]
        note     = payload[1]
        velocity = payload[2]
        duration = (payload[3] << 7) | payload[4]
        self.state.events_received.append({
            "ch": ch, "note": note, "vel": velocity, "dur": duration
        })
        self._push(bytes([RSP_ACK, CMD_NOTE_EVENT]))

    def _sx_pattern_clear(self, payload):
        slot = payload[0] if payload else self.state.current_pattern
        self.state.cleared_patterns.append(slot)
        self._push(bytes([RSP_ACK, CMD_PATTERN_CLEAR]))

    def _sx_pattern_select(self, payload):
        slot = payload[0] if payload else 0
        self.state.current_pattern = slot
        self._push(bytes([RSP_ACK, CMD_PATTERN_SELECT]))

    def _sx_request_status(self, _):
        s = self.state
        state_byte = (
            (1 if s.is_playing   else 0) |
            (2 if s.is_recording else 0) |
            (4 if s.loop_on      else 0) |
            (8 if s.metronome_on else 0)
        )
        bpm = s.bpm & 0x3FFF
        self._push(bytes([
            RSP_STATUS,
            state_byte,
            s.current_pattern & 0x7F,
            (bpm >> 7) & 0x7F,
            bpm & 0x7F,
        ]))

    # ── Response queue ────────────────────────────────────────────────────────

    def _push(self, data: bytes) -> None:
        """Queue a SysEx response (called from background thread)."""
        with self._response_lock:
            self._responses.append(data)
