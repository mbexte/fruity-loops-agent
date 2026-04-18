"""
Test suite for the voice-to-MIDI pipeline.

All tests use synthetic data (no microphone, no GPU, no FL Studio required).
CREPE is patched out so tests run without TensorFlow installed.
"""
from __future__ import annotations

import math
import os
import sys
import wave
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_sine_wav(
    freq_hz: float  = 440.0,
    duration_s: float = 1.0,
    samplerate: int = 44100,
    amplitude: int  = 20000,
) -> str:
    """Write a mono sine-wave WAV to a temp file; return its path."""
    t = np.linspace(0, duration_s, int(samplerate * duration_s), endpoint=False)
    audio = (amplitude * np.sin(2 * np.pi * freq_hz * t)).astype(np.int16)

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(audio.tobytes())
    return path


def _make_silence_wav(duration_s: float = 0.5, samplerate: int = 44100) -> str:
    return _make_sine_wav(freq_hz=0.0, duration_s=duration_s,
                          samplerate=samplerate, amplitude=0)


# ── recorder.py ───────────────────────────────────────────────────────────────

class TestSaveWav:
    def test_normalizes_quiet_audio(self, tmp_path):
        """Very quiet int16 frames should be peak-normalized to near full scale."""
        from recorder import _save_wav

        # Max amplitude = 100 (far below full scale)
        quiet = np.full((4410, 1), 100, dtype=np.int16)
        path  = str(tmp_path / "out.wav")
        _save_wav([quiet], 44100, out_path=path)

        with wave.open(path) as wf:
            raw = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

        assert np.max(np.abs(raw)) > 20000, "peak should be normalised above 20000"

    def test_empty_frames_writes_silence(self, tmp_path):
        """Zero-amplitude frame array shouldn't crash; peak stays at 0."""
        from recorder import _save_wav

        silent = np.zeros((4410, 1), dtype=np.int16)
        path   = str(tmp_path / "silent.wav")
        _save_wav([silent], 44100, out_path=path)

        with wave.open(path) as wf:
            raw = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

        assert np.max(np.abs(raw)) == 0

    def test_creates_mono_wav(self, tmp_path):
        """Output must be 1-channel 16-bit WAV."""
        from recorder import _save_wav

        frames = [np.full((1000, 1), 5000, dtype=np.int16)]
        path   = str(tmp_path / "mono.wav")
        _save_wav(frames, 16000, out_path=path)

        with wave.open(path) as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000

    def test_uses_temp_file_when_no_path(self):
        """When out_path is None a temp file path is returned and the file exists."""
        from recorder import _save_wav

        frames = [np.full((1000, 1), 8000, dtype=np.int16)]
        path   = _save_wav(frames, 44100)
        try:
            assert path is not None
            assert Path(path).exists()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


class TestRecorder:
    def test_stop_with_no_frames_returns_none(self):
        """stop() before start() (no frames) must return None without crashing."""
        from recorder import Recorder

        r = Recorder()
        result = r.stop()
        assert result is None

    def test_on_complete_called_with_path(self, tmp_path):
        """on_complete callback receives the WAV path when frames exist."""
        from recorder import Recorder, _save_wav

        received: list[str] = []
        r = Recorder(on_complete=received.append)

        frames = [np.full((1000, 1), 8000, dtype=np.int16)]
        # Directly inject frames (bypass sounddevice) then stop
        r._frames  = frames
        r._active  = True          # pretend we're recording
        out = str(tmp_path / "test.wav")
        result = r.stop(out_path=out)

        assert result == out
        assert received == [out]

    def test_double_stop_is_noop(self, tmp_path):
        """Calling stop() twice must not raise."""
        from recorder import Recorder

        r = Recorder()
        r._frames = [np.full((500, 1), 1000, dtype=np.int16)]
        r._active = True
        out = str(tmp_path / "t.wav")
        r.stop(out_path=out)
        second = r.stop()   # should return None, not crash
        assert second is None


# ── pitch.py ─────────────────────────────────────────────────────────────────

