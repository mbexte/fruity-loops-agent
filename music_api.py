import os
import re
from openai import OpenAI
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Union

from fl_transport import play_melody

app = FastAPI()
_api_key = os.environ.get("OPENROUTER_API_KEY")
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=_api_key,
) if _api_key else None

MODEL = "openai/gpt-5.4-mini"

def note_to_midi(note_str: str) -> int:
    """Convert note string like 'C4' or 'Bb3' to MIDI number."""
    note_map = {
        'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3,
        'E': 4, 'F': 5, 'F#': 6, 'Gb': 6, 'G': 7, 'G#': 8, 'Ab': 8,
        'A': 9, 'A#': 10, 'Bb': 10, 'B': 11,
    }
    match = re.match(r'^([A-G](?:#|b)?)(-?\d+)$', note_str)
    if not match:
        raise ValueError(f"Invalid note string: {note_str}")
    note, octave_str = match.groups()
    octave = int(octave_str)
    return note_map[note] + (octave + 1) * 12

BAR_GRID = 0.0625
MIN_DURATION_BARS = BAR_GRID


def sanitize_midi_notes(notes):
    """Pass-through — no notes are reserved since control uses SysEx, not note messages."""
    return list(notes)


def normalize_duration_bars(duration: float) -> float:
    if duration <= 0:
        return MIN_DURATION_BARS
    rounded = round(float(duration) / BAR_GRID) * BAR_GRID
    if rounded < MIN_DURATION_BARS:
        return MIN_DURATION_BARS
    return round(rounded, 6)


def quantize_melody_pattern(pattern, grid_bars: float = BAR_GRID) -> list:
    """Snap each note duration to the nearest multiple of grid_bars.

    Durations are measured in bars.
    Common grid values:
      0.0625 -> 16th-note grid (1/16 bar)
      0.125  -> 8th-note grid (1/8 bar)
      0.25   -> quarter-note grid (1/4 bar)
      0.5    -> half-note grid (1/2 bar)
      1.0    -> whole-note / full-bar grid

    Durations are rounded to the nearest grid multiple, with a minimum of
    one grid step. The last note is adjusted so the total duration stays
    on the grid.
    """
    if grid_bars <= 0:
        grid_bars = BAR_GRID

    quantized = []
    for note_obj in pattern:
        if isinstance(note_obj, dict):
            note_name = note_obj.get("note")
            duration_bars = float(note_obj.get("duration", grid_bars))
        else:
            note_name = note_obj.note
            duration_bars = float(note_obj.duration)

        steps = max(1, round(duration_bars / grid_bars))
        quantized.append({"note": note_name, "duration": round(steps * grid_bars, 6)})

    # Align total length to the next integer multiple of grid_bars
    total = sum(n["duration"] for n in quantized)
    target = round(max(grid_bars, round(total / grid_bars) * grid_bars), 6)
    if quantized and abs(total - target) > 1e-9:
        quantized[-1]["duration"] = round(
            max(grid_bars, quantized[-1]["duration"] + (target - total)), 6
        )

    return quantized


def normalize_melody_pattern(pattern, target_bars: float = 4.0):
    notes = []
    for note_obj in pattern:
        if isinstance(note_obj, dict):
            note_name = note_obj.get('note')
            duration_bars = float(note_obj.get('duration', BAR_GRID))
        else:
            note_name = note_obj.note
            duration_bars = float(note_obj.duration)
        notes.append({'note': note_name, 'duration': normalize_duration_bars(duration_bars)})

    total_bars = sum(note['duration'] for note in notes)
    if total_bars == 0:
        return [{'note': notes[0]['note'] if notes else 'C4', 'duration': 4.0}]

    desired = target_bars
    if desired <= 0:
        desired = 4.0

    if abs(total_bars - desired) <= 0.25:
        delta = desired - total_bars
        notes[-1]['duration'] = normalize_duration_bars(notes[-1]['duration'] + delta)
    else:
        scale = desired / total_bars
        for note in notes:
            note['duration'] = normalize_duration_bars(note['duration'] * scale)

    total_bars = sum(note['duration'] for note in notes)
    if total_bars != desired and notes:
        notes[-1]['duration'] = normalize_duration_bars(notes[-1]['duration'] + (desired - total_bars))

    return notes


