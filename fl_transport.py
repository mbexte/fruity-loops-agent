"""
fl_transport.py — FL Studio MIDI Transport & Protocol Client
============================================================

Implements the client side of the FL MIDI Protocol, providing the
FLTransport class for structured communication with FL Studio and
module-level convenience functions used by the MCP Server.

Architecture
────────────

  ┌──────────────────────────────────────────────────────┐
  │            MCP Server  (fl_mcp_server.py)            │
  │  play_song · start_recording · set_tempo · …         │
  └───────────────────────┬──────────────────────────────┘
                          │ calls module-level helpers
  ┌───────────────────────▼──────────────────────────────┐
  │            FLTransport  (this module)                │
  │                                                      │
  │  Tier-1  Note-based transport                        │
  │    note_on 72 → START REC                            │
  │    note_on 73 → PAUSE / RESUME                       │
  │    note_on 74 → STOP  REC                            │
  │    note_on 75 → REWIND                               │
  │    note_on 76 → LOOP TOGGLE                          │
  │    note_on 77 → METRONOME TOGGLE                     │
  │                                                      │
  │  Tier-2  CC-based parameters                         │
  │    CC 1  → TEMPO  (7-bit, 60–187 BPM)                │
  │    CC 7  → MASTER VOLUME                             │
  │    CC 20 → PATTERN SELECT                            │
  │    CC 23 → LOOP ENABLE                               │
  │                                                      │
  │  Tier-3  SysEx structured commands                   │
  │    0x01  PING          0x05  SET_TEMPO               │
  │    0x02  SET_PAT_NAME  0x06  CHANNEL_MUTE            │
  │    0x03  PAT_INFO_REQ  0x07  CHANNEL_SOLO            │
  │    0x04  QUANTIZE      0x10  NOTE_EVENT              │
  │    0x11  PAT_CLEAR     0x12  PAT_SELECT              │
  │    0x13  REQ_STATUS                                  │
  │                                                      │
  │  High-level  play_song / play_preview                │
  │    → midi_scheduler (absolute-time MIDI dispatch)    │
  └───────────────────────┬──────────────────────────────┘
                          │ loopMIDI virtual port "FL Agent"
  ┌───────────────────────▼──────────────────────────────┐
  │    FL Studio + device_FL_Agent_Controller.py         │
  └──────────────────────────────────────────────────────┘

Protocol constants mirror device_FL_Agent_Controller.py exactly so either
file can serve as the canonical reference.

Usage (direct):
  transport = FLTransport()
  with transport:
      transport.set_tempo(140)
      transport.select_pattern(2)
      transport.set_pattern_name(2, "Verse")
      transport.start_recording()
      transport.play_song(intent)
      transport.stop_recording()

Usage (MCP Server):
  # Module-level wrappers open a port, send, and close automatically.
  start_recording()
  play_song(intent)
  stop_recording()

CLI:
  python fl_transport.py start
  python fl_transport.py stop
  python fl_transport.py ping
  python fl_transport.py status
"""

import sys
import threading

import mido

from midi_scheduler import (
    DEFAULT_VELOCITY,
    NOTE_START,
    NOTE_STOP,
    _get_port,
    play_song as _sched_play_song,
    preview_song,
)

# ── Protocol constants (mirrors device_FL_Agent_Controller.py) ────────────────

SYSEX_MANF_ID = 0x7D          # Non-commercial / educational manufacturer ID

# Tier-1 Transport Notes
NOTE_START_REC  = 72   # C5  — arm + start recording
NOTE_PAUSE      = 73   # C#5 — pause / resume
NOTE_STOP_REC   = 74   # D5  — stop recording
NOTE_REWIND     = 75   # D#5 — rewind to bar 1
NOTE_LOOP       = 76   # E5  — toggle loop mode
NOTE_METRONOME  = 77   # F5  — toggle metronome

# Tier-2 CC controls
CC_TEMPO        = 1
CC_MASTER_VOL   = 7
CC_PATTERN_LEN  = 11
CC_PATTERN_SEL  = 20
CC_TIMESIG_NUM  = 21
CC_TIMESIG_DEN  = 22
CC_LOOP         = 23
CC_METRONOME_CC = 24

