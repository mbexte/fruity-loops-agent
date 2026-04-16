"""
tests/test_fl_protocol.py — FL MIDI Protocol Test Suite
========================================================

Tests cover all three protocol tiers and several integration scenarios.
All tests run fully in-process using MockFLStudio + MockFLTransport —
no real MIDI ports, no loopMIDI, no FL Studio installation required.

Test classes
────────────
  TestTier1Transport  — Note-based transport (72–77)
  TestTier2CC         — CC-based parameter control
  TestTier3SysEx      — SysEx structured commands + responses
  TestSysExResponses  — Response format validation
  TestProtocolEdges   — Boundary values and error conditions
  TestIntegration     — End-to-end session workflows

Run:
  pip install pytest
  pytest tests/test_fl_protocol.py -v
"""

import time
import pytest

from tests.mock_fl_studio import (
    MockFLStudio,
    RSP_ACK, RSP_PONG, RSP_STATUS,
    CMD_PING, CMD_SET_TEMPO, CMD_PATTERN_SELECT,
    CMD_SET_PATTERN_NAME, CMD_CHANNEL_MUTE, CMD_CHANNEL_SOLO,
    CMD_QUANTIZE, CMD_PATTERN_CLEAR, CMD_NOTE_EVENT,
)
from tests.mock_fl_transport import MockFLTransport


# ── Helpers ───────────────────────────────────────────────────────────────────

def flush(fl: MockFLStudio, ms: float = 60) -> None:
    """Give the MockFLStudio background thread time to process queued messages."""
    fl.flush(ms)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fl() -> MockFLStudio:
    """Provide a started MockFLStudio, stop it after the test."""
    with MockFLStudio() as studio:
        yield studio


@pytest.fixture
def t(fl: MockFLStudio) -> MockFLTransport:
    """Provide a MockFLTransport wired to the fl fixture."""
    return MockFLTransport(fl)


# ══════════════════════════════════════════════════════════════════════════════
# TIER 1 — Note-based transport control
# ══════════════════════════════════════════════════════════════════════════════

class TestTier1Transport:
    """Tests for Note-On messages 72–77 (FL MIDI Protocol Tier 1)."""

    def test_start_recording_sets_playing_and_recording(self, fl, t):
        t.start_recording()
        flush(fl)
        assert fl.state.is_playing   is True
        assert fl.state.is_recording is True

    def test_stop_recording_clears_playing_and_recording(self, fl, t):
        t.start_recording()
        flush(fl)
        t.stop_recording()
        flush(fl)
        assert fl.state.is_playing   is False
        assert fl.state.is_recording is False

    def test_pause_while_playing_stops_transport(self, fl, t):
        t.start_recording()
        flush(fl)
        assert fl.state.is_playing is True
        t.pause_resume()
        flush(fl)
        assert fl.state.is_playing is False

    def test_resume_while_stopped_starts_transport(self, fl, t):
        # Initial state is stopped
        assert fl.state.is_playing is False
        t.pause_resume()
        flush(fl)
        assert fl.state.is_playing is True

    def test_pause_resume_toggles_twice(self, fl, t):
        t.pause_resume()
        flush(fl)
        assert fl.state.is_playing is True
        t.pause_resume()
        flush(fl)
        assert fl.state.is_playing is False

    def test_rewind_increments_rewind_count(self, fl, t):
        assert fl.state.rewind_count == 0
        t.rewind()
        flush(fl)
        assert fl.state.rewind_count == 1
        t.rewind()
        flush(fl)
        assert fl.state.rewind_count == 2

    def test_rewind_does_not_change_playback_state(self, fl, t):
        t.start_recording()
        flush(fl)
        t.rewind()
        flush(fl)
        assert fl.state.is_playing is True

    def test_loop_toggle_off_to_on(self, fl, t):
        assert fl.state.loop_on is False
        t.toggle_loop()
        flush(fl)
        assert fl.state.loop_on is True

    def test_loop_toggle_on_to_off(self, fl, t):
        t.toggle_loop()
        flush(fl)
        t.toggle_loop()
        flush(fl)
        assert fl.state.loop_on is False

    def test_metronome_toggle_off_to_on(self, fl, t):
        assert fl.state.metronome_on is False
        t.toggle_metronome()
        flush(fl)
        assert fl.state.metronome_on is True

    def test_metronome_double_toggle(self, fl, t):
        t.toggle_metronome()
        flush(fl)
        t.toggle_metronome()
        flush(fl)
        assert fl.state.metronome_on is False

    def test_start_stop_idempotent_on_recording_flag(self, fl, t):
        """Recording flag is True after start and False after stop each time."""
        for _ in range(3):
            t.start_recording()
            flush(fl)
            assert fl.state.is_recording is True
            t.stop_recording()
            flush(fl)
            assert fl.state.is_recording is False


