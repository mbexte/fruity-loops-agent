"""
fl_mcp_server.py — MCP server exposing FL Studio MIDI control as tools.

Tools:
  open_fl_studio           — Launch FL Studio
  play_melody_in_fl_studio — Single-layer: arm recording, play melody, stop recording
  play_song                — Multi-layer: melody + chords + bass via absolute-time scheduler
  start_recording          — Send MIDI note 72 (start recording)
  stop_recording           — Send MIDI note 74 (stop recording)
  quantize_melody          — Snap note durations to a rhythmic grid

Run standalone (used by Claude Code / agent.py via MCP):
  python fl_mcp_server.py
"""

import asyncio
import glob
import os
import subprocess
import sys

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
# MCP server
# ---------------------------------------------------------------------------

server = Server("fl-agent")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
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

    elif name == "play_song":
        tempo  = int(arguments.get("tempo", 120))
        layers = arguments.get("layers", {})
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
        import json
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
