"""faster-whisper transcription with CUDA -> CPU int8 fallback."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

_LANGUAGE_MODES = {"auto": None, "en": "en", "he": "he"}


def resolve_cpu_threads(cpu_threads: int, cpu_count: int | None = None) -> int:
    """0 (auto) -> all logical cores; otherwise the explicit request.

    faster-whisper/CTranslate2 default to a conservative thread count, which
    leaves a CPU-only machine mostly idle. Auto uses every core.
    """
    if cpu_threads > 0:
        return cpu_threads
    resolved = cpu_count if cpu_count is not None else os.cpu_count()
    return resolved or 0


def resolve_language(mode: str) -> str | None:
    if mode not in _LANGUAGE_MODES:
        raise ValueError(f"unknown language mode: {mode!r}")
    return _LANGUAGE_MODES[mode]


def build_initial_prompt(vocabulary: list[str]) -> str | None:
    words = [w.strip() for w in vocabulary if w.strip()]
    if not words:
        return None
    return "Glossary: " + ", ".join(words) + "."


@dataclass
class TranscriptionResult:
    text: str
    language: str
    duration_s: float


def plan_load_attempts(device: str, compute_type: str) -> list[tuple[str, str]]:
    """Ordered (device, compute_type) load attempts.

    Honors the requested compute type on the chosen device, with CPU int8 as a
    final safety net (float16 isn't valid on CPU, so a bad request degrades
    instead of failing).
    """
    if device == "cpu":
        attempts = [("cpu", compute_type)]
    elif device == "cuda":
        return [("cuda", compute_type)]  # explicit CUDA: no silent CPU fallback
    else:  # auto: prefer CUDA, then CPU with the requested type
        attempts = [("cuda", compute_type), ("cpu", compute_type)]
    if ("cpu", "int8") not in attempts:
        attempts.append(("cpu", "int8"))
    return attempts


class Transcriber:
    def __init__(self, model_size: str = "large-v3-turbo", device: str = "auto",
                 compute_type: str = "float16", cpu_threads: int = 0,
                 num_workers: int = 1, model_factory: Callable | None = None):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads  # 0 = all cores (resolve_cpu_threads)
        self.num_workers = num_workers  # >1 only helps concurrent transcriptions
        self.active_device: str | None = None
        self.active_compute_type: str | None = None
        self._factory = model_factory or self._default_factory
        self._model = None

    def _default_factory(self, model_size: str, device: str, compute_type: str):
        from faster_whisper import WhisperModel  # heavy import, deferred

        return WhisperModel(
            model_size, device=device, compute_type=compute_type,
            cpu_threads=resolve_cpu_threads(self.cpu_threads),
            num_workers=self.num_workers)

    def load(self) -> str:
        """Load the model; returns the device actually used ('cuda' or 'cpu')."""
        if self._model is not None:
            return self.active_device
        last_exc: Exception | None = None
        for device, compute_type in plan_load_attempts(self.device, self.compute_type):
            try:
                self._model = self._factory(self.model_size, device, compute_type)
                self.active_device = device
                self.active_compute_type = compute_type
                return device
            except Exception as exc:
                last_exc = exc
                log.warning("model load failed on %s/%s: %s", device, compute_type, exc)
        raise RuntimeError(f"could not load Whisper model: {last_exc}")

    def transcribe(self, audio: np.ndarray, language_mode: str = "auto",
                   vocabulary: list[str] = ()) -> TranscriptionResult:
        self.load()
        segments, info = self._model.transcribe(
            audio,
            language=resolve_language(language_mode),
            initial_prompt=build_initial_prompt(list(vocabulary)),
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return TranscriptionResult(text=text, language=info.language,
                                   duration_s=float(info.duration))
