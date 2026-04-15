"""
tests/mock_fl_transport.py — Queue-Routed Transport Stub
=========================================================

MockFLTransport is a drop-in replacement for fl_transport.FLTransport that
routes every message directly to a MockFLStudio instance via its receive()
queue, bypassing loopMIDI entirely.

This means tests run:
  • cross-platform (Linux / macOS / Windows)
  • without any MIDI drivers installed
  • without FL Studio
  • deterministically and quickly

Usage
─────

  from tests.mock_fl_studio    import MockFLStudio
  from tests.mock_fl_transport import MockFLTransport

  with MockFLStudio() as fl:
      t = MockFLTransport(fl)
      t.start_recording()
      fl.flush()
      assert fl.state.is_playing

API parity
──────────
MockFLTransport exposes exactly the same methods as FLTransport so that
production code under test can accept either class.
"""

from __future__ import annotations

import mido

# Protocol constants (mirrors fl_transport.py)
SYSEX_MANF_ID   = 0x7D

NOTE_START_REC  = 72
NOTE_PAUSE      = 73
NOTE_STOP_REC   = 74
NOTE_REWIND     = 75
NOTE_LOOP       = 76
NOTE_METRONOME  = 77

CC_TEMPO        = 1
CC_MASTER_VOL   = 7
CC_PATTERN_LEN  = 11
CC_PATTERN_SEL  = 20
CC_TIMESIG_NUM  = 21
CC_TIMESIG_DEN  = 22
CC_LOOP         = 23
CC_METRONOME_CC = 24

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

QUANTIZE_1_4  = 0
QUANTIZE_1_8  = 1
QUANTIZE_1_16 = 2
QUANTIZE_1_32 = 3


