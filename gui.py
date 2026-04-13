"""
gui.py — FL Agent Chat Window

Tkinter-based chat interface that:
  • Streams agent responses from OpenRouter via MCP tools
  • Intercepts play_melody_in_fl_studio / play_song calls to show a piano-roll
    preview before anything is sent to FL Studio
  • Lets the user preview locally, confirm (send to FL Studio), or discard
  • Optionally records voice and transcribes via OpenAI Whisper

Usage:
  python gui.py
  python gui.py --model anthropic/claude-opus-4-5
"""

import asyncio
import json
import os
import queue
import sys
import threading
import wave
from pathlib import Path
from typing import Any
import tkinter as tk
from tkinter import ttk, messagebox

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from midi_scheduler import MidiEvent, build_events, build_song, preview_song

# ── paths ──────────────────────────────────────────────────────────────────────

_ROOT          = Path(__file__).parent
AGENTS_MD_PATH = _ROOT / "AGENTS.md"
SERVER_SCRIPT  = str(_ROOT / "fl_mcp_server.py")
VENV_PYTHON    = str(_ROOT / ".venv" / "Scripts" / "python.exe")
SYSTEM_PROMPT  = AGENTS_MD_PATH.read_text(encoding="utf-8")

# ── colours ────────────────────────────────────────────────────────────────────

BG       = "#1e1e2e"
BG2      = "#181825"
FG       = "#cdd6f4"
FG_DIM   = "#6c7086"
BLUE     = "#89b4fa"
GREEN    = "#a6e3a1"
YELLOW   = "#f9e2af"
RED      = "#f38ba8"
SURFACE  = "#313244"
OVERLAY  = "#45475a"

LAYER_COLORS = {
    "melody":  "#4A9EFF",
    "chords":  "#6BCB77",
    "bass":    "#FF6B6B",
    "default": "#FFD93D",
}

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _note_name(midi: int) -> str:
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


# ── Piano Roll Canvas ─────────────────────────────────────────────────────────

class PianoRollCanvas(tk.Canvas):
    """Minimal but readable piano-roll view of a list of MidiEvent objects."""

    PIANO_W  = 44
    HEADER_H = 18

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=BG2, highlightthickness=0, **kwargs)
        self._layers: dict[str, list[MidiEvent]] = {}
        self._tempo = 120
        self.bind("<Configure>", lambda _: self._draw())

    def set_data(self, events_by_layer: dict[str, list[MidiEvent]], tempo: int = 120) -> None:
        self._layers = events_by_layer
        self._tempo  = tempo
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        if not self._layers:
            return

        on_events = [ev for evs in self._layers.values() for ev in evs if ev.type == "note_on"]
        if not on_events:
            return

        W = self.winfo_width()  or 600
        H = self.winfo_height() or 180
        RW = W - self.PIANO_W
        RH = H - self.HEADER_H

        note_lo = max(0,   min(e.note for e in on_events) - 2)
        note_hi = min(127, max(e.note for e in on_events) + 2)
        n_range = max(1, note_hi - note_lo)

        all_off = [ev.time for evs in self._layers.values() for ev in evs if ev.type == "note_off"]
        max_t   = max(all_off) if all_off else 4.0

        bar_s    = 60.0 / self._tempo * 4
        num_bars = max(1, int(max_t / bar_s) + 1)

        # ── bar grid ──────────────────────────────────────────────────────────
        for b in range(num_bars + 1):
            x = self.PIANO_W + b / num_bars * RW
            self.create_line(x, self.HEADER_H, x, H, fill="#33334d")
            if b < num_bars:
                self.create_text(x + 3, self.HEADER_H // 2, text=str(b + 1),
                                 fill=FG_DIM, font=("Courier", 7), anchor="w")

        # ── horizontal note lines ─────────────────────────────────────────────
        for n in range(note_lo, note_hi + 1):
            y = self.HEADER_H + (1 - (n - note_lo) / n_range) * RH
            color = "#2a2a3e" if (n % 12) in {1, 3, 6, 8, 10} else "#2d2d42"
            self.create_rectangle(self.PIANO_W, y - RH / n_range / 2,
                                  W, y + RH / n_range / 2, fill=color, outline="")
            if n % 12 == 0:
                self.create_line(self.PIANO_W, y, W, y, fill="#444466", dash=(2, 4))

        # ── piano keyboard ────────────────────────────────────────────────────
        black = {1, 3, 6, 8, 10}
        for n in range(note_lo, note_hi + 1):
            y_top = self.HEADER_H + (1 - (n - note_lo + 1) / n_range) * RH
            y_bot = self.HEADER_H + (1 - (n - note_lo)     / n_range) * RH
            fill  = "#222233" if (n % 12) in black else "#d8d8e8"
            self.create_rectangle(1, y_top, self.PIANO_W - 2, y_bot, fill=fill, outline="#444455")
            if n % 12 == 0:
                self.create_text(self.PIANO_W - 3, (y_top + y_bot) / 2,
                                 text=_note_name(n), fill=FG_DIM,
                                 font=("Courier", 6), anchor="e")

        # ── note rectangles ───────────────────────────────────────────────────
        for layer, evs in self._layers.items():
            color = LAYER_COLORS.get(layer, LAYER_COLORS["default"])
            active: dict[int, float] = {}
            for ev in sorted(evs, key=lambda e: e.time):
                if ev.type == "note_on":
                    active[ev.note] = ev.time
                elif ev.type == "note_off" and ev.note in active:
                    t0  = active.pop(ev.note)
                    x1  = self.PIANO_W + t0       / max_t * RW
                    x2  = self.PIANO_W + ev.time  / max_t * RW
                    y_t = self.HEADER_H + (1 - (ev.note - note_lo + 1) / n_range) * RH
                    y_b = self.HEADER_H + (1 - (ev.note - note_lo)     / n_range) * RH
                    x2  = max(x2, x1 + 3)
                    self.create_rectangle(x1 + 1, y_t + 1, x2 - 1, y_b - 1,
                                          fill=color, outline="")
                    self.create_rectangle(x1 + 1, y_t + 1, x2 - 1, y_b - 1,
                                          fill="", outline=color)