# ══════════════════════════════════════════════════════════════════════════════
# TIER 2 — CC-based parameter control
# ══════════════════════════════════════════════════════════════════════════════

class TestTier2CC:
    """Tests for Control Change messages (FL MIDI Protocol Tier 2)."""

    def test_set_tempo_120_bpm(self, fl, t):
        t.set_tempo_cc(120)
        flush(fl)
        assert fl.state.bpm == 120

    def test_set_tempo_minimum_60_bpm(self, fl, t):
        t.set_tempo_cc(60)
        flush(fl)
        assert fl.state.bpm == 60

    def test_set_tempo_maximum_187_bpm(self, fl, t):
        t.set_tempo_cc(187)
        flush(fl)
        assert fl.state.bpm == 187

    def test_set_tempo_cc_overflow_clamped(self, fl, t):
        """BPM above 187 is clamped to CC max (value=127 → 187 BPM)."""
        t.set_tempo_cc(300)
        flush(fl)
        assert fl.state.bpm == 187

    def test_select_pattern_slot(self, fl, t):
        t.select_pattern_cc(5)
        flush(fl)
        assert fl.state.current_pattern == 5

    def test_select_pattern_slot_zero(self, fl, t):
        t.select_pattern_cc(0)
        flush(fl)
        assert fl.state.current_pattern == 0

    def test_select_pattern_slot_max(self, fl, t):
        t.select_pattern_cc(127)
        flush(fl)
        assert fl.state.current_pattern == 127

    def test_loop_enable_via_cc(self, fl, t):
        t.set_loop_cc(True)
        flush(fl)
        assert fl.state.loop_on is True

    def test_loop_disable_via_cc(self, fl, t):
        t.set_loop_cc(True)
        flush(fl)
        t.set_loop_cc(False)
        flush(fl)
        assert fl.state.loop_on is False

    def test_master_volume_half(self, fl, t):
        t.set_master_volume(0.5)
        flush(fl)
        assert abs(fl.state.master_volume - 0.5) < 0.02   # ±1 CC step tolerance

    def test_master_volume_full(self, fl, t):
        t.set_master_volume(1.0)
        flush(fl)
        assert abs(fl.state.master_volume - 1.0) < 0.02

    def test_master_volume_zero(self, fl, t):
        t.set_master_volume(0.0)
        flush(fl)
        assert fl.state.master_volume < 0.02


# ══════════════════════════════════════════════════════════════════════════════
# TIER 3 — SysEx structured commands
# ══════════════════════════════════════════════════════════════════════════════

