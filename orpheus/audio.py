"""Microphone capture: 16 kHz mono ring buffer + live RMS levels."""
from __future__ import annotations

import threading
from collections import deque
from typing import Callable

import numpy as np

SAMPLE_RATE = 16_000


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


class RingBuffer:
    """Bounded mono float32 buffer; drops oldest chunks past max_seconds."""

    def __init__(self, max_seconds: float = 300.0, samplerate: int = SAMPLE_RATE):
        self._max_samples = int(max_seconds * samplerate)
        self._chunks: deque[np.ndarray] = deque()
        self._samples = 0
        self._lock = threading.Lock()

    def append(self, chunk: np.ndarray) -> None:
        flat = np.asarray(chunk, dtype=np.float32).reshape(-1)
        with self._lock:
            self._chunks.append(flat)
            self._samples += flat.size
            while self._samples > self._max_samples and len(self._chunks) > 1:
                self._samples -= self._chunks.popleft().size

    def get(self) -> np.ndarray:
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(list(self._chunks))

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._samples = 0


class AudioCapture:
    """Records the microphone into a ring buffer via a PortAudio callback."""

    def __init__(self, samplerate: int = SAMPLE_RATE,
                 level_callback: Callable[[float], None] | None = None,
                 max_seconds: float = 300.0):
        self.samplerate = samplerate
        self._level_callback = level_callback
        self._buffer = RingBuffer(max_seconds, samplerate)
        self._stream = None

    @staticmethod
    def list_devices() -> list[tuple[int, str]]:
        import sounddevice as sd

        return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0]

    def start(self, device_name: str = "") -> None:
        import sounddevice as sd

        if self._stream is not None:
            return
        self._buffer.clear()
        device = None
        if device_name:
            for index, name in self.list_devices():
                if name == device_name:
                    device = index
                    break
        self._stream = sd.InputStream(
            samplerate=self.samplerate, channels=1, dtype="float32",
            device=device, callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = np.asarray(indata)[:, 0].copy()
        self._buffer.append(mono)
        if self._level_callback is not None:
            self._level_callback(rms(mono))

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return self._buffer.get()