class MusicNote(BaseModel):
    note: Union[str, List[str]]
    duration: float  # in bars: 1.0 = one 4/4 bar

class MusicPrompt(BaseModel):
    prompt: str

class MusicIntent(BaseModel):
    tempo: int
    key: str
    scale: str
    instruments: List[str]
    melody_pattern: List[MusicNote]  # list of note objects with duration in 4/4 bars

class MidiEvent(BaseModel):
    note: int
    velocity: int
    start: float
    duration: float
    instrument: Optional[str] = None

class MidiEventList(BaseModel):
    events: List[MidiEvent]

@app.post("/prompt-to-intent", response_model=MusicIntent)
def prompt_to_intent(prompt: MusicPrompt):
    if client is None:
        return MusicIntent(
            tempo=120,
            key="C",
            scale="major",
            instruments=["piano", "drums"],
            melody_pattern=[
                MusicNote(note="C4", duration=0.5),
                MusicNote(note="D4", duration=0.5),
                MusicNote(note="E4", duration=0.5),
                MusicNote(note="F4", duration=0.5),
                MusicNote(note="G4", duration=0.5),
                MusicNote(note="A4", duration=0.5),
                MusicNote(note="B4", duration=0.5),
                MusicNote(note="B4", duration=0.5),
            ],
        )
    try:
        response = client.beta.chat.completions.parse(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a music composition assistant. "
                        "Given a natural language music description, extract structured musical intent. "
                        "Choose appropriate tempo (BPM), key (e.g. C, F#), scale (e.g. major, minor, dorian), "
                        "instruments, and a melody_pattern as a list of note objects. "
                        "Each note object must include a note name (e.g. C4, D#4, F5) or a chord as a list of note names, plus a duration in bars. "
                        "All music must be in 4/4 time. Durations are bar fractions: 1.0 = one full bar, 0.5 = half note, 0.25 = quarter note, 0.125 = eighth note, 0.0625 = sixteenth note. "
                        "Do not use durations smaller than 0.0625 bars. The total bar duration should form valid 4/4 measures. If you generate 4 bars, the total duration must equal exactly 4.0 bars. "
                        "Do not use seconds to describe note lengths. Only emit durations in bars. "
                        "It is allowed to layer voices: for a top lead over a bass line or chord progression, output a note array containing both low bass notes and high lead notes with the same duration. "
                        "Ensure the rhythm is valid in 4/4 and the note durations combine naturally to form measures. "
                        "If the prompt mentions a specific song, artist, genre, bass line, top lead, or chords, incorporate that into the melody_pattern, "
                        "tempo, key, and instruments. For bass lines, use lower notes and supporting rhythm; for top leads, use distinctive melodic motion; "
                        "for chords, use chord tones, arpeggios, or a harmonic outline as appropriate. "
                        "Examples at 120 BPM: "
                        "Bassline 1: [C2(0.5), C2(0.5), G1(0.5), G1(0.5), A1(0.5), A1(0.5), F1(0.5), F1(0.5)] for a driving electro bass. "
                        "Bassline 2: [D2(0.5), D2(0.5), A1(0.5), A1(0.5), B1(0.5), B1(0.5), G1(0.5), G1(0.5)] for a pop groove. "
                        "Bassline 3: [E2(0.5), E2(0.5), B1(0.5), B1(0.5), C#2(0.5), C#2(0.5), A1(0.5), A1(0.5)] for a house-style pulse. "
                        "Chord progression 1: [[C3, E3, G3](1.0), [G2, B2, D3](1.0), [A2, C3, E3](1.0), [F2, A2, C3](1.0)]. "
                        "Chord progression 2: [[D3, F3, A3](1.0), [Bb2, D3, F3](1.0), [F2, A2, C3](1.0), [C3, E3, G3](1.0)]. "
                        "Chord progression 3: [[A2, C3, E3](1.0), [F2, A2, C3](1.0), [C3, E3, G3](1.0), [G2, B2, D3](1.0)]. "
                        "Top melody 1: [E5(0.5), E5(0.5), F#5(0.5), G5(0.5), E5(0.5), D#5(0.5), B4(1.0)] for a memorable hook. "
                        "Top melody 2: [G4(0.5), A4(0.5), B4(0.5), E5(0.5), B4(0.5), A4(0.5), G4(1.0)] for a pop lead. "
                        "Top melody 3: [B4(0.5), D#5(0.5), E5(0.5), G5(0.5), E5(1.0), D#5(1.0)] for a bright uplifting line. "
                        "Use all information from the prompt to tailor the musical elements accordingly."
                    ),
                },
                {"role": "user", "content": prompt.prompt},
            ],
            response_format=MusicIntent,
        )
        return response.choices[0].message.parsed
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/prompt-to-fl-studio")
def prompt_to_fl_studio(prompt: MusicPrompt, background_tasks: BackgroundTasks):
    intent = prompt_to_intent(prompt).model_dump()
    normalized_pattern = normalize_melody_pattern(intent['melody_pattern'], target_bars=4.0)
    converted_pattern = []
    for note in normalized_pattern:
        note_value = note['note']
        if isinstance(note_value, list):
            midi_notes = sanitize_midi_notes([note_to_midi(n) for n in note_value])
            converted_pattern.append({'note': midi_notes, 'duration': note['duration']})
        else:
            midi_note = sanitize_midi_notes([note_to_midi(note_value)])[0]
            converted_pattern.append({'note': midi_note, 'duration': note['duration']})
    intent['melody_pattern'] = converted_pattern
    background_tasks.add_task(play_melody, intent)
    return {"status": "playing", "intent": intent}


