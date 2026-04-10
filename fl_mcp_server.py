"""
fl_mcp_server.py — MCP server exposing FL Studio MIDI control as tools.

Tools:
  open_fl_studio           — Launch FL Studio
  play_melody_in_fl_studio — Arm recording, play melody, stop recording
  start_recording          — Send MIDI note 72 (start recording)
  stop_recording           — Send MIDI note 74 (stop recording)

Run standalone (used by Claude Code via MCP):
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
from music_api import note_to_midi, sanitize_midi_notes, normalize_melody_pattern

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
                            "'duration' (length in 4/4 bars; 1.0 = full bar, 0.5 = half bar)."
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
                                    "description": "Duration in bars (min 0.5).",
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
