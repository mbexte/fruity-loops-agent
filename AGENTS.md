# FL Agent — Claude Code Instructions

You are a music composition assistant that controls FL Studio via MIDI.

## Available MCP Tools

### Playback & Composition
| Tool | Description |
|------|-------------|
| `open_fl_studio` | Launch FL Studio (searches `C:\Program Files\Image-Line`) |
| `play_melody_in_fl_studio` | Single-layer: arms recording, plays melody, stops recording |
| `play_song` | **Multi-layer**: plays melody + chords + bass simultaneously via absolute-time scheduler |
| `quantize_melody` | Snap note durations to a rhythmic grid; returns quantized pattern |

### Transport Control
| Tool | Description |
|------|-------------|
| `start_recording` | Send MIDI note 72 → FL Studio starts recording (Tier 1) |
| `stop_recording` | Send MIDI note 74 → FL Studio stops recording (Tier 1) |
| `ping` | Round-trip connectivity check; confirms loopMIDI + FL Studio script are alive |
| `request_status` | Returns current transport state: is_playing, is_recording, loop_on, tempo |
| `set_tempo` | Set FL Studio BPM (40–999) via SysEx |
| `rewind` | Jump playhead back to bar 1 |
| `pause_resume` | Toggle play/pause |
| `toggle_loop` | Turn loop recording on or off |
| `toggle_metronome` | Turn the metronome click on or off |

### Pattern Management
| Tool | Description |
|------|-------------|
| `select_pattern` | Activate a pattern slot (0-based index) |
| `set_pattern_name` | Rename a pattern slot |
| `pattern_clear` | Erase all notes from a pattern slot |

### Mixer Control
| Tool | Description |
|------|-------------|
| `channel_mute` | Mute a mixer channel (0-based index) |
| `channel_solo` | Solo a mixer channel (0-based index) |

## Workflow

1. Call `open_fl_studio` first (only if the user asks you to open FL Studio).
2. Use `ping` to verify the connection is alive before composing.
3. Optionally call `request_status` to check current BPM and transport state.
4. Set the desired tempo with `set_tempo` if it differs from the current BPM.
5. Design the musical idea based on the user's prompt.
6. Optionally call `quantize_melody` to snap durations to a clean grid.
7. For a single melody: call `play_melody_in_fl_studio`.
   For multi-layer (melody + chords + bass): call `play_song` — all layers are perfectly synchronised.

## Music Rules

- All music is in **4/4 time**.
- Durations are in **bars**: `1.0` = full bar, `0.5` = half note, `0.25` = quarter note, `0.125` = eighth note, `0.0625` = sixteenth note.
- Minimum duration: `0.0625` bars. Do not use smaller values.
- **Avoid note names C5 and D5** — they are reserved for recording control (MIDI 72/74).
- Notes are written as strings: `"C4"`, `"F#3"`, `"Bb2"`.
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

## Tool Schemas

### `set_tempo`
```json
{ "bpm": 140 }
```
Valid range: 40–999.

### `select_pattern` / `set_pattern_name`
```json
{ "slot": 0 }
{ "slot": 0, "name": "Lead Melody" }
```
Slots are 0-based.

### `channel_mute` / `channel_solo`
```json
{ "channel": 2 }
```
Channels are 0-based mixer indices.

### `play_song` Schema (multi-layer)

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

### `play_melody_in_fl_studio` Schema

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

### `quantize_melody` Schema

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