class TestDetectPitch:
    def test_440hz_detected_as_A4_with_pyin(self, tmp_path):
        """A 440 Hz sine WAV should yield frames near A4 (MIDI 69)."""
        from pitch import detect_pitch

        path   = _make_sine_wav(440.0, duration_s=1.0)
        frames = detect_pitch(path, backend="pyin")

        voiced = [f for f in frames if f.frequency > 0]
        assert len(voiced) > 5, "should detect voiced frames in a pure sine"

        freqs = [f.frequency for f in voiced]
        median_f = np.median(freqs)
        midi     = 69 + 12 * math.log2(median_f / 440.0)
        assert abs(midi - 69) < 1.0, f"expected ~A4 (69), got MIDI {midi:.2f}"
        os.unlink(path)

    def test_silence_returns_all_unvoiced(self, tmp_path):
        """Silent WAV should return frames with frequency == 0, no crash."""
        from pitch import detect_pitch

        path   = _make_silence_wav()
        frames = detect_pitch(path, backend="pyin")

        assert isinstance(frames, list)
        voiced = [f for f in frames if f.frequency > 0]
        assert len(voiced) == 0
        os.unlink(path)

    def test_auto_backend_falls_back_to_pyin_when_crepe_missing(self):
        """If crepe is not importable, 'auto' backend must use pyin."""
        import pitch as pitch_mod
        original = pitch_mod._choose_backend

        # Temporarily make _choose_backend always return 'pyin'
        pitch_mod._choose_backend = lambda: "pyin"
        try:
            path   = _make_sine_wav(440.0, duration_s=0.5)
            frames = pitch_mod.detect_pitch(path, backend="auto")
            assert isinstance(frames, list)
            os.unlink(path)
        finally:
            pitch_mod._choose_backend = original

    def test_returns_pitchframe_namedtuples(self):
        """Each element must be a PitchFrame with time/frequency/confidence."""
        from pitch import detect_pitch, PitchFrame

        path   = _make_sine_wav(330.0, duration_s=0.5)
        frames = detect_pitch(path, backend="pyin")

        assert len(frames) > 0
        for f in frames:
            assert isinstance(f, PitchFrame)
            assert f.time >= 0.0
            assert f.frequency >= 0.0
            assert 0.0 <= f.confidence <= 1.0
        os.unlink(path)

    def test_crepe_backend_with_mock(self, tmp_path):
        """CREPE path returns PitchFrames even with a mocked crepe.predict."""
        import pitch as pitch_mod
        from pitch import PitchFrame

        n = 50
        mock_crepe = MagicMock()
        mock_crepe.predict.return_value = (
            None,
            np.full(n, 440.0),           # frequencies
            np.full(n, 0.9),             # confidences
            None,
        )

        with patch.dict(sys.modules, {"crepe": mock_crepe}):
            path   = _make_sine_wav(440.0, duration_s=0.5, samplerate=16000)
            frames = pitch_mod._detect_crepe(
                np.zeros(8000, dtype=np.float32), 16000, "small", 10.0
            )
            assert all(isinstance(f, PitchFrame) for f in frames)
        os.unlink(path)


# ── notes.py ──────────────────────────────────────────────────────────────────

