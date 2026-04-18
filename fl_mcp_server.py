"""
fl_mcp_server.py — MCP server exposing FL Studio MIDI control as tools.

Tools:
  open_fl_studio           — Launch FL Studio
  play_melody_in_fl_studio — Single-layer: arm recording, play melody, stop recording
  play_song                — Multi-layer: melody + chords + bass via absolute-time scheduler
  start_recording          — Send MIDI note 72 (start recording)
  stop_recording           — Send MIDI note 74 (stop recording)
  quantize_melody          — Snap note durations to a rhythmic grid
  search_sheet_music       — Search IMSLP and Open Opus for sheet music of a given song

Run standalone (used by Claude Code / agent.py via MCP):
  python fl_mcp_server.py
"""

import asyncio
import glob
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

# Make sure fl_transport and music_api are importable from the same directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fl_transport
from midi_scheduler import play_song as _play_song
from music_api import note_to_midi, sanitize_midi_notes, normalize_melody_pattern, quantize_melody_pattern

# ---------------------------------------------------------------------------
# FL Studio discovery
# ---------------------------------------------------------------------------

_FL_SEARCH_ROOTS = [
    r"C:\Program Files\Image-Line",
    r"C:\Program Files (x86)\Image-Line",
]


def _find_fl_studio() -> str | None:
    for root in _FL_SEARCH_ROOTS:
        matches = glob.glob(os.path.join(root, "**", "FL64.exe"), recursive=True)
        if matches:
            return matches[0]
    return None


# ---------------------------------------------------------------------------
# Music-theory helpers for sheet-music note / chord generation
# ---------------------------------------------------------------------------

_CHROMATIC = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
_FLAT_MAP  = {'Db': 'C#', 'Eb': 'D#', 'Fb': 'E', 'Gb': 'F#', 'Ab': 'G#', 'Bb': 'A#', 'Cb': 'B'}

_MAJOR_SCALE    = [0, 2, 4, 5, 7, 9, 11]
_MINOR_SCALE    = [0, 2, 3, 5, 7, 8, 10]   # natural minor
_MAJOR_QUALS    = ['major', 'minor', 'minor', 'major', 'major', 'minor', 'diminished']
_MINOR_QUALS    = ['minor', 'diminished', 'major', 'minor', 'minor', 'major', 'major']


def _root_idx(root: str) -> int:
    return _CHROMATIC.index(_FLAT_MAP.get(root, root))


def _triad(root_idx: int, quality: str, octave: int) -> list:
    """Return [root, third, fifth] note name strings."""
    intervals = {'major': (0, 4, 7), 'minor': (0, 3, 7), 'diminished': (0, 3, 6)}[quality]
    notes = []
    for iv in intervals:
        idx = (root_idx + iv) % 12
        oct_bump = (root_idx + iv) // 12
        notes.append(f"{_CHROMATIC[idx]}{octave + oct_bump}")
    return notes


def _extract_key(texts: list) -> tuple:
    """Scan text for patterns like 'C# minor', 'D-flat major'. Returns (root, mode)."""
    combined = ' '.join(texts)
    m = re.search(r'\b([A-G][#b]?)\s+(major|minor)\b', combined, re.IGNORECASE)
    if m:
        return m.group(1), m.group(2).lower()
    m = re.search(r'\b([A-G])[-\s](?:sharp|♯)\s+(major|minor)\b', combined, re.IGNORECASE)
    if m:
        return m.group(1) + '#', m.group(2).lower()
    m = re.search(r'\b([A-G])[-\s](?:flat|♭)\s+(major|minor)\b', combined, re.IGNORECASE)
    if m:
        return m.group(1) + 'b', m.group(2).lower()
    return None, None