# Tier-3 SysEx commands (host → device)
CMD_PING              = 0x01
CMD_SET_PATTERN_NAME  = 0x02
CMD_PATTERN_INFO_REQ  = 0x03
CMD_QUANTIZE          = 0x04
CMD_SET_TEMPO         = 0x05
CMD_CHANNEL_MUTE      = 0x06
CMD_CHANNEL_SOLO      = 0x07
CMD_NOTE_EVENT        = 0x10
CMD_PATTERN_CLEAR     = 0x11
CMD_PATTERN_SELECT    = 0x12
CMD_REQUEST_STATUS    = 0x13

# Tier-3 SysEx responses (device → host)
RSP_ACK    = 0x80
RSP_NACK   = 0x81
RSP_PONG   = 0x82
RSP_STATUS = 0x83

# Quantize grid constants
QUANTIZE_1_4  = 0
QUANTIZE_1_8  = 1
QUANTIZE_1_16 = 2
QUANTIZE_1_32 = 3

__all__ = [
    # Class
    "FLTransport",
    # Module-level MCP helpers
    "start_recording",
    "stop_recording",
    "play_melody",
    "play_song",
    "preview_song",
    "set_tempo",
    "select_pattern",
    "set_pattern_name",
    "quantize",
    "channel_mute",
    "channel_solo",
    "request_status",
    "ping",
    # Constants
    "QUANTIZE_1_4",
    "QUANTIZE_1_8",
    "QUANTIZE_1_16",
    "QUANTIZE_1_32",
]


# ══════════════════════════════════════════════════════════════════════════════
# FLTransport — Full protocol client
# ══════════════════════════════════════════════════════════════════════════════