class TestTier3SysEx:
    """Tests for SysEx protocol commands (FL MIDI Protocol Tier 3)."""

    # ── PING / PONG ────────────────────────────────────────────────────────────

    def test_ping_receives_pong(self, fl, t):
        t.ping()
        flush(fl)
        response = fl.wait_for_response(timeout=0.5)
        assert response is not None, "No response received for PING"
        assert response[0] == RSP_PONG

    def test_pong_contains_uptime(self, fl, t):
        t.ping()
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None
        # Uptime is 14-bit in bytes [1] and [2]
        uptime = (response[1] << 7) | response[2]
        assert uptime >= 0

    # ── REQUEST_STATUS ─────────────────────────────────────────────────────────

    def test_request_status_receives_response_and_reflects_state(self, fl, t):
        """REQUEST_STATUS must reply with RSP_STATUS encoding the live state.

        This is the real-function analogue of test_ping_receives_pong: it
        exercises an actual FL function (transport + tempo state) end-to-end
        rather than just testing connectivity.
        """
        t.set_tempo(175)
        flush(fl)
        t.start_recording()
        flush(fl)
        t.toggle_loop()
        flush(fl)
        fl.clear_responses()

        t.request_status()
        flush(fl)
        response = fl.wait_for_response(timeout=0.5)

        assert response is not None, "No STATUS response received for REQUEST_STATUS"
        assert response[0] == RSP_STATUS

        state_byte = response[1]
        assert state_byte & 0x01, "bit 0 (is_playing) should be set"
        assert state_byte & 0x02, "bit 1 (is_recording) should be set"
        assert state_byte & 0x04, "bit 2 (loop_on) should be set"

        bpm = (response[3] << 7) | response[4]
        assert bpm == 175

    # ── SET_TEMPO ──────────────────────────────────────────────────────────────

    def test_set_tempo_sysex_140(self, fl, t):
        t.set_tempo(140)
        flush(fl)
        assert fl.state.bpm == 140

    def test_set_tempo_sysex_ack(self, fl, t):
        t.set_tempo(140)
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None
        assert response[0] == RSP_ACK
        assert response[1] == CMD_SET_TEMPO

    def test_set_tempo_14bit_300_bpm(self, fl, t):
        """Values > 127 require 14-bit encoding (two 7-bit bytes)."""
        t.set_tempo(300)
        flush(fl)
        assert fl.state.bpm == 300

    def test_set_tempo_maximum_999(self, fl, t):
        t.set_tempo(999)
        flush(fl)
        assert fl.state.bpm == 999

    def test_set_tempo_clamp_below_40(self, fl, t):
        t.set_tempo(10)
        flush(fl)
        assert fl.state.bpm == 40

    def test_set_tempo_clamp_above_999(self, fl, t):
        t.set_tempo(5000)
        flush(fl)
        assert fl.state.bpm == 999

    # ── PATTERN_SELECT ─────────────────────────────────────────────────────────

    def test_pattern_select_changes_slot(self, fl, t):
        t.select_pattern(7)
        flush(fl)
        assert fl.state.current_pattern == 7

    def test_pattern_select_ack(self, fl, t):
        t.select_pattern(3)
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None
        assert response[0] == RSP_ACK

    # ── SET_PATTERN_NAME ───────────────────────────────────────────────────────

    def test_set_pattern_name_stored(self, fl, t):
        t.set_pattern_name(0, "Lead")
        flush(fl)
        assert fl.state.pattern_names.get(0) == "Lead"

    def test_set_pattern_name_ack(self, fl, t):
        t.set_pattern_name(1, "Bass")
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None
        assert response[0] == RSP_ACK
        assert response[1] == CMD_SET_PATTERN_NAME

    def test_set_pattern_name_truncated_to_32(self, fl, t):
        long_name = "A" * 50
        t.set_pattern_name(2, long_name)
        flush(fl)
        stored = fl.state.pattern_names.get(2, "")
        assert len(stored) <= 32

    def test_set_pattern_name_multiple_slots(self, fl, t):
        names = {0: "Intro", 1: "Verse", 2: "Chorus", 3: "Bridge"}
        for slot, name in names.items():
            t.set_pattern_name(slot, name)
        flush(fl, ms=100)
        for slot, name in names.items():
            assert fl.state.pattern_names.get(slot) == name

    def test_set_pattern_name_ascii_only_transport(self, fl, t):
        """Pattern names are ASCII-only (MIDI SysEx data bytes are 7-bit, 0–127).
        Non-ASCII Python strings are silently truncated at the first non-ASCII char
        since ord() > 127 would violate the MIDI spec; the transport clips at 32
        printable ASCII chars.  Test that names stay within the ASCII printable range.
        """
        name = "Intro-A"     # all ASCII printable
        t.set_pattern_name(9, name)
        flush(fl)
        stored = fl.state.pattern_names.get(9, "")
        assert stored == name
        assert all(ord(c) < 128 for c in stored)

    # ── CHANNEL_MUTE ──────────────────────────────────────────────────────────

    def test_channel_mute_adds_to_set(self, fl, t):
        t.channel_mute(3, True)
        flush(fl)
        assert 3 in fl.state.muted_channels

    def test_channel_mute_ack(self, fl, t):
        t.channel_mute(2, True)
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None and response[0] == RSP_ACK

    def test_channel_unmute_removes_from_set(self, fl, t):
        t.channel_mute(3, True)
        flush(fl)
        t.channel_mute(3, False)
        flush(fl)
        assert 3 not in fl.state.muted_channels

    def test_channel_mute_multiple(self, fl, t):
        for ch in [0, 2, 5, 9]:
            t.channel_mute(ch, True)
        flush(fl)
        for ch in [0, 2, 5, 9]:
            assert ch in fl.state.muted_channels

    # ── CHANNEL_SOLO ──────────────────────────────────────────────────────────

    def test_channel_solo_adds_to_set(self, fl, t):
        t.channel_solo(1, True)
        flush(fl)
        assert 1 in fl.state.soloed_channels

    def test_channel_unsolo_removes_from_set(self, fl, t):
        t.channel_solo(1, True)
        flush(fl)
        t.channel_solo(1, False)
        flush(fl)
        assert 1 not in fl.state.soloed_channels

    # ── QUANTIZE ──────────────────────────────────────────────────────────────

    def test_quantize_1_16_recorded(self, fl, t):
        t.quantize(2)   # QUANTIZE_1_16
        flush(fl)
        assert 2 in fl.state.quantize_calls

    def test_quantize_all_grids(self, fl, t):
        for grid in range(4):
            t.quantize(grid)
        flush(fl, ms=100)
        assert fl.state.quantize_calls == [0, 1, 2, 3]

    def test_quantize_ack(self, fl, t):
        t.quantize(1)
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None and response[0] == RSP_ACK

    # ── PATTERN_CLEAR ─────────────────────────────────────────────────────────

    def test_pattern_clear_recorded(self, fl, t):
        t.pattern_clear(4)
        flush(fl)
        assert 4 in fl.state.cleared_patterns

    def test_pattern_clear_ack(self, fl, t):
        t.pattern_clear(0)
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None and response[0] == RSP_ACK

    # ── NOTE_EVENT ────────────────────────────────────────────────────────────

    def test_note_event_stored(self, fl, t):
        t.send_note_event(0, 60, 100, 480)
        flush(fl)
        assert len(fl.state.events_received) == 1
        ev = fl.state.events_received[0]
        assert ev["ch"]   == 0
        assert ev["note"] == 60
        assert ev["vel"]  == 100
        assert ev["dur"]  == 480

    def test_note_event_multiple(self, fl, t):
        notes = [(0, 60, 100, 480), (0, 64, 90, 240), (0, 67, 80, 960)]
        for ch, note, vel, dur in notes:
            t.send_note_event(ch, note, vel, dur)
        flush(fl, ms=100)
        assert len(fl.state.events_received) == 3

    def test_note_event_14bit_duration(self, fl, t):
        """Durations > 127 ticks require 14-bit (two 7-bit) encoding."""
        t.send_note_event(0, 60, 100, 8192)
        flush(fl)
        ev = fl.state.events_received[0]
        assert ev["dur"] == 8192

    def test_note_event_max_duration(self, fl, t):
        t.send_note_event(0, 60, 100, 16383)  # max 14-bit
        flush(fl)
        ev = fl.state.events_received[0]
        assert ev["dur"] == 16383

    def test_note_event_ack(self, fl, t):
        t.send_note_event(0, 60, 80, 480)
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None and response[0] == RSP_ACK