def _build_fl_layers(root: str, mode: str) -> dict:
    """
    Return 8 bars of chords, bass, and melody in FL Studio layer format
    for the given key (root + 'major'/'minor').

    Chord progression:
      major → I – V – vi – IV  (each chord 2 bars)
      minor → i – VI – III – VII
    Melody: 32 quarter-notes (8 bars) built from scale tones at octave 4.
    Bass:   root–root–fifth–root pattern in octave 2, 0.5-bar notes.
    """
    ri    = _root_idx(root)
    scale = _MAJOR_SCALE if mode == 'major' else _MINOR_SCALE
    quals = _MAJOR_QUALS  if mode == 'major' else _MINOR_QUALS

    prog  = [0, 4, 5, 3] if mode == 'major' else [0, 5, 2, 6]  # degree indices

    # Scale tones for melody (octave 4, avoids C5/D5 reserved notes)
    scale_notes = [
        f"{_CHROMATIC[(ri + scale[i % 7]) % 12]}4"
        for i in range(8)
    ]

    chords_layer, bass_layer, melody_layer = [], [], []

    # Melodic contours per chord (scale-degree indices, 8 quarter-notes = 2 bars)
    contours = [
        [0, 2, 4, 2, 1, 3, 2, 0],
        [4, 3, 2, 4, 3, 2, 1, 4],
        [5, 4, 3, 5, 4, 3, 2, 1],
        [3, 2, 1, 3, 2, 0, 2, 0],
    ]

    for i, degree in enumerate(prog):
        chord_ri   = (ri + scale[degree]) % 12
        quality    = quals[degree]
        chord_root = _CHROMATIC[chord_ri]
        fifth_ri   = (chord_ri + 7) % 12

        chords_layer.append({"note": _triad(chord_ri, quality, 3), "duration": 2.0})

        # Bass: 8 × 0.5-bar notes per 2-bar chord block
        for beat in range(8):
            n = f"{_CHROMATIC[fifth_ri]}2" if beat % 4 == 2 else f"{chord_root}2"
            bass_layer.append({"note": n, "duration": 0.5})

        for idx in contours[i]:
            melody_layer.append({"note": scale_notes[idx], "duration": 0.25})

    return {"chords": chords_layer, "bass": bass_layer, "melody": melody_layer}


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