# ── Main Window ───────────────────────────────────────────────────────────────

class FLAgentGUI:

    def __init__(self, model: str = "openai/gpt-4o") -> None:
        self.model = model

        # thread-safe queues
        self._ui_q:    queue.Queue[dict] = queue.Queue()
        self._input_q: queue.Queue[str]  = queue.Queue()

        # confirmation state (agent thread waits; main thread signals)
        self._confirm_evt    = threading.Event()
        self._confirm_result: list[str | None] = [None]
        self._pending_intent: dict | None       = None

        self._in_stream = False    # are we currently rendering a streaming agent reply?
        self._recording = False

        self._build_ui()
        self._start_agent_thread()
        self.root.after(40, self._poll)

    # ── UI layout ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.root = tk.Tk()
        self.root.title("FL Agent")
        self.root.configure(bg=BG)
        self.root.geometry("960x720")
        self.root.minsize(700, 500)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        style = ttk.Style()
        style.theme_use("clam")
        for name, bg, fg in [
            ("TFrame",           BG,      FG),
            ("TLabel",           BG,      FG),
            ("TLabelframe",      BG,      FG),
            ("TLabelframe.Label",BG,      BLUE),
        ]:
            style.configure(name, background=bg, foreground=fg, borderwidth=0)
        style.configure("TButton",
                        background=SURFACE, foreground=FG, borderwidth=0,
                        focusthickness=0, padding=6)
        style.map("TButton",
                  background=[("active", OVERLAY), ("pressed", "#585b70")])
        style.configure("Accent.TButton", background=BLUE,  foreground=BG)
        style.map("Accent.TButton",  background=[("active", "#74c7ec")])
        style.configure("Danger.TButton",  background=RED,   foreground=BG)
        style.map("Danger.TButton",  background=[("active", "#eba0ac")])
        style.configure("TEntry",    fieldbackground=SURFACE, foreground=FG,
                        insertcolor=FG, borderwidth=0)
        style.configure("Vertical.TScrollbar",
                        background=SURFACE, troughcolor=BG2, borderwidth=0,
                        arrowcolor=FG_DIM)

        # ── paned: chat + preview ──────────────────────────────────────────
        self.pane = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        self.pane.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        # chat frame
        chat_f = ttk.Frame(self.pane)
        self.pane.add(chat_f, weight=3)

        self.chat = tk.Text(
            chat_f, wrap=tk.WORD, state=tk.DISABLED,
            bg=BG2, fg=FG, relief=tk.FLAT, padx=10, pady=8,
            font=("Consolas", 10), insertbackground=FG,
            selectbackground=OVERLAY,
        )
        sb = ttk.Scrollbar(chat_f, orient=tk.VERTICAL, command=self.chat.yview)
        self.chat.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat.pack(fill=tk.BOTH, expand=True)

        self.chat.tag_configure("user",   foreground=BLUE,   font=("Consolas", 10, "bold"))
        self.chat.tag_configure("agent",  foreground=GREEN)
        self.chat.tag_configure("tool",   foreground=YELLOW, font=("Consolas", 9))
        self.chat.tag_configure("result", foreground=FG_DIM, font=("Consolas", 9))
        self.chat.tag_configure("system", foreground=FG_DIM, font=("Consolas", 9, "italic"))
        self.chat.tag_configure("error",  foreground=RED)

        # preview frame (hidden until a melody arrives)
        self.preview_f = ttk.LabelFrame(self.pane, text=" MIDI Preview ", padding=6)

        self.piano_roll = PianoRollCanvas(self.preview_f, height=160)
        self.piano_roll.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        btn_row = ttk.Frame(self.preview_f)
        btn_row.pack(fill=tk.X)

        self.info_lbl = ttk.Label(btn_row, text="", foreground=FG_DIM,
                                  font=("Consolas", 9))
        self.info_lbl.pack(side=tk.LEFT, padx=4)

        ttk.Button(btn_row, text="▶  Preview locally",
                   command=self._on_preview).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_row, text="✓  Send to FL Studio",
                   style="Accent.TButton",
                   command=self._on_confirm).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_row, text="✗  Discard",
                   style="Danger.TButton",
                   command=self._on_discard).pack(side=tk.RIGHT, padx=2)

        # ── input row ─────────────────────────────────────────────────────
        input_f = ttk.Frame(self.root)
        input_f.pack(fill=tk.X, padx=8, pady=6)

        self.input_var = tk.StringVar()
        entry = ttk.Entry(input_f, textvariable=self.input_var, font=("Consolas", 11))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        entry.bind("<Return>", self._on_send)
        entry.focus()

        ttk.Button(input_f, text="Send", style="Accent.TButton",
                   command=self._on_send).pack(side=tk.LEFT, padx=(0, 4))

        self._setup_record_btn(input_f)

        # ── status bar ────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Connecting to MCP server…")
        ttk.Label(self.root, textvariable=self.status_var,
                  foreground=FG_DIM, font=("Consolas", 8)
                  ).pack(fill=tk.X, padx=10, pady=(0, 4))

    def _setup_record_btn(self, parent: ttk.Frame) -> None:
        try:
            import sounddevice  # noqa: F401
            self._rec_btn = ttk.Button(parent, text="🎤  Record",
                                       command=self._toggle_record)
            self._rec_btn.pack(side=tk.LEFT)
        except ImportError:
            pass  # sounddevice not installed

    # ── chat helpers ──────────────────────────────────────────────────────────

    def _write(self, text: str, tag: str = "", newline: bool = True) -> None:
        self.chat.configure(state=tk.NORMAL)
        self.chat.insert(tk.END, (text + "\n") if newline else text, tag or "")
        self.chat.configure(state=tk.DISABLED)
        self.chat.see(tk.END)

    # ── button handlers ───────────────────────────────────────────────────────

    def _on_send(self, _event=None) -> None:
        text = self.input_var.get().strip()
        if not text:
            return
        self.input_var.set("")
        self._write(f"You: {text}", "user")
        self._input_q.put(text)

    def _on_preview(self) -> None:
        if self._pending_intent is None:
            return
        threading.Thread(
            target=preview_song, args=(self._pending_intent,), daemon=True
        ).start()

    def _on_confirm(self) -> None:
        self._confirm_result[0] = "confirmed"
        self._confirm_evt.set()
        self._hide_preview()

    def _on_discard(self) -> None:
        self._confirm_result[0] = "discarded"
        self._confirm_evt.set()
        self._hide_preview()

    def _on_close(self) -> None:
        if not self._confirm_evt.is_set():
            self._confirm_result[0] = "discarded"
            self._confirm_evt.set()
        self.root.destroy()

    # ── preview panel ─────────────────────────────────────────────────────────

    def _show_preview(self, intent: dict, events_by_layer: dict[str, list[MidiEvent]]) -> None:
        self._pending_intent = intent
        tempo  = intent.get("tempo", 120)
        layers = intent.get("layers") or {"melody": intent.get("melody_pattern", [])}

        self.piano_roll.set_data(events_by_layer, tempo)
        n_total = sum(len(v) for v in layers.values())
        self.info_lbl.configure(
            text=f"Tempo: {tempo} BPM  •  Layers: {', '.join(layers)}  •  {n_total} notes"
        )

        if not self.preview_f.winfo_ismapped():
            self.pane.add(self.preview_f, weight=2)

        self._confirm_evt.clear()
        self._confirm_result[0] = None

    def _hide_preview(self) -> None:
        if self.preview_f.winfo_ismapped():
            self.pane.forget(self.preview_f)
        self._pending_intent = None

    # ── UI queue poll (main thread) ───────────────────────────────────────────

    def _poll(self) -> None:
        try:
            while True:
                self._dispatch(self._ui_q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _dispatch(self, msg: dict) -> None:
        t = msg["type"]
        if t == "status":
            self.status_var.set(msg["text"])
        elif t == "chat":
            self._write(msg["text"], msg.get("tag", ""), msg.get("newline", True))
        elif t == "stream_start":
            self._write("Agent: ", "agent", newline=False)
            self._in_stream = True
        elif t == "stream_chunk":
            if self._in_stream:
                self._write(msg["chunk"], "agent", newline=False)
        elif t == "stream_end":
            if self._in_stream:
                self._write("", newline=True)
                self._in_stream = False
        elif t == "preview":
            self._show_preview(msg["intent"], msg["events"])
        elif t == "error":
            self._write(f"ERROR: {msg['text']}", "error")

    # ── voice recording ───────────────────────────────────────────────────────

    def _toggle_record(self) -> None:
        if self._recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self) -> None:
        import sounddevice as sd
        self._recording   = True
        self._rec_frames  = []
        self._rec_rate    = 44100
        self._rec_btn.configure(text="⏹  Stop")

        def cb(indata, *_):
            self._rec_frames.append(indata.copy())

        self._rec_stream = sd.InputStream(
            samplerate=self._rec_rate, channels=1, dtype="int16", callback=cb
        )
        self._rec_stream.start()

    def _stop_record(self) -> None:
        import tempfile
        import numpy as np

        self._recording = False
        self._rec_btn.configure(text="🎤  Record")
        self._rec_stream.stop()
        self._rec_stream.close()

        if not self._rec_frames:
            return

        audio = np.concatenate(self._rec_frames, axis=0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._rec_rate)
            wf.writeframes(audio.tobytes())

        threading.Thread(target=self._transcribe, args=(tmp,), daemon=True).start()

    def _transcribe(self, path: str) -> None:
        import os as _os
        try:
            from openai import OpenAI
            key = _os.environ.get("OPENAI_API_KEY")
            if not key:
                self._ui_q.put({"type": "error",
                                "text": "OPENAI_API_KEY required for voice transcription."})
                return
            client = OpenAI(api_key=key)
            with open(path, "rb") as f:
                tx = client.audio.transcriptions.create(model="whisper-1", file=f)
            text = tx.text.strip()
            if text:
                self._input_q.put(text)
                self._ui_q.put({"type": "chat", "text": f"You (voice): {text}", "tag": "user"})
        except Exception as exc:
            self._ui_q.put({"type": "error", "text": f"Transcription: {exc}"})
        finally:
            try:
                _os.unlink(path)
            except Exception:
                pass

    # ── agent thread ──────────────────────────────────────────────────────────

    def _start_agent_thread(self) -> None:
        threading.Thread(target=self._agent_thread_main, daemon=True).start()

    def _agent_thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._agent_async())
        except Exception as exc:
            self._ui_q.put({"type": "error", "text": str(exc)})
        finally:
            loop.close()

    async def _agent_async(self) -> None:
        python = VENV_PYTHON if Path(VENV_PYTHON).exists() else sys.executable
        params = StdioServerParameters(command=python, args=[SERVER_SCRIPT])

        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                mcp_tools    = (await session.list_tools()).tools
                openai_tools = _tools_to_openai(mcp_tools)
                llm          = _openrouter_client()

                self._ui_q.put({"type": "status",
                                "text": f"Ready — {len(mcp_tools)} tools • model: {self.model}"})
                self._ui_q.put({"type": "chat",
                                "text": f"FL Agent [{self.model}]  —  type a music request.",
                                "tag": "system"})

                messages = [{"role": "system", "content": SYSTEM_PROMPT}]

                while True:
                    user = await asyncio.get_event_loop().run_in_executor(
                        None, self._input_q.get
                    )
                    messages.append({"role": "user", "content": user})

                    # agentic loop
                    while True:
                        try:
                            asst, finish = await asyncio.get_event_loop().run_in_executor(
                                None, _stream, llm, self.model, messages,
                                openai_tools, self._ui_q
                            )
                            messages.append(asst)

                            calls = asst.get("tool_calls") or []
                            if finish == "tool_calls" and calls:
                                for tc in calls:
                                    fn   = tc["function"]["name"]
                                    args = json.loads(tc["function"]["arguments"])
                                    self._ui_q.put({
                                        "type": "chat",
                                        "text": f"  → {fn}({json.dumps(args)[:140]})",
                                        "tag": "tool",
                                    })

                                    if fn in ("play_melody_in_fl_studio", "play_song"):
                                        result_text = await self._intercept_play(fn, args, session)
                                    else:
                                        res = await session.call_tool(fn, args)
                                        result_text = "\n".join(
                                            c.text for c in res.content if hasattr(c, "text")
                                        )

                                    self._ui_q.put({
                                        "type": "chat",
                                        "text": f"  ← {result_text[:220]}",
                                        "tag": "result",
                                    })
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": tc["id"],
                                        "content": result_text,
                                    })
                            else:
                                break
                        except Exception as exc:
                            self._ui_q.put({"type": "error", "text": str(exc)})
                            break

    async def _intercept_play(
        self, fn: str, args: dict, session: ClientSession
    ) -> str:
        """Show piano roll and wait for user to confirm or discard before sending."""
        tempo  = args.get("tempo", 120)
        layers = args.get("layers") or {"melody": args.get("melody_pattern", [])}

        events_by_layer = {
            name: build_events(notes, tempo)
            for name, notes in layers.items()
        }

        self._ui_q.put({
            "type":   "preview",
            "intent": {"tempo": tempo, "layers": layers},
            "events": events_by_layer,
        })
        self._ui_q.put({
            "type": "chat",
            "text": "  ⏸  Review the piano roll above — confirm or discard.",
            "tag":  "system",
        })

        # block agent thread until user responds (5-minute timeout)
        confirmed = await asyncio.get_event_loop().run_in_executor(
            None, self._confirm_evt.wait, 300
        )

        if not confirmed or self._confirm_result[0] != "confirmed":
            return "Discarded — MIDI not sent to FL Studio."

        res = await session.call_tool(fn, args)
        return "\n".join(c.text for c in res.content if hasattr(c, "text"))

    # ── entry point ───────────────────────────────────────────────────────────

    def run(self) -> None:
        self.root.mainloop()


