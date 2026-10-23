"""Microphone capture: 16 kHz mono float32 audio and live RMS levels."""
from __future__ import annotations

import threading
from collections import deque
from typing import Callable

import numpy as np

SAMPLE_RATE = 16_000
DEFAULT_MAX_SECONDS = 1_800.0
_CURRENT_CALLBACK = object()


class AudioStopError(RuntimeError):
    """A stream cleanup failure that still carries the complete captured audio."""

    def __init__(self, message: str, audio: np.ndarray):
        super().__init__(message)
        self.audio = audio


def _sample_limit(seconds: float, samplerate: int) -> int:
    if seconds <= 0:
        raise ValueError("max_seconds must be greater than zero")
    if samplerate <= 0:
        raise ValueError("samplerate must be greater than zero")
    return max(1, int(seconds * samplerate))


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


class RingBuffer:
    """Bounded mono float32 buffer retaining the first samples up to its cap.

    ``append`` returns True only on the first append that reaches the sample
    limit. A chunk crossing the limit is truncated at the exact boundary.
    """

    def __init__(self, max_seconds: float = DEFAULT_MAX_SECONDS,
                 samplerate: int = SAMPLE_RATE):
        self._max_samples = _sample_limit(max_seconds, samplerate)
        self._samplerate = samplerate
        self._chunks: deque[np.ndarray] = deque()
        self._samples = 0
        self._reached = False
        self._lock = threading.Lock()

    def append(self, chunk: np.ndarray) -> bool:
        flat = np.asarray(chunk, dtype=np.float32).reshape(-1)
        with self._lock:
            if self._reached:
                return False
            remaining = self._max_samples - self._samples
            if remaining <= 0:
                self._reached = True
                return True
            retained = flat[:remaining].copy()
            if retained.size:
                self._chunks.append(retained)
                self._samples += retained.size
            if self._samples >= self._max_samples:
                self._reached = True
                return True
            return False

    def get(self) -> np.ndarray:
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(list(self._chunks))

    @property
    def duration_s(self) -> float:
        with self._lock:
            return self._samples / self._samplerate

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._samples = 0
            self._reached = False


class AudioCapture:
    """Capture microphone audio until stopped or the configured limit is hit."""

    def __init__(self, samplerate: int = SAMPLE_RATE,
                 level_callback: Callable[[float], None] | None = None,
                 max_seconds: float = DEFAULT_MAX_SECONDS,
                 limit_callback: Callable[[], None] | None = None):
        self.samplerate = samplerate
        self._level_callback = level_callback
        self._limit_callback = limit_callback
        self._max_seconds = float(max_seconds)
        self._buffer = RingBuffer(max_seconds, samplerate)
        self._stream = None
        self._lock = threading.RLock()
        self._generation = 0
        self._active = False

    @staticmethod
    def list_devices() -> list[tuple[int, str]]:
        import sounddevice as sd

        return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0]

    @property
    def duration_s(self) -> float:
        return self._buffer.duration_s

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._active

    def set_max_seconds(self, seconds: float) -> None:
        """Change the capture limit while stopped, clearing any old samples."""
        _sample_limit(float(seconds), self.samplerate)
        with self._lock:
            if self._active or self._stream is not None:
                raise RuntimeError("Cannot change the recording limit while recording")
            self._max_seconds = float(seconds)
            self._buffer = RingBuffer(self._max_seconds, self.samplerate)

    def set_limit_callback(self, callback: Callable[[], None] | None) -> None:
        with self._lock:
            if self._active:
                raise RuntimeError("Cannot change the recording callback while recording")
            self._limit_callback = callback

    def start(self, device_name: str = "") -> None:
        import sounddevice as sd

        with self._lock:
            if self._active or self._stream is not None:
                return
            self._buffer.clear()
            self._generation += 1
            generation = self._generation
            limit_callback = self._limit_callback
            self._active = True
            self._limit_notified = False

        device = None
        if device_name:
            try:
                device = next((index for index, name in self.list_devices()
                               if name == device_name), None)
            except Exception:
                with self._lock:
                    if self._generation == generation:
                        self._generation += 1
                        self._active = False
                        self._buffer.clear()
                raise
            if device is None:
                with self._lock:
                    if self._generation == generation:
                        self._generation += 1
                        self._active = False
                        self._buffer.clear()
                raise RuntimeError(
                    f"Configured microphone '{device_name}' is unavailable. "
                    "Select another microphone in Settings."
                )

        stream = None
        try:
            callback = lambda indata, frames, time_info, status: self._callback(
                indata, frames, time_info, status, generation, limit_callback
            )
            stream = sd.InputStream(
                samplerate=self.samplerate, channels=1, dtype="float32",
                device=device, callback=callback,
            )
            with self._lock:
                if self._generation != generation:
                    raise RuntimeError("Microphone start was superseded")
                self._stream = stream
            stream.start()
        except Exception as start_exc:
            with self._lock:
                if self._generation == generation:
                    self._generation += 1
                    self._active = False
                    self._stream = None
                    self._buffer.clear()
            if stream is not None:
                try:
                    stream.close()
                except Exception as close_exc:
                    # Keep the construction/start error primary while retaining
                    # the close failure for callers that support exception notes.
                    start_exc.add_note(
                        f"Closing the failed microphone stream also failed: {close_exc}"
                    )
            raise

    def _callback(self, indata, frames, time_info, status,
                  generation: int | None = None, limit_callback=_CURRENT_CALLBACK) -> None:
        mono = np.asarray(indata, dtype=np.float32)[:, 0].copy()
        with self._lock:
            if not self._active or (generation is not None and generation != self._generation):
                return
            reached = self._buffer.append(mono)
            if reached and not self._limit_notified:
                self._limit_notified = True
                notify_limit = True
            else:
                notify_limit = False

        if self._level_callback is not None and mono.size:
            self._level_callback(rms(mono))
        callback = self._limit_callback if limit_callback is _CURRENT_CALLBACK else limit_callback
        if reached and callback is not None and notify_limit:
            callback()

    def stop(self) -> np.ndarray:
        with self._lock:
            stream = self._stream
            self._generation += 1
            self._active = False
            self._stream = None

        failure: Exception | None = None
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:
                failure = exc
            try:
                stream.close()
            except Exception as exc:
                if failure is None:
                    failure = exc

        audio = self._buffer.get()
        self._buffer.clear()
        if failure is not None:
            raise AudioStopError(
                f"Could not stop microphone cleanly: {failure}", audio
            ) from failure
        return audio