server = Server("fl-agent")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_sheet_music",
            description=(
                "Search for sheet music (Musiknoten) of a given song or composition. "
                "Queries IMSLP (the largest free sheet-music library) and Open Opus "
                "(classical music catalogue) and returns titles, composers, genres, and "
                "direct URLs to the scores. "
                "Use this tool whenever the user mentions a song, piece, or composer and "
                "wants to find, view, or reference its sheet music."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Song title or composition to search for, e.g. "
                            "'Moonlight Sonata', 'Für Elise', 'Bohemian Rhapsody'."
                        ),
                    },
                    "composer": {
                        "type": "string",
                        "description": (
                            "Optional composer name to narrow the search, "
                            "e.g. 'Beethoven', 'Mozart', 'Chopin'."
                        ),
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="open_fl_studio",
            description=(
                "Launch FL Studio. Searches common install paths under "
                r"C:\Program Files\Image-Line for FL64.exe and opens it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional explicit path to FL64.exe. "
                            "If omitted, the server searches automatically."
                        ),
                    }
                },
            },
        ),
        types.Tool(
            name="play_melody_in_fl_studio",
            description=(
                "Arm FL Studio recording, play a melody via loopMIDI, then stop recording. "
                "Requires loopMIDI running with a port named 'FL Agent'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tempo": {
                        "type": "integer",
                        "description": "Tempo in BPM (e.g. 120).",
                    },
                    "melody_pattern": {
                        "type": "array",
                        "description": (
                            "List of note objects. Each object has 'note' (note name string "
                            "like 'C4', 'F#3', or a list of note names for chords) and "
                            "'duration' (length in 4/4 bars; 1.0 = full bar, 0.5 = half note, "
                            "0.25 = quarter note, 0.125 = eighth note, 0.0625 = sixteenth note)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "note": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {"type": "array", "items": {"type": "string"}},
                                    ],
                                    "description": "Note name(s), e.g. 'C4' or ['C3','E3','G3'].",
                                },
                                "duration": {
                                    "type": "number",
                                    "description": "Duration in bars (min 0.0625).",
                                },
                            },
                            "required": ["note", "duration"],
                        },
                    },
                },
                "required": ["tempo", "melody_pattern"],
            },
        ),
        types.Tool(
            name="start_recording",
            description="Send MIDI note 72 (C5) to FL Studio to arm and start recording.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="stop_recording",
            description="Send MIDI note 74 (D5) to FL Studio to stop recording.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="quantize_melody",
            description=(
                "Snap note durations in a melody pattern to the nearest rhythmic grid. "
                "Returns the quantized pattern ready to pass to play_melody_in_fl_studio."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "melody_pattern": {
                        "type": "array",
                        "description": "List of note objects with 'note' and 'duration' (in bars).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "note": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {"type": "array", "items": {"type": "string"}},
                                    ]
                                },
                                "duration": {"type": "number"},
                            },
                            "required": ["note", "duration"],
                        },
                    },
                    "grid_bars": {
                        "type": "number",
                        "description": (
                            "Grid resolution in bars. "
                            "0.0625 = 16th-note grid (default), "
                            "0.125 = 8th-note grid, "
                            "0.25 = quarter-note grid."
                        ),
                        "default": 0.0625,
                    },
                },
                "required": ["melody_pattern"],
            },
        ),
        types.Tool(
            name="play_song",
            description=(
                "Play a multi-layer composition (melody, chords, bass) via the absolute-time "
                "scheduler. All layers start simultaneously at t=0 — perfect synchronisation. "
                "Requires loopMIDI 'FL Agent' port. Wraps playback in FL Studio record start/stop."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tempo": {
                        "type": "integer",
                        "description": "Tempo in BPM.",
                    },
                    "layers": {
                        "type": "object",
                        "description": (
                            "Named layers. Each key is a layer name (e.g. 'melody', 'chords', 'bass') "
                            "and its value is a list of note objects with 'note' and 'duration' (bars)."
                        ),
                        "additionalProperties": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "note": {
                                        "oneOf": [
                                            {"type": "string"},
                                            {"type": "integer"},
                                            {"type": "array", "items": {"oneOf": [
                                                {"type": "string"}, {"type": "integer"}
                                            ]}},
                                        ],
                                        "description": "Note name ('C4'), MIDI int, or chord list.",
                                    },
                                    "duration": {
                                        "type": "number",
                                        "description": "Duration in bars (min 0.0625).",
                                    },
                                },
                                "required": ["note", "duration"],
                            },
                        },
                    },
                },
                "required": ["tempo", "layers"],
            },
        ),
        types.Tool(
            name="compose_music",
            description=(
                "Automatically generate and play 8 bars of music (melody + chords + bass) "
                "for a given musical key. Use this whenever the user asks you to play, compose, "
                "or create music without specifying individual notes. "
                "Much easier than play_song — just provide the key root and mode."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Key root note, e.g. 'C', 'G', 'F#', 'Bb'.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["major", "minor"],
                        "description": "'major' or 'minor'.",
                    },
                    "tempo": {
                        "type": "integer",
                        "description": "Tempo in BPM (40–999). Default 120.",
                    },
                },
                "required": ["key", "mode"],
            },
        ),
        # ── Transport control ─────────────────────────────────────────────────
        types.Tool(
            name="ping",
            description="Send a PING to the FL Agent Controller script and verify it is alive.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="request_status",
            description=(
                "Request a full status snapshot from FL Studio: "
                "is_playing, is_recording, loop_on, metronome_on, current pattern slot, tempo."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="set_tempo",
            description=(
                "Set the FL Studio tempo precisely via SysEx (14-bit, 40–999 BPM). "
                "Prefer this over the CC-based tempo when accuracy matters."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "bpm": {
                        "type": "integer",
                        "description": "Tempo in BPM (40–999).",
                    },
                },
                "required": ["bpm"],
            },
        ),
        types.Tool(
            name="rewind",
            description="Return the FL Studio playhead to bar 1 (position 0).",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="pause_resume",
            description="Pause the FL Studio transport if playing, or resume if paused.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="toggle_loop",
            description="Toggle FL Studio loop mode on or off.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="toggle_metronome",
            description="Toggle the FL Studio metronome on or off.",
            inputSchema={"type": "object", "properties": {}},
        ),
        # ── Pattern management ────────────────────────────────────────────────
        types.Tool(
            name="select_pattern",
            description="Switch FL Studio to a specific pattern slot (0-based index).",
            inputSchema={
                "type": "object",
                "properties": {
                    "slot": {
                        "type": "integer",
                        "description": "Pattern slot index (0–127).",
                    },
                },
                "required": ["slot"],
            },
        ),
        types.Tool(
            name="set_pattern_name",
            description="Assign a name to a pattern slot in FL Studio (max 32 ASCII characters).",
            inputSchema={
                "type": "object",
                "properties": {
                    "slot": {
                        "type": "integer",
                        "description": "Pattern slot index (0–127).",
                    },
                    "name": {
                        "type": "string",
                        "description": "Pattern name (ASCII only, max 32 chars).",
                    },
                },
                "required": ["slot", "name"],
            },
        ),
        types.Tool(
            name="pattern_clear",
            description="Erase all notes from a pattern slot in FL Studio.",
            inputSchema={
                "type": "object",
                "properties": {
                    "slot": {
                        "type": "integer",
                        "description": "Pattern slot index (0–127).",
                    },
                },
                "required": ["slot"],
            },
        ),
        # ── Mixer control ─────────────────────────────────────────────────────
        types.Tool(
            name="channel_mute",
            description="Mute or unmute a mixer channel in FL Studio.",
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "integer",
                        "description": "Mixer channel index (0-based).",
                    },
                    "mute": {
                        "type": "boolean",
                        "description": "True to mute, False to unmute.",
                    },
                },
                "required": ["channel", "mute"],
            },
        ),
        types.Tool(
            name="channel_solo",
            description="Solo or unsolo a mixer channel in FL Studio.",
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "integer",
                        "description": "Mixer channel index (0-based).",
                    },
                    "solo": {
                        "type": "boolean",
                        "description": "True to solo, False to unsolo.",
                    },
                },
                "required": ["channel", "solo"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:

    if name == "open_fl_studio":
        path = arguments.get("path") or _find_fl_studio()
        if not path:
            return [types.TextContent(
                type="text",
                text=(
                    "ERROR: FL Studio not found. "
                    "Install it under C:\\Program Files\\Image-Line or pass the path explicitly."
                ),
            )]
        try:
            subprocess.Popen([path])
            return [types.TextContent(type="text", text=f"FL Studio launched: {path}")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR launching FL Studio: {exc}")]

    elif name == "play_melody_in_fl_studio":
        tempo = int(arguments.get("tempo", 120))
        raw_pattern = arguments.get("melody_pattern", [])

        # Normalize durations
        normalized = normalize_melody_pattern(raw_pattern, target_bars=4.0)

        # Convert note names → MIDI numbers
        converted = []
        for note_obj in normalized:
            note_val = note_obj["note"]
            duration = note_obj["duration"]
            if isinstance(note_val, list):
                midi_notes = sanitize_midi_notes([note_to_midi(n) for n in note_val])
                converted.append({"note": midi_notes, "duration": duration})
            else:
                midi_note = sanitize_midi_notes([note_to_midi(note_val)])[0]
                converted.append({"note": midi_note, "duration": duration})

        intent = {"tempo": tempo, "melody_pattern": converted}

        try:
            # Run blocking MIDI I/O in a thread so we don't block the event loop.
            await asyncio.get_event_loop().run_in_executor(
                None, fl_transport.play_melody, intent
            )
            return [types.TextContent(
                type="text",
                text=f"Melody played at {tempo} BPM ({len(converted)} notes/chords).",
            )]
        except SystemExit as exc:
            return [types.TextContent(type="text", text=f"MIDI error: {exc}")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "start_recording":
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, fl_transport.start_recording
            )
            return [types.TextContent(type="text", text="Recording started.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "stop_recording":
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, fl_transport.stop_recording
            )
            return [types.TextContent(type="text", text="Recording stopped.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "compose_music":
        key    = arguments.get("key", "C").strip()
        mode   = arguments.get("mode", "major").lower()
        tempo  = int(arguments.get("tempo", 120))
        if mode not in ("major", "minor"):
            mode = "major"
        layers = _build_fl_layers(key, mode)
        intent = {"tempo": tempo, "layers": layers}
        try:
            await asyncio.get_event_loop().run_in_executor(None, _play_song, intent)
            total = sum(len(v) for v in layers.values())
            return [types.TextContent(
                type="text",
                text=f"Composed and played 8 bars in {key} {mode} at {tempo} BPM ({total} notes total).",
            )]
        except SystemExit as exc:
            return [types.TextContent(type="text", text=f"MIDI error: {exc}")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "play_song":
        tempo  = int(arguments.get("tempo", 120))
        layers = arguments.get("layers") or {}
        # If the agent forgot to include notes, fall back to C major auto-generation.
        if not layers or all(len(v) == 0 for v in layers.values()):
            layers = _build_fl_layers("C", "major")
        intent = {"tempo": tempo, "layers": layers}
        try:
            await asyncio.get_event_loop().run_in_executor(None, _play_song, intent)
            layer_names = ", ".join(layers.keys())
            total_notes = sum(len(v) for v in layers.values())
            return [types.TextContent(
                type="text",
                text=f"Song played at {tempo} BPM — layers: {layer_names} ({total_notes} notes total).",
            )]
        except SystemExit as exc:
            return [types.TextContent(type="text", text=f"MIDI error: {exc}")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "quantize_melody":
        raw_pattern = arguments.get("melody_pattern", [])
        grid_bars = float(arguments.get("grid_bars", 0.0625))
        quantized = quantize_melody_pattern(raw_pattern, grid_bars=grid_bars)
        return [types.TextContent(
            type="text",
            text=json.dumps({"grid_bars": grid_bars, "melody_pattern": quantized}, indent=2),
        )]

    # ── Transport control ─────────────────────────────────────────────────────

    elif name == "ping":
        try:
            await asyncio.get_event_loop().run_in_executor(None, fl_transport.ping)
            return [types.TextContent(type="text", text="PING sent — FL Agent Controller is alive.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "request_status":
        try:
            await asyncio.get_event_loop().run_in_executor(None, fl_transport.request_status)
            return [types.TextContent(type="text", text="STATUS requested from FL Studio.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "set_tempo":
        bpm = int(arguments.get("bpm", 120))
        try:
            await asyncio.get_event_loop().run_in_executor(None, fl_transport.set_tempo, bpm)
            return [types.TextContent(type="text", text=f"Tempo set to {bpm} BPM.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "rewind":
        try:
            with fl_transport.FLTransport() as t:
                await asyncio.get_event_loop().run_in_executor(None, t.rewind)
            return [types.TextContent(type="text", text="Rewound to bar 1.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "pause_resume":
        try:
            with fl_transport.FLTransport() as t:
                await asyncio.get_event_loop().run_in_executor(None, t.pause_resume)
            return [types.TextContent(type="text", text="Transport paused/resumed.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "toggle_loop":
        try:
            with fl_transport.FLTransport() as t:
                await asyncio.get_event_loop().run_in_executor(None, t.toggle_loop)
            return [types.TextContent(type="text", text="Loop mode toggled.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "toggle_metronome":
        try:
            with fl_transport.FLTransport() as t:
                await asyncio.get_event_loop().run_in_executor(None, t.toggle_metronome)
            return [types.TextContent(type="text", text="Metronome toggled.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    # ── Pattern management ────────────────────────────────────────────────────

    elif name == "select_pattern":
        slot = int(arguments.get("slot", 0))
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, fl_transport.select_pattern, slot
            )
            return [types.TextContent(type="text", text=f"Pattern slot {slot} selected.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "set_pattern_name":
        slot = int(arguments.get("slot", 0))
        pattern_name = str(arguments.get("name", ""))
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, fl_transport.set_pattern_name, slot, pattern_name
            )
            return [types.TextContent(type="text", text=f"Pattern {slot} named '{pattern_name}'.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "pattern_clear":
        slot = int(arguments.get("slot", 0))
        try:
            with fl_transport.FLTransport() as t:
                await asyncio.get_event_loop().run_in_executor(None, t.pattern_clear, slot)
            return [types.TextContent(type="text", text=f"Pattern {slot} cleared.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    # ── Mixer control ─────────────────────────────────────────────────────────

    elif name == "channel_mute":
        channel = int(arguments.get("channel", 0))
        mute = bool(arguments.get("mute", True))
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, fl_transport.channel_mute, channel, mute
            )
            state = "muted" if mute else "unmuted"
            return [types.TextContent(type="text", text=f"Channel {channel} {state}.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]

    elif name == "channel_solo":
        channel = int(arguments.get("channel", 0))
        solo = bool(arguments.get("solo", True))
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, fl_transport.channel_solo, channel, solo
            )
            state = "soloed" if solo else "unsoloed"
            return [types.TextContent(type="text", text=f"Channel {channel} {state}.")]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"ERROR: {exc}")]
    elif name == "search_sheet_music":
        query    = arguments.get("query", "").strip()
        composer = arguments.get("composer", "").strip()

        if not query:
            return [types.TextContent(type="text", text="ERROR: 'query' is required.")]

        def _fetch():
            results    = []
            key_texts  = []           # accumulate title/snippet strings for key detection
            search_term = f"{composer} {query}".strip() if composer else query

            # ── 1. IMSLP via MediaWiki API ────────────────────────────────────
            imslp_params = urllib.parse.urlencode({
                "action": "query",
                "list": "search",
                "srsearch": search_term,
                "srnamespace": "0",
                "srlimit": "5",
                "format": "json",
            })
            try:
                req = urllib.request.Request(
                    f"https://imslp.org/api.php?{imslp_params}",
                    headers={"User-Agent": "fl-agent/1.0"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                for hit in data.get("query", {}).get("search", []):
                    title   = hit.get("title", "")
                    snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
                    key_texts += [title, snippet]
                    results.append({
                        "source": "IMSLP",
                        "title": title,
                        "url": (
                            "https://imslp.org/wiki/"
                            + urllib.parse.quote(title.replace(" ", "_"), safe="/:(),'")
                        ),
                        "snippet": snippet[:200],
                    })
            except Exception as exc:
                results.append({"source": "IMSLP", "error": str(exc)})

            # ── 2. Open Opus (classical catalogue) ────────────────────────────
            try:
                openopus_url = (
                    "https://openopus.org/work/search/"
                    + urllib.parse.quote(query)
                    + ".json"
                )
                req = urllib.request.Request(
                    openopus_url, headers={"User-Agent": "fl-agent/1.0"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                if data.get("status", {}).get("success") == "true":
                    for work in data.get("works", [])[:4]:
                        comp_name = work.get("composer", {}).get("complete_name", "Unknown")
                        title     = work.get("title", "")
                        subtitle  = work.get("subtitle", "")
                        genre     = work.get("genre", "")
                        key_texts += [title, subtitle]
                        imslp_guess = (
                            "https://imslp.org/wiki/"
                            + urllib.parse.quote(
                                f"{title} ({comp_name})".replace(" ", "_"), safe="/:(),',"
                            )
                        )
                        results.append({
                            "source":     "Open Opus",
                            "title":      title,
                            "composer":   comp_name,
                            "genre":      genre,
                            "imslp_url":  imslp_guess,
                        })
            except Exception as exc:
                results.append({"source": "Open Opus", "error": str(exc)})

            # ── 3. Derive key and build playable notes / chords ───────────────
            root, mode = _extract_key(key_texts)
            if root is None:
                # Fall back to C major if no key found in the returned metadata
                root, mode = "C", "major"
                key_source = "default (C major — key not found in metadata)"
            else:
                key_source = "detected from metadata"

            fl_layers = _build_fl_layers(root, mode)

            return results, root, mode, key_source, fl_layers

        results, root, mode, key_source, fl_layers = \
            await asyncio.get_event_loop().run_in_executor(None, _fetch)

        real_results = [r for r in results if "error" not in r]
        if not real_results:
            errors = "; ".join(r.get("error", "") for r in results if "error" in r)
            return [types.TextContent(
                type="text",
                text=f"No sheet music found for '{query}'. API errors: {errors}",
            )]

        payload = {
            "query":    query,
            "composer": composer or None,
            "results":  results,
            "key": {
                "root":   root,
                "mode":   mode,
                "source": key_source,
            },
            "fl_studio_layers": {
                "description": (
                    f"8-bar playable arrangement in {root} {mode}. "
                    "Pass 'fl_studio_layers.layers' and a tempo to play_song."
                ),
                "tempo_suggestion": 120,
                "layers": fl_layers,
            },
        }
        return [types.TextContent(
            type="text",
            text=json.dumps(payload, indent=2, ensure_ascii=False),
        )]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