# ── standalone helpers ────────────────────────────────────────────────────────

def _openrouter_client():
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


def _tools_to_openai(tools) -> list:
    return [
        {"type": "function", "function": {
            "name": t.name,
            "description": t.description or "",
            "parameters": t.inputSchema,
        }}
        for t in tools
    ]


def _stream(
    llm, model: str, messages: list, tools: list, ui_q: queue.Queue
) -> tuple[dict[str, Any], str | None]:
    """Blocking streaming response — runs in executor thread."""
    parts:  list[str]             = []
    ptc:    dict[int, dict]       = {}
    finish: str | None            = None
    started = False

    s = llm.chat.completions.create(
        model=model, messages=messages, tools=tools,
        tool_choice="auto", stream=True,
    )
    try:
        for chunk in s:
            if not chunk.choices:
                continue
            ch = chunk.choices[0]
            if ch.finish_reason:
                finish = ch.finish_reason
            d = ch.delta
            if not d:
                continue
            if d.content:
                if not started:
                    ui_q.put({"type": "stream_start"})
                    started = True
                ui_q.put({"type": "stream_chunk", "chunk": d.content})
                parts.append(d.content)
            for tc in d.tool_calls or []:
                p = ptc.setdefault(tc.index, {
                    "id": None, "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                if tc.id:            p["id"]   = tc.id
                if tc.type:          p["type"] = tc.type
                if tc.function:
                    if tc.function.name:      p["function"]["name"]      += tc.function.name
                    if tc.function.arguments: p["function"]["arguments"] += tc.function.arguments
    finally:
        if started:
            ui_q.put({"type": "stream_end"})
        if hasattr(s, "close"):
            s.close()

    tc_list = [
        {
            "id":   v["id"] or f"call_{i}",
            "type": v["type"] or "function",
            "function": {
                "name":      v["function"]["name"],
                "arguments": v["function"]["arguments"] or "{}",
            },
        }
        for i, v in sorted(ptc.items())
    ]
    asst: dict[str, Any] = {"role": "assistant", "content": "".join(parts) or None}
    if tc_list:
        asst["tool_calls"] = tc_list
    return asst, finish


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="FL Agent GUI")
    p.add_argument("--model", default=os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o"))
    a = p.parse_args()
    FLAgentGUI(model=a.model).run()