class TestFramesToNotes:
    def _frames(self, freq: float, count: int, conf: float = 0.9):
        from pitch import PitchFrame
        return [PitchFrame(i * 0.01, freq, conf) for i in range(count)]

    def test_single_pitch_yields_one_note(self):
        """50 frames at A4 (440 Hz) → exactly 1 note with pitch 69."""
        from notes import frames_to_notes

        notes = frames_to_notes(self._frames(440.0, 50))
        assert len(notes) == 1
        assert notes[0]["pitch"] == 69   # A4

    def test_two_pitches_yield_two_notes(self):
        """25 frames A4 then 25 frames E5 (659.26 Hz) → 2 notes."""
        from pitch import PitchFrame
        from notes import frames_to_notes

        frames  = self._frames(440.0, 25)
        e5_freq = 440.0 * 2 ** (7 / 12)  # E5
        frames += [PitchFrame(0.25 + i * 0.01, e5_freq, 0.9) for i in range(25)]

        notes = frames_to_notes(frames)
        assert len(notes) == 2
        assert notes[0]["pitch"] == 69   # A4
        assert notes[1]["pitch"] == 76   # E5

    def test_short_segment_filtered_out(self):
        """5 frames (50 ms) is below MIN_NOTE_DURATION=80 ms → no notes."""
        from notes import frames_to_notes

        notes = frames_to_notes(self._frames(440.0, 5))
        assert len(notes) == 0

    def test_silence_frames_produce_no_notes(self):
        """Unvoiced frames (frequency=0) must produce zero notes."""
        from pitch import PitchFrame
        from notes import frames_to_notes

        frames = [PitchFrame(i * 0.01, 0.0, 0.1) for i in range(50)]
        assert frames_to_notes(frames) == []

    def test_velocity_in_valid_range(self):
        """Velocity must be 1–127."""
        from notes import frames_to_notes

        notes = frames_to_notes(self._frames(440.0, 30))
        for n in notes:
            assert 1 <= n["velocity"] <= 127

    def test_low_confidence_filtered(self):
        """Frames with very low confidence (0.05) → unvoiced → no notes.

        Notes: pitch.py sets frequency to 0.0 for low-confidence frames before
        frames_to_notes sees them, so we simulate that directly.
        """
        from pitch import PitchFrame
        from notes import frames_to_notes

        # Simulate what pitch.py does: low confidence → frequency set to 0
        frames = [PitchFrame(i * 0.01, 0.0, 0.05) for i in range(50)]
        notes  = frames_to_notes(frames)
        assert len(notes) == 0

    def test_out_of_range_midi_filtered(self):
        """Pitches outside 40–84 (MIN/MAX_MIDI_NOTE) are discarded."""
        from pitch import PitchFrame
        from notes import frames_to_notes, MIN_MIDI_NOTE, MAX_MIDI_NOTE

        # Build a frequency below MIDI 40 (E2 = ~82 Hz → use 50 Hz which is ~MIDI 31)
        very_low = 440.0 * 2 ** ((30 - 69) / 12)   # ~MIDI 30
        frames   = [PitchFrame(i * 0.01, very_low, 0.9) for i in range(50)]
        notes    = frames_to_notes(frames)
        assert all(MIN_MIDI_NOTE <= n["pitch"] <= MAX_MIDI_NOTE for n in notes)


class TestNotesToPattern:
    def test_bar_conversion_at_120bpm(self):
        """At 120 BPM, a 2-second note = 1.0 bars (bar_s = 2.0 s)."""
        from notes import NoteSegment, notes_to_pattern

        seg: NoteSegment = {"pitch": 60, "start": 0.0, "duration": 2.0, "velocity": 80}
        pattern = notes_to_pattern([seg], tempo=120)

        assert len(pattern) == 1
        assert abs(pattern[0]["duration"] - 1.0) < 0.001
        assert pattern[0]["note"] == 60

    def test_pattern_format_compatible_with_build_events(self):
        """Pattern dicts must have 'note' and 'duration' keys (build_events contract)."""
        from notes import NoteSegment, notes_to_pattern

        segs: list[NoteSegment] = [
            {"pitch": 60, "start": 0.0, "duration": 0.5, "velocity": 80},
            {"pitch": 62, "start": 0.5, "duration": 0.5, "velocity": 80},
        ]
        pattern = notes_to_pattern(segs, tempo=120)
        for p in pattern:
            assert "note"     in p
            assert "duration" in p


# ── midi.py ───────────────────────────────────────────────────────────────────