# ══════════════════════════════════════════════════════════════════════════════
# SysEx response format validation
# ══════════════════════════════════════════════════════════════════════════════

class TestSysExResponses:
    """Validate the exact byte layout of SysEx responses."""

    def test_status_response_format(self, fl, t):
        """STATUS (0x83): [state_byte, slot, bpm_msb7, bpm_lsb7]"""
        t.start_recording()
        flush(fl)
        t.set_tempo(128)
        flush(fl)
        fl.clear_responses()

        t.request_status()
        flush(fl)
        response = fl.wait_for_response(timeout=0.5)
        assert response is not None, "No STATUS response"
        assert response[0] == RSP_STATUS

        state_byte = response[1]
        assert state_byte & 0x01, "bit 0 (is_playing) should be set"
        assert state_byte & 0x02, "bit 1 (is_recording) should be set"

        bpm = (response[3] << 7) | response[4]
        assert bpm == 128

    def test_status_loop_bit(self, fl, t):
        t.toggle_loop()
        flush(fl)
        fl.clear_responses()
        t.request_status()
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None
        assert response[1] & 0x04, "bit 2 (loop_on) should be set"

    def test_status_metronome_bit(self, fl, t):
        t.toggle_metronome()
        flush(fl)
        fl.clear_responses()
        t.request_status()
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None
        assert response[1] & 0x08, "bit 3 (metronome_on) should be set"

    def test_status_pattern_slot(self, fl, t):
        t.select_pattern(10)
        flush(fl)
        fl.clear_responses()
        t.request_status()
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None
        assert response[2] == 10

    def test_pattern_info_response(self, fl, t):
        t.set_pattern_name(3, "Chorus")
        flush(fl)
        fl.clear_responses()
        t.pattern_info(3)
        flush(fl)
        response = fl.wait_for_response()
        assert response is not None
        assert response[0] == RSP_ACK
        slot = response[2]
        assert slot == 3
        name_len  = response[3]
        name_bytes = response[4:4 + name_len]
        assert bytes(name_bytes).decode("ascii") == "Chorus"

    def test_unknown_command_is_silently_ignored(self, fl):
        """Unknown SysEx commands must produce no response.

        Sending NACK for unknown commands creates an endless loop: the NACK
        loopbacks through the loopMIDI port as a new unknown command, which
        triggers another NACK, and so on.  The correct behaviour is a silent
        discard — no response at all.
        """
        import mido
        from tests.mock_fl_studio import SYSEX_MANF_ID
        fl.receive(mido.Message("sysex", data=tuple([SYSEX_MANF_ID, 0x7F])))
        flush(fl)
        assert fl.wait_for_response(timeout=0.1) is None