class FLTransport:
    """
    Full FL MIDI Protocol client over a loopMIDI output port.

    Manages one persistent output port and exposes all three protocol
    tiers as typed Python methods.  Thread-safe: a lock serialises
    concurrent sends from multiple threads (e.g. MCP + GUI).

    Parameters
    ──────────
    port_name : str
        Substring of the loopMIDI port to open (default: "FL Agent").
    velocity  : int
        Default Note-On velocity for Tier-1 transport notes (1–127).
    """

    def __init__(self, port_name: str = "FL Agent", velocity: int = 100):
        self.port_name  = port_name
        self.velocity   = velocity
        self._port      = None
        self._lock      = threading.Lock()

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        self.close()

    def open(self) -> "FLTransport":
        """Open the MIDI output port.  Returns self for chaining."""
        available = mido.get_output_names()
        match = next((n for n in available if self.port_name in n), None)
        if match is None:
            raise RuntimeError(
                f"MIDI port '{self.port_name}' not found.\n"
                f"Available ports: {available or ['(none)']}\n"
                "Start loopMIDI and create a port named 'FL Agent'."
            )
        self._port = mido.open_output(match)
        return self

    def close(self) -> None:
        """Close the MIDI output port."""
        if self._port:
            self._port.close()
            self._port = None

    def _send(self, msg: mido.Message) -> None:
        with self._lock:
            if self._port is None:
                raise RuntimeError(
                    "FLTransport port is not open. Call open() or use 'with FLTransport() as t:'"
                )
            self._port.send(msg)

    # ══════════════════════════════════════════════════════════════════════════
    # TIER 1 — Note-based transport control
    # ══════════════════════════════════════════════════════════════════════════

    def start_recording(self) -> None:
        """Note 72 (C5) → FL Studio arms record and starts transport."""
        self._send(mido.Message("note_on",  note=NOTE_START_REC, velocity=self.velocity))
        self._send(mido.Message("note_off", note=NOTE_START_REC, velocity=0))
        print("[FLTransport] Recording STARTED")

    def stop_recording(self) -> None:
        """Note 74 (D5) → FL Studio stops transport."""
        self._send(mido.Message("note_on",  note=NOTE_STOP_REC, velocity=self.velocity))
        self._send(mido.Message("note_off", note=NOTE_STOP_REC, velocity=0))
        print("[FLTransport] Recording STOPPED")

    def pause_resume(self) -> None:
        """Note 73 (C#5) → pause if playing, resume if stopped."""
        self._send(mido.Message("note_on", note=NOTE_PAUSE, velocity=self.velocity))
        print("[FLTransport] Pause/Resume toggled")

    def rewind(self) -> None:
        """Note 75 (D#5) → return playhead to bar 1."""
        self._send(mido.Message("note_on", note=NOTE_REWIND, velocity=self.velocity))
        print("[FLTransport] Rewound to bar 1")

    def toggle_loop(self) -> None:
        """Note 76 (E5) → toggle loop mode on/off."""
        self._send(mido.Message("note_on", note=NOTE_LOOP, velocity=self.velocity))
        print("[FLTransport] Loop mode toggled")

    def toggle_metronome(self) -> None:
        """Note 77 (F5) → toggle the FL Studio metronome."""
        self._send(mido.Message("note_on", note=NOTE_METRONOME, velocity=self.velocity))
        print("[FLTransport] Metronome toggled")

    # ══════════════════════════════════════════════════════════════════════════
    # TIER 2 — CC-based parameters
    # ══════════════════════════════════════════════════════════════════════════

    def set_tempo_cc(self, bpm: int) -> None:
        """CC 1 (Mod Wheel): maps BPM 60–187 → CC value 0–127."""
        val = max(0, min(127, bpm - 60))
        self._send(mido.Message("control_change", control=CC_TEMPO, value=val))
        print(f"[FLTransport] Tempo (CC) -> {bpm} BPM")

    def select_pattern_cc(self, slot: int) -> None:
        """CC 20: select pattern slot 0–127."""
        val = max(0, min(127, slot))
        self._send(mido.Message("control_change", control=CC_PATTERN_SEL, value=val))
        print(f"[FLTransport] Pattern (CC) -> slot {slot + 1}")

    def set_loop_cc(self, enable: bool) -> None:
        """CC 23: enable (127) or disable (0) loop mode."""
        self._send(mido.Message("control_change", control=CC_LOOP,
                                value=127 if enable else 0))
        print(f"[FLTransport] Loop (CC) -> {'ON' if enable else 'OFF'}")

    def set_master_volume(self, level: float) -> None:
        """CC 7: set master volume.  level is 0.0–1.0."""
        val = max(0, min(127, int(level * 127)))
        self._send(mido.Message("control_change", control=CC_MASTER_VOL, value=val))
        print(f"[FLTransport] Master volume -> {level:.0%}")

    # ══════════════════════════════════════════════════════════════════════════
    # TIER 3 — SysEx structured commands
    # ══════════════════════════════════════════════════════════════════════════

    def _sysex(self, payload: list) -> None:
        """Send F0 7D [payload...] F7."""
        data = tuple([SYSEX_MANF_ID] + [int(b) for b in payload])
        self._send(mido.Message("sysex", data=data))

    def ping(self) -> None:
        """SysEx 0x01 PING — device replies with PONG (0x82)."""
        self._sysex([CMD_PING])
        print("[FLTransport] PING sent")

    def set_tempo(self, bpm: int) -> None:
        """SysEx 0x05 SET_TEMPO — 14-bit BPM (40–999).

        Encodes BPM as two 7-bit bytes: [bpm >> 7, bpm & 0x7F].
        This covers the full 40–999 BPM range without reserved-byte issues.
        """
        bpm = max(40, min(999, int(bpm)))
        msb7 = (bpm >> 7) & 0x7F
        lsb7 = bpm & 0x7F
        self._sysex([CMD_SET_TEMPO, msb7, lsb7])
        print(f"[FLTransport] Tempo (SysEx) -> {bpm} BPM")

    def select_pattern(self, slot: int) -> None:
        """SysEx 0x12 PATTERN_SELECT — choose active pattern slot (0-based)."""
        self._sysex([CMD_PATTERN_SELECT, slot & 0x7F])
        print(f"[FLTransport] Pattern (SysEx) -> slot {slot + 1}")

    def set_pattern_name(self, slot: int, name: str) -> None:
        """SysEx 0x02 SET_PATTERN_NAME — assign ASCII name (max 32 chars)."""
        encoded = [ord(c) for c in name[:32]]
        self._sysex([CMD_SET_PATTERN_NAME, slot & 0x7F, len(encoded)] + encoded)
        print(f"[FLTransport] Pattern {slot + 1} name -> '{name}'")

    def pattern_info(self, slot: int) -> None:
        """SysEx 0x03 PATTERN_INFO_REQ — request name/info for a pattern slot."""
        self._sysex([CMD_PATTERN_INFO_REQ, slot & 0x7F])
        print(f"[FLTransport] Pattern info requested for slot {slot + 1}")

    def quantize(self, grid: int = QUANTIZE_1_16) -> None:
        """SysEx 0x04 QUANTIZE — snap current pattern to a rhythmic grid.

        grid: QUANTIZE_1_4=0  QUANTIZE_1_8=1  QUANTIZE_1_16=2  QUANTIZE_1_32=3
        """
        grid_labels = {0: "1/4", 1: "1/8", 2: "1/16", 3: "1/32"}
        self._sysex([CMD_QUANTIZE, grid & 0x03])
        print(f"[FLTransport] Quantize -> {grid_labels.get(grid, '?')}")

    def pattern_clear(self, slot: int) -> None:
        """SysEx 0x11 PATTERN_CLEAR — erase all notes from a pattern slot."""
        self._sysex([CMD_PATTERN_CLEAR, slot & 0x7F])
        print(f"[FLTransport] Pattern {slot + 1} cleared")

    def channel_mute(self, channel: int, mute: bool) -> None:
        """SysEx 0x06 CHANNEL_MUTE — mute or unmute a mixer channel."""
        self._sysex([CMD_CHANNEL_MUTE, channel & 0x7F, 1 if mute else 0])
        print(f"[FLTransport] Channel {channel} mute -> {'ON' if mute else 'OFF'}")

    def channel_solo(self, channel: int, solo: bool) -> None:
        """SysEx 0x07 CHANNEL_SOLO — solo or unsolo a mixer channel."""
        self._sysex([CMD_CHANNEL_SOLO, channel & 0x7F, 1 if solo else 0])
        print(f"[FLTransport] Channel {channel} solo -> {'ON' if solo else 'OFF'}")

    def send_note_event(self, channel: int, note: int, velocity: int,
                        duration_ticks: int) -> None:
        """SysEx 0x10 NOTE_EVENT — insert a note into the current FL pattern.

        duration_ticks is encoded as two 7-bit bytes (14-bit total, 0–16383).
        """
        dur_msb7 = (duration_ticks >> 7) & 0x7F
        dur_lsb7 = duration_ticks & 0x7F
        self._sysex([CMD_NOTE_EVENT,
                     channel & 0x7F, note & 0x7F, velocity & 0x7F,
                     dur_msb7, dur_lsb7])

    def request_status(self) -> None:
        """SysEx 0x13 REQUEST_STATUS — ask device for full state snapshot.

        Device responds with STATUS (0x83):
          [state_byte] [pattern_slot] [bpm_msb7] [bpm_lsb7]
          state_byte bits: 0=playing  1=recording  2=loop  3=metronome
        """
        self._sysex([CMD_REQUEST_STATUS])
        print("[FLTransport] Status requested")

    # ══════════════════════════════════════════════════════════════════════════
    # High-level playback (delegates to midi_scheduler)
    # ══════════════════════════════════════════════════════════════════════════

    def play_song(self, intent: dict) -> None:
        """Play a multi-layer song through FL Studio (arms recording first).

        Closes this transport's port before delegating — the scheduler
        opens its own port internally.
        """
        _sched_play_song(intent, send_to_fl=True)

    def play_preview(self, intent: dict) -> None:
        """Play locally without arming FL Studio recording."""
        preview_song(intent)