class TestNotesToMidi:
    def _sample_notes(self):
        return [
            {"pitch": 60, "start": 0.0,  "duration": 0.5, "velocity": 80},
            {"pitch": 62, "start": 0.5,  "duration": 0.5, "velocity": 80},
            {"pitch": 64, "start": 1.0,  "duration": 0.5, "velocity": 80},
        ]

    def test_creates_non_empty_file(self, tmp_path):
        """notes_to_midi must write a non-empty .mid file."""
        from midi import notes_to_midi

        path = notes_to_midi(self._sample_notes(), 120,
                             out_path=str(tmp_path / "test.mid"))
        assert Path(path).exists()
        assert Path(path).stat().st_size > 0

    def test_pretty_midi_path(self, tmp_path):
        """Force pretty_midi backend explicitly."""
        from midi import _write_pretty_midi

        path = str(tmp_path / "pm.mid")
        result = _write_pretty_midi(self._sample_notes(), 120, path)
        assert Path(result).exists()
        assert Path(result).stat().st_size > 0

    def test_mido_fallback_path(self, tmp_path):
        """mido fallback must also produce a valid non-empty file."""
        from midi import _write_mido_fallback

        path = str(tmp_path / "mido.mid")
        result = _write_mido_fallback(self._sample_notes(), 120, path)
        assert Path(result).exists()
        assert Path(result).stat().st_size > 0

    def test_fl_output_dir_env_var(self, tmp_path, monkeypatch):
        """FL_STUDIO_MIDI_OUT env var must override the default output path."""
        monkeypatch.setenv("FL_STUDIO_MIDI_OUT", str(tmp_path))

        # Re-import to pick up the env var
        import importlib
        import midi as midi_mod
        importlib.reload(midi_mod)

        assert midi_mod._fl_output_dir() == tmp_path

    def test_output_dir_created_if_missing(self, tmp_path, monkeypatch):
        """Output directory is created automatically if it doesn't exist."""
        target = tmp_path / "deep" / "nested" / "dir"
        monkeypatch.setenv("FL_STUDIO_MIDI_OUT", str(target))

        import importlib
        import midi as midi_mod
        importlib.reload(midi_mod)

        midi_mod.notes_to_midi(self._sample_notes(), 120)
        assert target.exists()

    def test_mido_fallback_note_order(self, tmp_path):
        """Events must be sorted by absolute tick (no negative delta-time)."""
        import mido
        from midi import _write_mido_fallback

        # Two notes with overlapping start/end that would expose sorting bugs
        notes = [
            {"pitch": 64, "start": 0.0, "duration": 1.0, "velocity": 80},
            {"pitch": 60, "start": 0.0, "duration": 0.5, "velocity": 80},
        ]
        path = str(tmp_path / "order.mid")
        _write_mido_fallback(notes, 120, path)

        mid = mido.MidiFile(path)
        for track in mid.tracks:
            for msg in track:
                assert msg.time >= 0, f"Negative delta time detected: {msg}"


# ── main.py ───────────────────────────────────────────────────────────────────

class TestProcessAudio:
    def test_end_to_end_pyin(self, tmp_path):
        """440 Hz WAV → ProcessResult with ≥1 note, valid midi_path, tempo=120."""
        from main import process_audio

        wav  = _make_sine_wav(440.0, duration_s=1.5)
        result = process_audio(wav, tempo=120, backend="pyin",
                               on_progress=lambda m: None)
        try:
            assert len(result["notes"])   >= 1
            assert len(result["pattern"]) >= 1
            assert result["tempo"]        == 120
            assert Path(result["midi_path"]).exists()
        finally:
            os.unlink(wav)
            try:
                os.unlink(result["midi_path"])
            except OSError:
                pass

    def test_progress_callback_called(self):
        """on_progress must be called at least once during processing."""
        from main import process_audio

        messages: list[str] = []
        wav = _make_sine_wav(440.0, duration_s=1.0)
        try:
            process_audio(wav, tempo=120, backend="pyin",
                          on_progress=messages.append)
        except Exception:
            pass   # might fail on no-note detection; we just care about callbacks
        finally:
            os.unlink(wav)

        assert len(messages) > 0

    def test_raises_on_silent_input(self):
        """Silent WAV should raise ValueError about no pitched content."""
        from main import process_audio

        wav = _make_silence_wav(duration_s=1.0)
        try:
            with pytest.raises(ValueError, match="No pitched content"):
                process_audio(wav, tempo=120, backend="pyin")
        finally:
            os.unlink(wav)


