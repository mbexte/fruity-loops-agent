"""
fl_transport.py — MIDI communication with FL Studio via loopMIDI.

  Note 72 (C5) → Start recording
  Note 74 (D5) → Stop recording
  play_melody(intent) → arms recording, plays all notes, stops recording

Requirements:
  loopMIDI running with a port named "FL Agent".
  FL Studio MIDI Settings: "FL Agent" enabled, Controller type = FL Agent Controller.

Usage:
  python fl_transport.py start
  python fl_transport.py stop
"""

import sys
import time
import mido

PORT_NAME  = "FL Agent"
NOTE_START = 72    # C5 → start recording
NOTE_STOP  = 74    # D5 → stop recording
CHANNEL    = 0
VELOCITY   = 100
RESERVED_NOTES = {NOTE_START, NOTE_STOP}


def sanitize_midi_note_number(note: int) -> int:
    while note in RESERVED_NOTES:
        note += 1
    return note


def _get_port() -> str:
    match = next(
        (p for p in mido.get_output_names() if PORT_NAME.lower() in p.lower()),
        None,
    )
    if match is None:
        print(f"ERROR: MIDI port '{PORT_NAME}' not found.")
        print("Available:", mido.get_output_names() or ["(none)"])
        sys.exit(1)
    return match


def _note(port, note: int, velocity: int = VELOCITY) -> None:
    port.send(mido.Message("note_on",  note=note, velocity=velocity, channel=CHANNEL))
    port.send(mido.Message("note_off", note=note, velocity=0,        channel=CHANNEL))


def start_recording() -> None:
    with mido.open_output(_get_port()) as port:
        _note(port, NOTE_START)
    print("FL Studio: recording started")


def stop_recording() -> None:
    with mido.open_output(_get_port()) as port:
        _note(port, NOTE_STOP)
    print("FL Studio: recording stopped")


def play_melody(intent: dict) -> None:
    """
    Arms FL Studio recording, plays every note in intent['melody_pattern'],
    then stops recording. Blocks until the full melody has played.

    intent keys used:
      tempo          (int)       — BPM
      melody_pattern (list[dict]) — dicts with note (MIDI number) and duration (bars)
    """
    tempo        = intent.get("tempo", 120)
    notes        = intent.get("melody_pattern", [])
    bar_dur      = 60.0 / tempo * 4    # seconds in one 4/4 bar
    gap          = 0.02               # silence between consecutive notes

    with mido.open_output(_get_port()) as port:
        # 1 — start recording
        _note(port, NOTE_START)
        print("FL Studio: recording started")

        # 2 — play notes
        for note_obj in notes:
            if isinstance(note_obj, dict):
                note = note_obj.get('note')
                duration_bars = note_obj.get('duration', 0.25)
            else:
                note = note_obj
                duration_bars = 0.25
            note_duration = bar_dur * duration_bars
            if isinstance(note, list):
                midi_notes = [sanitize_midi_note_number(n) for n in note]
                for midi_note in midi_notes:
                    port.send(mido.Message("note_on", note=midi_note, velocity=VELOCITY, channel=CHANNEL))
                time.sleep(note_duration)
                for midi_note in midi_notes:
                    port.send(mido.Message("note_off", note=midi_note, velocity=0, channel=CHANNEL))
                time.sleep(gap)
                print(f"  played chord {midi_notes} for {note_duration:.2f}s")
            else:
                note = sanitize_midi_note_number(note)
                port.send(mido.Message("note_on",  note=note, velocity=VELOCITY, channel=CHANNEL))
                time.sleep(note_duration)
                port.send(mido.Message("note_off", note=note, velocity=0,        channel=CHANNEL))
                time.sleep(gap)
                print(f"  played note {note} for {note_duration:.2f}s")

        time.sleep(0.1)

        # 3 — stop recording
        _note(port, NOTE_STOP)
        print("FL Studio: recording stopped")


if __name__ == "__main__":
    commands = {"start": start_recording, "stop": stop_recording}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print("Usage: python fl_transport.py <start|stop>")
        sys.exit(1)
    commands[sys.argv[1]]()