# ══════════════════════════════════════════════════════════════════════════════
# Edge cases and boundary values
# ══════════════════════════════════════════════════════════════════════════════

class TestProtocolEdges:
    """Boundary values, oversized inputs, and resilience tests."""

    def test_note_velocity_zero_ignored(self, fl, t):
        """Note-On with velocity 0 is a Note-Off — should NOT trigger transport."""
        import mido
        fl.receive(mido.Message("note_on", note=72, velocity=0))
        flush(fl)
        assert fl.state.is_playing   is False
        assert fl.state.is_recording is False

    def test_unknown_cc_ignored(self, fl):
        """CC numbers not in the protocol should not change any state."""
        import mido
        before = fl.state.bpm
        fl.receive(mido.Message("control_change", control=99, value=127))
        flush(fl)
        assert fl.state.bpm == before

    def test_sysex_wrong_manf_id_ignored(self, fl):
        """SysEx with wrong manufacturer ID must be silently discarded."""
        import mido
        fl.receive(mido.Message("sysex", data=(0x41, 0x01)))  # Roland ID
        flush(fl)
        assert fl.wait_for_response(timeout=0.1) is None

    def test_sysex_too_short_ignored(self, fl):
        """SysEx with only the manufacturer byte (no command) is discarded."""
        import mido
        from tests.mock_fl_studio import SYSEX_MANF_ID
        fl.receive(mido.Message("sysex", data=(SYSEX_MANF_ID,)))
        flush(fl)
        assert fl.wait_for_response(timeout=0.1) is None

    def test_set_tempo_40_bpm_minimum(self, fl, t):
        t.set_tempo(40)
        flush(fl)
        assert fl.state.bpm == 40

    def test_set_tempo_999_bpm_maximum(self, fl, t):
        t.set_tempo(999)
        flush(fl)
        assert fl.state.bpm == 999

    def test_pattern_slot_zero(self, fl, t):
        t.select_pattern(0)
        flush(fl)
        assert fl.state.current_pattern == 0

    def test_pattern_slot_127(self, fl, t):
        t.select_pattern(127)
        flush(fl)
        assert fl.state.current_pattern == 127

    def test_empty_pattern_name(self, fl, t):
        t.set_pattern_name(0, "")
        flush(fl)
        assert fl.state.pattern_names.get(0) == ""

    def test_32_char_pattern_name_not_truncated(self, fl, t):
        name = "A" * 32
        t.set_pattern_name(0, name)
        flush(fl)
        assert fl.state.pattern_names.get(0) == name

    def test_33_char_pattern_name_truncated(self, fl, t):
        name = "B" * 33
        t.set_pattern_name(0, name)
        flush(fl)
        assert len(fl.state.pattern_names.get(0, "")) == 32

    def test_note_event_midi_max_note(self, fl, t):
        t.send_note_event(0, 127, 127, 480)
        flush(fl)
        ev = fl.state.events_received[0]
        assert ev["note"] == 127
        assert ev["vel"]  == 127

    def test_channel_mute_idempotent(self, fl, t):
        """Muting an already-muted channel should not cause errors."""
        t.channel_mute(3, True)
        flush(fl)
        t.channel_mute(3, True)
        flush(fl)
        assert 3 in fl.state.muted_channels

    def test_channel_unmute_when_not_muted(self, fl, t):
        """Unmuting a non-muted channel should be safe."""
        t.channel_mute(5, False)
        flush(fl)
        assert 5 not in fl.state.muted_channels

    def test_rapid_tempo_changes(self, fl, t):
        """Last tempo change wins when commands arrive in rapid succession."""
        for bpm in range(100, 180, 10):
            t.set_tempo(bpm)
        flush(fl, ms=200)
        assert fl.state.bpm == 170

    def test_multiple_pings(self, fl, t):
        for _ in range(3):
            t.ping()
        flush(fl, ms=100)
        responses = fl.get_responses()
        pongs = [r for r in responses if r[0] == RSP_PONG]
        assert len(pongs) == 3