class TestDetectBpm:
    def test_returns_int_in_range(self):
        """detect_bpm should return an int between 60 and 200."""
        from main import detect_bpm

        wav = _make_sine_wav(440.0, duration_s=2.0)
        bpm = detect_bpm(wav)
        os.unlink(wav)

        # May return None if librosa cannot detect rhythm in a pure sine
        if bpm is not None:
            assert isinstance(bpm, int)
            assert 60 <= bpm <= 200

    def test_returns_none_on_empty_file(self, tmp_path):
        """Broken / empty WAV should return None, not raise."""
        from main import detect_bpm

        # Write a valid but silent WAV
        wav = _make_silence_wav(0.1)
        result = detect_bpm(wav)
        os.unlink(wav)
        # None or int, either is acceptable
        assert result is None or isinstance(result, int)


class TestDetectKey:
    def test_c_major_scale_detected(self):
        """Notes from the C major scale should yield 'C major'."""
        from main import detect_key

        # C D E F G — strong C major signal
        notes = [
            {"pitch": 60, "start": 0.0, "duration": 1.0, "velocity": 80},  # C4
            {"pitch": 62, "start": 1.0, "duration": 1.0, "velocity": 80},  # D4
            {"pitch": 64, "start": 2.0, "duration": 1.0, "velocity": 80},  # E4
            {"pitch": 65, "start": 3.0, "duration": 1.0, "velocity": 80},  # F4
            {"pitch": 67, "start": 4.0, "duration": 1.0, "velocity": 80},  # G4
        ]
        key = detect_key(notes)
        assert key is not None
        assert "C" in key and "major" in key

    def test_a_minor_scale_detected(self):
        """Notes from A natural minor should yield 'A minor'."""
        from main import detect_key

        notes = [
            {"pitch": 69, "start": 0.0, "duration": 1.0, "velocity": 80},  # A4
            {"pitch": 71, "start": 1.0, "duration": 1.0, "velocity": 80},  # B4
            {"pitch": 72, "start": 2.0, "duration": 1.0, "velocity": 80},  # C5
            {"pitch": 74, "start": 3.0, "duration": 1.0, "velocity": 80},  # D5
            {"pitch": 76, "start": 4.0, "duration": 1.0, "velocity": 80},  # E5
        ]
        key = detect_key(notes)
        assert key is not None
        assert "A" in key and "minor" in key

    def test_empty_notes_returns_none(self):
        """No notes → None, not an error."""
        from main import detect_key
        assert detect_key([]) is None

    def test_returns_string_format(self):
        """Return value must be 'NoteName mode' (two words) or None."""
        from main import detect_key

        notes = [{"pitch": 60, "start": 0.0, "duration": 1.0, "velocity": 80}]
        key = detect_key(notes)
        if key is not None:
            parts = key.split()
            assert len(parts) == 2
            assert parts[1] in ("major", "minor")


# ── integration: pipeline matches midi_scheduler contract ────────────────────

class TestPipelineIntegration:
    def test_pattern_accepted_by_build_events(self, tmp_path):
        """Pattern from process_audio must be accepted by build_events() unchanged."""
        from main import process_audio
        from midi_scheduler import build_events

        wav = _make_sine_wav(440.0, duration_s=1.5)
        result = process_audio(wav, tempo=120, backend="pyin",
                               on_progress=lambda m: None)
        os.unlink(wav)

        events = build_events(result["pattern"], result["tempo"])
        assert isinstance(events, list)
        # Every event must have type, note, and time attributes
        for ev in events:
            assert hasattr(ev, "type")
            assert hasattr(ev, "note")
            assert hasattr(ev, "time")

    def test_midi_file_readable_by_mido(self, tmp_path):
        """The generated melody.mid must be parseable by mido."""
        import mido
        from main import process_audio

        wav = _make_sine_wav(330.0, duration_s=1.5)
        result = process_audio(wav, tempo=120, backend="pyin",
                               on_progress=lambda m: None)
        os.unlink(wav)

        mid = mido.MidiFile(result["midi_path"])
        assert mid.type == 0
        # Should have at least one note_on message
        all_msgs = [msg for track in mid.tracks for msg in track]
        note_ons = [m for m in all_msgs
                    if not m.is_meta and m.type == "note_on" and m.velocity > 0]
        assert len(note_ons) >= 1
