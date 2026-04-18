"""
Thread-safe audio recorder with WAV peak-normalization.

Decoupled from Tkinter so it is testable in isolation and reusable
from both the GUI and any future CLI/API entry points.
"""
from __future__ import annotations

import os
import tempfile
import threading
import wave
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

DEFAULT_SAMPLERATE = 44100
DEFAULT_CHANNELS   = 1
DEFAULT_DTYPE      = "int16"


class Recorder:
    """
    Manages one recording session: start → collect frames → stop → save WAV.

    Parameters
    ----------
    samplerate  : int
    on_complete : Callable[[str], None]
        Called synchronously inside stop() (on whichever thread calls stop).
        Receives the absolute path to the saved WAV file.
        The caller is responsible for dispatching any UI work to the main thread.
    """

    def __init__(
        self,
        samplerate: int = DEFAULT_SAMPLERATE,
        on_complete: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.samplerate  = samplerate
        self.on_complete = on_complete
        self._frames: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._lock   = threading.Lock()
        self._active = False

    @property
    def is_recording(self) -> bool:
        return self._active

    def start(self) -> None:
        """Open a sounddevice InputStream and begin collecting audio frames."""
        with self._lock:
            if self._active:
                return
            self._frames = []
            self._active = True

        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=DEFAULT_CHANNELS,
            dtype=DEFAULT_DTYPE,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self, out_path: Optional[str] = None) -> Optional[str]:
        """
        Stop recording, peak-normalize, and save to a WAV file.

        Parameters
        ----------
        out_path : str or None
            Destination path.  If None a temp file is created.

        Returns
        -------
        Absolute path to the saved WAV, or None if no audio was captured.
        """
        with self._lock:
            if not self._active:
                return None
            self._active = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._frames:
            return None

        path = _save_wav(self._frames, self.samplerate, out_path)
        if self.on_complete and path:
            self.on_complete(path)
        return path

    def _callback(self, indata: np.ndarray, *_) -> None:
        if self._active:
            self._frames.append(indata.copy())


# ── helpers ───────────────────────────────────────────────────────────────────

def _save_wav(
    frames: list[np.ndarray],
    samplerate: int,
    out_path: Optional[str] = None,
) -> str:
    """
    Concatenate frames, peak-normalize to int16, write WAV.

    Normalization to 90 % of int16 range prevents CREPE/pyin from receiving
    near-zero float arrays that cause inaccurate pitch detection on quiet hums.
    """
    audio = np.concatenate(frames, axis=0).astype(np.float32).flatten()

    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.9 * 32767.0
    audio = audio.clip(-32767, 32767).astype(np.int16)

    if out_path is None:
        fd, out_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)

    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(audio.tobytes())

    return out_path