# ══════════════════════════════════════════════════════════════════════════════
# Integration — end-to-end session workflows
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """Full multi-step workflows combining all three protocol tiers."""

    def test_complete_recording_session(self, fl, t):
        """
        Simulates a realistic single-pattern recording session:
        1. Set tempo and project settings
        2. Configure and name a pattern
        3. Arm and record notes
        4. Quantize
        5. Stop and verify final state
        """
        # 1. Project setup (Tier 2 + Tier 3)
        t.set_tempo_cc(128)           # Tier-2: rough tempo via CC
        t.set_tempo(128)              # Tier-3: precise tempo via SysEx
        flush(fl)

        # 2. Pattern configuration
        t.select_pattern(0)
        t.set_pattern_name(0, "Main Theme")
        flush(fl)

        # 3. Arm recording (Tier 1) + insert notes (Tier 3)
        t.start_recording()
        flush(fl)
        assert fl.state.is_playing   is True
        assert fl.state.is_recording is True

        scale = [60, 62, 64, 65, 67, 69, 71, 72]   # C major scale
        for note in scale:
            t.send_note_event(0, note, 100, 480)
        flush(fl, ms=100)

        # 4. Quantize to 1/16
        t.quantize(2)
        flush(fl)

        # 5. Stop recording
        t.stop_recording()
        flush(fl)

        # Assertions
        assert fl.state.bpm                       == 128
        assert fl.state.is_playing                is False
        assert fl.state.is_recording              is False
        assert fl.state.pattern_names[0]          == "Main Theme"
        assert len(fl.state.events_received)      == 8
        assert [e["note"] for e in fl.state.events_received] == scale
        assert 2 in fl.state.quantize_calls

    def test_multi_pattern_arrangement(self, fl, t):
        """Record three separate patterns (melody, chords, bass)."""
        arrangement = [
            (0, "Melody", [(60, 100), (62, 90), (64, 85), (65, 80)]),
            (1, "Chords", [(48, 75),  (52, 70), (55, 70)]),
            (2, "Bass",   [(36, 90),  (36, 80)]),
        ]

        for slot, name, notes in arrangement:
            t.select_pattern(slot)
            t.set_pattern_name(slot, name)
            flush(fl)
            t.start_recording()
            flush(fl)
            for note, vel in notes:
                t.send_note_event(0, note, vel, 480)
            flush(fl)
            t.stop_recording()
            flush(fl)

        # Verify all patterns were recorded correctly
        assert fl.state.pattern_names == {0: "Melody", 1: "Chords", 2: "Bass"}
        assert len(fl.state.events_received) == 4 + 3 + 2   # = 9 notes total
        assert fl.state.current_pattern == 2
        assert fl.state.is_playing is False

    def test_mixer_setup_workflow(self, fl, t):
        """Configure mixer: volume, mute, solo channels."""
        t.set_master_volume(0.8)
        t.channel_mute(4, True)
        t.channel_mute(5, True)
        t.channel_solo(0, True)
        flush(fl, ms=100)

        assert abs(fl.state.master_volume - 0.8) < 0.02
        assert 4 in fl.state.muted_channels
        assert 5 in fl.state.muted_channels
        assert 0 in fl.state.soloed_channels

        # Un-mute one channel
        t.channel_mute(4, False)
        flush(fl)
        assert 4 not in fl.state.muted_channels
        assert 5 in fl.state.muted_channels

    def test_status_reflects_full_session_state(self, fl, t):
        """STATUS response must match the live state after a session."""
        t.set_tempo(160)
        flush(fl)
        t.select_pattern(3)
        flush(fl)
        t.toggle_loop()
        flush(fl)
        t.start_recording()
        flush(fl)
        fl.clear_responses()

        t.request_status()
        flush(fl)
        response = fl.wait_for_response(timeout=0.5)

        assert response is not None
        assert response[0] == RSP_STATUS

        state_byte = response[1]
        assert state_byte & 0x01, "is_playing"
        assert state_byte & 0x02, "is_recording"
        assert state_byte & 0x04, "loop_on"

        slot = response[2]
        assert slot == 3

        bpm = (response[3] << 7) | response[4]
        assert bpm == 160

    def test_start_stop_cycle_five_times(self, fl, t):
        """Transport must be consistent after many start/stop cycles."""
        for cycle in range(5):
            t.start_recording()
            flush(fl)
            assert fl.state.is_playing   is True,  f"Cycle {cycle}: expected playing"
            assert fl.state.is_recording is True,  f"Cycle {cycle}: expected recording"
            t.stop_recording()
            flush(fl)
            assert fl.state.is_playing   is False, f"Cycle {cycle}: expected stopped"
            assert fl.state.is_recording is False, f"Cycle {cycle}: expected not recording"

    def test_clear_then_re_record(self, fl, t):
        """Clear a pattern and then record fresh notes into the same slot."""
        # First recording
        t.select_pattern(0)
        flush(fl)
        t.start_recording()
        flush(fl)
        t.send_note_event(0, 60, 100, 480)
        flush(fl)
        t.stop_recording()
        flush(fl)
        assert len(fl.state.events_received) == 1

        # Clear and re-record
        t.pattern_clear(0)
        flush(fl)
        t.start_recording()
        flush(fl)
        t.send_note_event(0, 64, 90, 480)
        t.send_note_event(0, 67, 80, 480)
        flush(fl)
        t.stop_recording()
        flush(fl)

        assert 0 in fl.state.cleared_patterns
        # Both recordings accumulated in events_received (history list)
        assert len(fl.state.events_received) == 3

    def test_all_three_tiers_in_sequence(self, fl, t):
        """Exercise all three tiers in a single coherent workflow."""
        # Tier-2: rough setup
        t.set_tempo_cc(120)
        t.select_pattern_cc(1)
        t.set_loop_cc(True)
        flush(fl)

        # Tier-3: precise setup
        t.set_tempo(120)
        t.select_pattern(1)
        t.set_pattern_name(1, "Loop A")
        flush(fl)

        # Tier-1: transport
        t.start_recording()
        flush(fl)

        # Tier-3: notes
        t.send_note_event(0, 60, 100, 480)
        t.send_note_event(0, 64, 90,  480)
        t.send_note_event(0, 67, 80,  480)
        flush(fl)

        t.stop_recording()
        flush(fl)

        assert fl.state.bpm                   == 120
        assert fl.state.current_pattern       == 1
        assert fl.state.pattern_names[1]      == "Loop A"
        assert fl.state.loop_on               is True
        assert len(fl.state.events_received)  == 3
        assert fl.state.is_playing            is False