class MockFLTransport:
    """
    Mirrors fl_transport.FLTransport but routes messages to a MockFLStudio.

    Parameters
    ──────────
    fl_studio : MockFLStudio
        The simulator instance that will receive and process messages.
    velocity  : int
        Default Note-On velocity for Tier-1 transport notes.
    """

    def __init__(self, fl_studio, velocity: int = 100):
        self._fl       = fl_studio
        self.velocity  = velocity

    # ── Context manager (API parity with FLTransport) ─────────────────────────

    def __enter__(self) -> "MockFLTransport":
        return self

    def __exit__(self, *_) -> None:
        pass

    def open(self) -> "MockFLTransport":
        return self

    def close(self) -> None:
        pass

    # ── Internal send ─────────────────────────────────────────────────────────

    def _send(self, msg: mido.Message) -> None:
        self._fl.receive(msg)

    # ══════════════════════════════════════════════════════════════════════════
    # TIER 1 — Note-based transport
    # ══════════════════════════════════════════════════════════════════════════

    def start_recording(self) -> None:
        """Note 72 → FL Studio arms + starts recording."""
        self._send(mido.Message("note_on", note=NOTE_START_REC, velocity=self.velocity))

    def stop_recording(self) -> None:
        """Note 74 → FL Studio stops recording."""
        self._send(mido.Message("note_on", note=NOTE_STOP_REC, velocity=self.velocity))

    def pause_resume(self) -> None:
        """Note 73 → pause if playing, resume if stopped."""
        self._send(mido.Message("note_on", note=NOTE_PAUSE, velocity=self.velocity))

    def rewind(self) -> None:
        """Note 75 → rewind to bar 1."""
        self._send(mido.Message("note_on", note=NOTE_REWIND, velocity=self.velocity))

    def toggle_loop(self) -> None:
        """Note 76 → toggle loop mode."""
        self._send(mido.Message("note_on", note=NOTE_LOOP, velocity=self.velocity))

    def toggle_metronome(self) -> None:
        """Note 77 → toggle metronome."""
        self._send(mido.Message("note_on", note=NOTE_METRONOME, velocity=self.velocity))

    # ══════════════════════════════════════════════════════════════════════════
    # TIER 2 — CC-based parameters
    # ══════════════════════════════════════════════════════════════════════════

    def set_tempo_cc(self, bpm: int) -> None:
        """CC 1: BPM 60–187 → value 0–127."""
        val = max(0, min(127, bpm - 60))
        self._send(mido.Message("control_change", control=CC_TEMPO, value=val))

    def select_pattern_cc(self, slot: int) -> None:
        """CC 20: select pattern slot 0–127."""
        self._send(mido.Message("control_change", control=CC_PATTERN_SEL,
                                value=max(0, min(127, slot))))

    def set_loop_cc(self, enable: bool) -> None:
        """CC 23: enable (127) / disable (0) loop."""
        self._send(mido.Message("control_change", control=CC_LOOP,
                                value=127 if enable else 0))

    def set_master_volume(self, level: float) -> None:
        """CC 7: master volume 0.0–1.0."""
        val = max(0, min(127, int(level * 127)))
        self._send(mido.Message("control_change", control=CC_MASTER_VOL, value=val))

    # ══════════════════════════════════════════════════════════════════════════
    # TIER 3 — SysEx structured commands
    # ══════════════════════════════════════════════════════════════════════════

    def _sysex(self, payload: list) -> None:
        """Emit F0 7D [payload...] F7 as a mido sysex message."""
        data = tuple([SYSEX_MANF_ID] + [int(b) for b in payload])
        self._send(mido.Message("sysex", data=data))

    def ping(self) -> None:
        """SysEx 0x01 PING."""
        self._sysex([CMD_PING])

    def set_tempo(self, bpm: int) -> None:
        """SysEx 0x05 SET_TEMPO — 14-bit BPM (40–999)."""
        bpm = max(40, min(999, int(bpm)))
        self._sysex([CMD_SET_TEMPO, (bpm >> 7) & 0x7F, bpm & 0x7F])

    def select_pattern(self, slot: int) -> None:
        """SysEx 0x12 PATTERN_SELECT."""
        self._sysex([CMD_PATTERN_SELECT, slot & 0x7F])

    def set_pattern_name(self, slot: int, name: str) -> None:
        """SysEx 0x02 SET_PATTERN_NAME (max 32 ASCII chars)."""
        encoded = [ord(c) for c in name[:32]]
        self._sysex([CMD_SET_PATTERN_NAME, slot & 0x7F, len(encoded)] + encoded)

    def pattern_info(self, slot: int) -> None:
        """SysEx 0x03 PATTERN_INFO_REQ."""
        self._sysex([CMD_PATTERN_INFO_REQ, slot & 0x7F])

    def quantize(self, grid: int = QUANTIZE_1_16) -> None:
        """SysEx 0x04 QUANTIZE — 0=1/4  1=1/8  2=1/16  3=1/32."""
        self._sysex([CMD_QUANTIZE, grid & 0x03])

    def pattern_clear(self, slot: int) -> None:
        """SysEx 0x11 PATTERN_CLEAR."""
        self._sysex([CMD_PATTERN_CLEAR, slot & 0x7F])

    def channel_mute(self, channel: int, mute: bool) -> None:
        """SysEx 0x06 CHANNEL_MUTE."""
        self._sysex([CMD_CHANNEL_MUTE, channel & 0x7F, 1 if mute else 0])

    def channel_solo(self, channel: int, solo: bool) -> None:
        """SysEx 0x07 CHANNEL_SOLO."""
        self._sysex([CMD_CHANNEL_SOLO, channel & 0x7F, 1 if solo else 0])

    def send_note_event(self, channel: int, note: int, velocity: int,
                        duration_ticks: int) -> None:
        """SysEx 0x10 NOTE_EVENT — insert a note into the current pattern."""
        dur_msb7 = (duration_ticks >> 7) & 0x7F
        dur_lsb7 = duration_ticks & 0x7F
        self._sysex([CMD_NOTE_EVENT,
                     channel & 0x7F, note & 0x7F, velocity & 0x7F,
                     dur_msb7, dur_lsb7])

    def request_status(self) -> None:
        """SysEx 0x13 REQUEST_STATUS."""
        self._sysex([CMD_REQUEST_STATUS])
