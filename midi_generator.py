import mido
from mido import Message, MidiFile, MidiTrack
import json
from typing import List, Dict

def generate_midi_from_intent(intent: Dict, output_path: str = "output.mid"):
    mid = MidiFile()
    track = MidiTrack()
    mid.tracks.append(track)

    tempo = intent.get("tempo", 120)
    # Convert BPM to microseconds per beat
    microseconds_per_beat = mido.bpm2tempo(tempo)
    track.append(mido.MetaMessage('set_tempo', tempo=microseconds_per_beat))

    melody = intent.get("melody_pattern", [])
    velocity = 100
    duration = 480  # ticks (default 0.5s at 120bpm)
    time = 0
    for note in melody:
        track.append(Message('note_on', note=note, velocity=velocity, time=time))
        track.append(Message('note_off', note=note, velocity=velocity, time=duration))
        time = 0  # Only the first note_on gets the accumulated time

    mid.save(output_path)
    return output_path

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python midi_generator.py <intent_json_file> [output.mid]")
        sys.exit(1)
    with open(sys.argv[1], "r") as f:
        intent = json.load(f)
    output = sys.argv[2] if len(sys.argv) > 2 else "output.mid"
    generate_midi_from_intent(intent, output)
    print(f"MIDI file saved to {output}")