# ══════════════════════════════════════════════════════════════════════════════
# Module-level convenience functions — used by MCP Server & backwards-compat
# ══════════════════════════════════════════════════════════════════════════════
#
# Each function opens a short-lived FLTransport, sends its message, and closes.
# This keeps the MCP Server stateless and avoids leaving ports open between calls.

def start_recording() -> None:
    """MCP-callable: arm and start FL Studio recording."""
    with FLTransport() as t:
        t.start_recording()


def stop_recording() -> None:
    """MCP-callable: stop FL Studio recording."""
    with FLTransport() as t:
        t.stop_recording()


def set_tempo(bpm: int) -> None:
    """MCP-callable: set FL Studio tempo via SysEx (14-bit, 40–999 BPM)."""
    with FLTransport() as t:
        t.set_tempo(bpm)


def select_pattern(slot: int) -> None:
    """MCP-callable: activate a pattern slot (0-based)."""
    with FLTransport() as t:
        t.select_pattern(slot)


def set_pattern_name(slot: int, name: str) -> None:
    """MCP-callable: assign a name to a pattern slot."""
    with FLTransport() as t:
        t.set_pattern_name(slot, name)


def quantize(grid: int = QUANTIZE_1_16) -> None:
    """MCP-callable: quantize current pattern (0=1/4 … 3=1/32)."""
    with FLTransport() as t:
        t.quantize(grid)


