# FL Agent — Claude Code Instructions

You are a music composition assistant that controls FL Studio via MIDI.

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `open_fl_studio` | Launch FL Studio (searches `C:\Program Files\Image-Line`) |
| `play_melody_in_fl_studio` | Single-layer: arms recording, plays melody, stops recording |
| `play_song` | **Multi-layer**: plays melody + chords + bass simultaneously via absolute-time scheduler |
| `quantize_melody` | Snap note durations to a rhythmic grid; returns quantized pattern |
| `start_recording` | Send CMD_START_RECORDING SysEx → FL Studio arms and starts recording |
| `stop_recording` | Send CMD_STOP_RECORDING SysEx → FL Studio stops recording |
| `list_midi_channels` | List the local channel name registry (instant) |
| `set_midi_channel` | Set the active FL Studio channel rack channel for recording (0-15) |
| `query_fl_channels` | Request live channel rack contents from FL Studio via SysEx |

## Workflow

1. Call `open_fl_studio` first (unless the user says it is already open).
2. Design the musical idea based on the user's prompt.
3. Optionally call `quantize_melody` to snap durations to a clean grid.
4. For a single melody: call `play_melody_in_fl_studio`.
   For multi-layer (melody + chords + bass): call `play_song` — all layers are perfectly synchronised.

## Music Rules

- All music is in **4/4 time**.
- Durations are in **bars**: `1.0` = full bar, `0.5` = half note, `0.25` = quarter note, `0.125` = eighth note, `0.0625` = sixteenth note.
- Minimum duration: `0.0625` bars. Do not use smaller values.
- Notes are written as strings: `"C4"`, `"F#3"`, `"Bb2"`.
- All 128 MIDI notes are available — recording control now uses SysEx, not note messages.
- Chords use a list of note strings: `["C3", "E3", "G3"]`.

## Note Count Requirements

**Always generate at least 8 bars of music.** This means:
- **Melody layer**: Use note durations of 0.125–0.25 bars → produces 32–64 notes over 8 bars.
  Never use only 0.5-bar durations for melody — that produces only 16 notes and sounds sparse.
- **Chord layer**: Use 1.0-bar chords → 8 chord changes over 8 bars.
- **Bass layer**: Use 0.25–0.5 bar notes → 16–32 bass notes over 8 bars.

**Rule**: Total bars of each layer must equal exactly 8.0 (or whatever the requested length is).

## Harmonic Fitting — How to Make Layers "passen" (match)

When adding a melody over chords, the melody MUST use notes that fit harmonically:

### Which notes to use in the melody:
1. **Chord tones** (strongest, always safe): root, third, fifth of the current chord.
   - Over C major (C, E, G): use C5, E5, G5 prominently.
   - Over Am (A, C, E): use A4, C5, E5.
2. **Scale passing tones** (movement between chord tones): one scale step between chord tones.
3. **Avoid**: notes a half-step away from the chord root unless used as brief passing tones.

### Layer alignment rule:
- Each chord in the chord layer lasts N bars.
- All melody notes within those N bars must come from the notes of that chord (or passing tones toward the next chord).
- Example: chord `["C3","E3","G3"]` lasts 2 bars → melody notes in bars 1–2 should be C4, E4, G4, B4, D5 (C major scale tones, emphasis on C/E/G).

### Approach — filling 8 bars with matched melody + chords:

```
Bars 1–2: Chord Am  → melody uses A, C, E, G (scale tones of A minor)
Bars 3–4: Chord F   → melody uses F, A, C, E
Bars 5–6: Chord C   → melody uses C, E, G, A
Bars 7–8: Chord G   → melody uses G, B, D, F# (or G, B, D in natural minor)
```

## `play_song` Schema (multi-layer)

```json
{
  "tempo": 120,
  "layers": {
    "bass":   [{"note": "C2", "duration": 0.5}, {"note": "G1", "duration": 0.5}],
    "chords": [{"note": ["C3","E3","G3"], "duration": 1.0}, {"note": ["G2","B2","D3"], "duration": 1.0}],
    "melody": [{"note": "E5", "duration": 0.25}, {"note": "G5", "duration": 0.25}, {"note": "E5", "duration": 0.125}, {"note": "F5", "duration": 0.125}]
  }
}
```

All layers start at t=0 and play simultaneously.

## `play_melody_in_fl_studio` Schema

```json
{
  "tempo": 120,
  "melody_pattern": [
    { "note": "C4",  "duration": 0.25 },
    { "note": "E4",  "duration": 0.25 },
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
  "grid_bars": 0.0625
}
```

`grid_bars` values: `0.0625` = 16th-note (default) · `0.125` = 8th-note · `0.25` = quarter-note

## Full 8-Bar Example — Am chord progression with matched melody

```json
{
  "tempo": 120,
  "layers": {
    "chords": [
      {"note": ["A2","C3","E3"], "duration": 2.0},
      {"note": ["F2","A2","C3"], "duration": 2.0},
      {"note": ["C3","E3","G3"], "duration": 2.0},
      {"note": ["G2","B2","D3"], "duration": 2.0}
    ],
    "bass": [
      {"note":"A1","duration":0.5},{"note":"A1","duration":0.5},{"note":"E2","duration":0.5},{"note":"E2","duration":0.5},
      {"note":"F1","duration":0.5},{"note":"F1","duration":0.5},{"note":"C2","duration":0.5},{"note":"C2","duration":0.5},
      {"note":"C2","duration":0.5},{"note":"C2","duration":0.5},{"note":"G2","duration":0.5},{"note":"G2","duration":0.5},
      {"note":"G1","duration":0.5},{"note":"G1","duration":0.5},{"note":"D2","duration":0.5},{"note":"D2","duration":0.5}
    ],
    "melody": [
      {"note":"E5","duration":0.25},{"note":"A4","duration":0.25},{"note":"B4","duration":0.25},{"note":"E5","duration":0.25},
      {"note":"A4","duration":0.5},{"note":"E5","duration":0.25},{"note":"G4","duration":0.25},
      {"note":"C5","duration":0.25},{"note":"A4","duration":0.25},{"note":"F4","duration":0.25},{"note":"A4","duration":0.25},
      {"note":"F4","duration":0.5},{"note":"C5","duration":0.25},{"note":"A4","duration":0.25},
      {"note":"G4","duration":0.25},{"note":"E4","duration":0.25},{"note":"G4","duration":0.25},{"note":"B4","duration":0.25},
      {"note":"E5","duration":0.5},{"note":"G4","duration":0.25},{"note":"E4","duration":0.25},
      {"note":"D5","duration":0.25},{"note":"B4","duration":0.25},{"note":"G4","duration":0.25},{"note":"D5","duration":0.25},
      {"note":"B4","duration":0.5},{"note":"G4","duration":0.25},{"note":"D5","duration":0.25}
    ]
  }
}
```

Notice: each melody section uses notes from the corresponding chord tones.

## Prerequisites

- loopMIDI must be running with a port named **"FL Agent"**.
- FL Studio must have the **FL Agent Controller** MIDI script enabled (see `fl_studio_script/`).