@app.post("/intent-to-midi", response_model=List[MidiEvent])
def intent_to_midi(intent: MusicIntent):
    if client is None:
        events = []
        time = 0.0
        bar_seconds = 60.0 / intent.tempo * 4
        normalized = normalize_melody_pattern(intent.melody_pattern, target_bars=4.0)
        for note_obj in normalized:
            duration_seconds = note_obj['duration'] * bar_seconds
            if isinstance(note_obj['note'], list):
                midi_notes = sanitize_midi_notes([note_to_midi(n) for n in note_obj['note']])
                for midi_note in midi_notes:
                    events.append(MidiEvent(
                        note=midi_note,
                        velocity=100,
                        start=time,
                        duration=duration_seconds,
                        instrument=intent.instruments[0] if intent.instruments else None,
                    ))
            else:
                note = note_to_midi(note_obj['note'])
                events.append(MidiEvent(
                    note=note,
                    velocity=100,
                    start=time,
                    duration=duration_seconds,
                    instrument=intent.instruments[0] if intent.instruments else None,
                ))
            time += duration_seconds
        return events
    try:
        melody_description = []
        for note_obj in intent.melody_pattern:
            if isinstance(note_obj, dict):
                note_name = note_obj['note']
                duration_bars = note_obj['duration']
            else:
                note_name = note_obj.note
                duration_bars = note_obj.duration
            melody_description.append(f"{note_name}({duration_bars})")

        intent_description = (
            f"Tempo: {intent.tempo} BPM\n"
            f"Key: {intent.key}\n"
            f"Scale: {intent.scale}\n"
            f"Instruments: {', '.join(intent.instruments)}\n"
            f"Melody pattern (note/duration in bars): {', '.join(melody_description)}"
        )
        response = client.beta.chat.completions.parse(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a MIDI sequencer. Given a musical intent, produce a list of MIDI events. "
                        "Each event has: note (MIDI number), velocity (0-127), start (seconds), "
                        "duration (seconds), and instrument (name string). "
                        "Treat durations as bar-based values from the intent and convert them to seconds using 4/4 time. "
                        "Do not choose durations in seconds directly; use the duration values in bars from the intent. "
                        "Assign appropriate velocities for dynamics, stagger start times based on the tempo, "
                        "and assign the correct instrument from the provided list to each note."
                    ),
                },
                {"role": "user", "content": intent_description},
            ],
            response_format=MidiEventList,
        )
        return response.choices[0].message.parsed.events
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