def channel_mute(channel: int, mute: bool) -> None:
    """MCP-callable: mute or unmute a mixer channel."""
    with FLTransport() as t:
        t.channel_mute(channel, mute)


def channel_solo(channel: int, solo: bool) -> None:
    """MCP-callable: solo or unsolo a mixer channel."""
    with FLTransport() as t:
        t.channel_solo(channel, solo)


def request_status() -> None:
    """MCP-callable: request full device status snapshot."""
    with FLTransport() as t:
        t.request_status()


def ping() -> None:
    """MCP-callable: send PING to device."""
    with FLTransport() as t:
        t.ping()


def play_melody(intent: dict) -> None:
    """MCP-callable: play a single-layer melody through FL Studio.

    intent keys: tempo (int BPM), melody_pattern (list of note dicts)
    """
    _sched_play_song(intent, send_to_fl=True)


def play_song(intent: dict) -> None:
    """MCP-callable: play a multi-layer song through FL Studio.

    intent keys: tempo (int BPM), layers (dict of layer_name → note list)
    """
    _sched_play_song(intent, send_to_fl=True)


# ══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> None:
    """Run a single FL Transport command.

    Accepts arguments either as separate tokens (terminal usage) or as a single
    space-separated string (VS Code launch.json pickString passes one arg):

      python fl_transport.py tempo 140      # terminal
      python fl_transport.py "tempo 140"    # VS Code pickString
    """
    if argv is None:
        argv = sys.argv[1:]

    # Flatten so "tempo 140" (one arg) and "tempo" "140" (two args) both work.
    parts = " ".join(argv).split()

    def usage() -> None:
        print("Usage: python fl_transport.py <command> [args]\n")
        print("Commands:")
        print("  ping                   Send PING — device replies with PONG")
        print("  status                 Request full state snapshot (STATUS)")
        print("  start                  Arm + start recording")
        print("  stop                   Stop recording / transport")
        print("  rewind                 Return playhead to bar 1")
        print("  loop                   Toggle loop mode on/off")
        print("  metronome              Toggle metronome on/off")
        print("  tempo <bpm>            Set tempo, e.g. 'tempo 140'  (40–999)")
        print("  pattern <slot>         Select pattern slot (0-based)")
        print("  name <slot> <name>     Set pattern name, e.g. 'name 0 Lead'")
        print("  mute <ch>              Mute mixer channel")
        print("  unmute <ch>            Unmute mixer channel")
        print("  solo <ch>              Solo mixer channel")
        print("  unsolo <ch>            Unsolo mixer channel")
        print("  quantize [grid]        Quantize (0=1/4 1=1/8 2=1/16 3=1/32)")
        print("  clear [slot]           Clear pattern slot (default: 0)")
        sys.exit(1)

    if not parts:
        usage()

    cmd = parts[0]

    with FLTransport() as t:
        if cmd == "ping":
            t.ping()
        elif cmd == "status":
            t.request_status()
        elif cmd == "start":
            t.start_recording()
        elif cmd == "stop":
            t.stop_recording()
        elif cmd == "rewind":
            t.rewind()
        elif cmd == "loop":
            t.toggle_loop()
        elif cmd == "metronome":
            t.toggle_metronome()
        elif cmd == "tempo":
            t.set_tempo(int(parts[1]) if len(parts) > 1 else 120)
        elif cmd == "pattern":
            t.select_pattern(int(parts[1]) if len(parts) > 1 else 0)
        elif cmd == "name":
            slot = int(parts[1]) if len(parts) > 1 else 0
            name = parts[2] if len(parts) > 2 else "Pattern"
            t.set_pattern_name(slot, name)
        elif cmd == "mute":
            t.channel_mute(int(parts[1]) if len(parts) > 1 else 0, True)
        elif cmd == "unmute":
            t.channel_mute(int(parts[1]) if len(parts) > 1 else 0, False)
        elif cmd == "solo":
            t.channel_solo(int(parts[1]) if len(parts) > 1 else 0, True)
        elif cmd == "unsolo":
            t.channel_solo(int(parts[1]) if len(parts) > 1 else 0, False)
        elif cmd == "quantize":
            t.quantize(int(parts[1]) if len(parts) > 1 else QUANTIZE_1_16)
        elif cmd == "clear":
            t.pattern_clear(int(parts[1]) if len(parts) > 1 else 0)
        else:
            print(f"Unknown command: {cmd!r}")
            usage()


if __name__ == "__main__":
    main()
