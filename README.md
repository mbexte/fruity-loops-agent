# FL Agent

A FastAPI-based music API for generating melody and MIDI intent, then sending notes to FL Studio via MIDI.

## Setup

### 1. Create and activate a Python virtual environment

From the repo root (`c:\GIT\fl-agent\fl-agent`):

PowerShell:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Bash:
```bash
python -m venv .venv
source .venv/Scripts/activate
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure environment variables

The app uses `OPENROUTER_API_KEY` for AI-powered music intent generation. Set this in your shell before running the server.

PowerShell:
```powershell
$env:OPENROUTER_API_KEY = "your_api_key_here"
```

Bash:
```bash
export OPENROUTER_API_KEY="your_api_key_here"
```

If `OPENROUTER_API_KEY` is not set, the API will use a fallback static melody for local testing.

## Running the server

From the repo root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn music_api:app --reload --host 127.0.0.1 --port 8000
```

Then open:

- `http://127.0.0.1:8000/docs` for FastAPI Swagger UI

## VS Code Launch Configuration

A `.vscode/launch.json` file exists that launches the server via `uvicorn`.

In VS Code, open the Run and Debug panel and select `Music API Server`.

## FL Studio MIDI setup

This project sends notes to FL Studio using a loopMIDI port named `FL Agent`.

1. Start loopMIDI and create a port named `FL Agent`.
2. In FL Studio MIDI settings, enable the `FL Agent` port and set the controller type to `FL Agent Controller`.
3. The app uses MIDI note `72` (C5) to start recording and `74` (D5) to stop recording. These are reserved and are not emitted as playable notes.

## Notes

- The API accepts natural language prompts and converts them to structured music intent.
- Melodies are generated in 4/4 time and use bar-based durations.
- Chords are supported by passing multiple note strings in the same `melody_pattern` entry.

## Useful commands

```powershell
# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Run server
python -m uvicorn music_api:app --reload --host 127.0.0.1 --port 8000
```

## Files

- `music_api.py` - FastAPI application and prompt handling
- `fl_transport.py` - MIDI transport to FL Studio
- `requirements.txt` - Python dependencies
- `.vscode/launch.json` - VS Code launch configuration
- `RUN_SERVER.ps1` - example server command
