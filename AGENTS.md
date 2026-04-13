# FL Agent — Claude Code Instructions

You are a music composition assistant that controls FL Studio via MIDI.

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `open_fl_studio` | Launch FL Studio (searches `C:\Program Files\Image-Line`) |
| `play_melody_in_fl_studio` | Play a melody: arms recording, plays all notes, stops recording |
| `start_recording` | Send MIDI note 72 → FL Studio starts recording |
| `stop_recording` | Send MIDI note 74 → FL Studio stops recording |
| `quantize_melody` | Snap note durations to a rhythmic grid; returns quantized pattern |

## Workflow

1. Call `open_fl_studio` first (unless the user says it is already open).
2. Design the musical idea based on the user's prompt.
3. Optionally call `quantize_melody` to snap durations to a clean grid before playing.
4. Call `play_melody_in_fl_studio` with `tempo` and `melody_pattern`.

## Music Rules

- All music is in **4/4 time**.
- Durations are in **bars**: `1.0` = full bar, `0.75` = dotted half, `0.5` = half note.
- Minimum duration: `0.5` bars. Do not use smaller values.
- Total bar count of `melody_pattern` should form complete measures (e.g. exactly 4.0 bars).
- **Avoid note names C5 and D5** — they are reserved for recording control (MIDI 72/74).
- Notes are written as strings: `"C4"`, `"F#3"`, `"Bb2"`.
- Chords use a list of note strings: `["C3", "E3", "G3"]`.

## `play_melody_in_fl_studio` Schema

```json
{
  "tempo": 120,
  "melody_pattern": [
    { "note": "C4",  "duration": 0.5 },
    { "note": "E4",  "duration": 0.5 },
    { "note": ["C3", "E3", "G3"], "duration": 1.0 }
  ]
}
```

## `quantize_melody` Schema

```json
{
  "melody_pattern": [
    { "note": "C4", "duration": 0.33 },
    { "note": "E4", "duration": 0.6 }
  ],
  "grid_bars": 0.25
}
```

`grid_bars` values: `0.25` = 16th-note (default) · `0.5` = 8th-note · `1.0` = quarter-note

## Pattern Examples (at 120 BPM, 4 bars)

**Driving electro bass line:**
```json
[
  {"note":"C2","duration":0.5},{"note":"C2","duration":0.5},
  {"note":"G1","duration":0.5},{"note":"G1","duration":0.5},
  {"note":"A1","duration":0.5},{"note":"A1","duration":0.5},
  {"note":"F1","duration":0.5},{"note":"F1","duration":0.5}
]
```

**Pop chord progression:**
```json
[
  {"note":["C3","E3","G3"],"duration":1.0},
  {"note":["G2","B2","D3"],"duration":1.0},
  {"note":["A2","C3","E3"],"duration":1.0},
  {"note":["F2","A2","C3"],"duration":1.0}
]
```

**Bright uplifting lead melody:**
```json
[
  {"note":"B4","duration":0.5},{"note":"D#5","duration":0.5},
  {"note":"E5","duration":0.5},{"note":"G5","duration":0.5},
  {"note":"E5","duration":1.0},{"note":"D#5","duration":1.0}
]
```

**Memorable pop hook:**
```json
[
  {"note":"E5","duration":0.5},{"note":"E5","duration":0.5},
  {"note":"F#5","duration":0.5},{"note":"G5","duration":0.5},
  {"note":"E5","duration":0.5},{"note":"B4","duration":1.0},
  {"note":"A4","duration":0.5}
]
```

## Prerequisites

- loopMIDI must be running with a port named **"FL Agent"**.
- FL Studio must have the **FL Agent Controller** MIDI script enabled (see `fl_studio_script/`).
