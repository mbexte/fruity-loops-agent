# FL Agent

Generate music and send it directly to FL Studio by chatting with a Claude AI agent. Describe what you want in plain English — the agent composes a melody and plays it into FL Studio's piano roll via MIDI.

```
You: "play me a driving electro bass line in F minor"
  → Claude designs melody
  → MCP tools send MIDI via loopMIDI
  → FL Studio records the notes
```

## Prerequisites

| Requirement | Notes |
|---|---|
| [Python 3.11+](https://www.python.org/downloads/) | Must be on PATH |
| [Node.js / npm](https://nodejs.org/) | Required to install Claude Code CLI and GitHub Copilot CLI |
| [Claude Code CLI](https://claude.ai/code) | Installed automatically by `START_AGENT.ps1`, or manually: `npm install -g @anthropic-ai/claude-code` |
| [GitHub Copilot CLI](https://github.com/github/feedback/discussions/7878) | Automatically installed by `START_AGENT.ps1 copilot` if not present. Uses `gh copilot` command after installing GitHub CLI and the copilot extension. |
| [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) | Free virtual MIDI port driver |
| FL Studio | Any version with MIDI scripting support |
| Anthropic API key | Set as `ANTHROPIC_API_KEY` in your environment |

## Quick Start

1. **Set up loopMIDI and FL Studio** — see [FL Studio MIDI Setup](#fl-studio-midi-setup) below.

2. **Run the launch script** from the repo root:

```powershell
.\START_AGENT.ps1
```

This installs Claude Code if needed, creates the Python venv, installs dependencies, registers the MCP server, and opens Claude.

> To use Copilot CLI instead of Claude, pass the `copilot` mode:
>
> ```powershell
> .\START_AGENT.ps1 copilot
> ```
> 
> The script will automatically install GitHub CLI and the Copilot extension if needed, then launch `gh copilot`. Falls back to VS Code if installation fails.
>
> If you use `.\START_AGENT.ps1 openrouter`, the custom OpenRouter CLI now streams assistant output live and shows tool activity while it works.

3. **Describe your music** in the Claude prompt:

```
play me an upbeat bass line in E minor at 128 BPM
```

```
create a pop chord progression in C major
```

```
generate a bright lead melody inspired by 80s synth pop
```

Claude will open FL Studio (if not already running), compose the pattern, and play it into the active piano roll.

## FL Studio MIDI Setup

Do this once before using the agent.

1. Start **loopMIDI** and create a virtual port named exactly `FL Agent`.
2. Open FL Studio → **Options → MIDI Settings**.
3. In the Input list, enable the `FL Agent` port.
4. Set its Controller type to **FL Agent Controller** (provided in `fl_studio_script/`).
5. The agent uses MIDI note `72` (C5) to start recording and `74` (D5) to stop — these are reserved and never emitted as musical notes.

To install the controller script, copy `fl_studio_script/device_FL Agent Controller.py` into a
new subfolder `FL Agent Controller\` inside:
```
%USERPROFILE%\Documents\Image-Line\FL Studio\Settings\Hardware\
```
and copy `fl_studio_script/FL Agent Controller.ini` directly into that `Hardware\` folder (next
to the subfolder). Then restart FL Studio.

## Manual Setup

If you prefer not to use `START_AGENT.ps1`:

```powershell
# 1. Create venv and install deps
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 2. Register the MCP server with Claude Code
#    Add the following to .claude\settings.json in this repo:
#    {
#      "mcpServers": {
#        "fl-agent": {
#          "command": "<absolute path>/.venv/Scripts/python.exe",
#          "args": ["<absolute path>/fl_mcp_server.py"]
#        }
#      }
#    }

# 3. Launch Claude Code from the repo root
claude
```

## Available MCP Tools

The agent has access to these tools (defined in `fl_mcp_server.py`):

| Tool | What it does |
|---|---|
| `open_fl_studio` | Launches FL Studio from its default install path |
| `play_melody_in_fl_studio` | Arms recording, plays all notes via loopMIDI, stops recording |
| `quantize_melody` | Snaps note durations to a rhythmic grid measured in bars (16th-note grid by default, 1/16 bar) |
| `start_recording` | Sends MIDI note 72 → FL Studio starts recording |
| `stop_recording` | Sends MIDI note 74 → FL Studio stops recording |

## File Reference

| File | Purpose |
|---|---|
| `START_AGENT.ps1` | One-click setup + Claude Code launcher |
| `fl_mcp_server.py` | MCP server — exposes FL Studio control as tools |
| `AGENTS.md` | Persistent instructions Claude reads as context |
| `fl_transport.py` | Low-level MIDI transport via loopMIDI |
| `music_api.py` | Note utilities and (legacy) FastAPI endpoints |
| `fl_studio_script/device_FL Agent Controller.py` | FL Studio MIDI controller script |
| `fl_studio_script/FL Agent Controller.ini`       | Sidecar `.ini` required by FL Studio's Hardware folder |
| `requirements.txt` | Python dependencies |
