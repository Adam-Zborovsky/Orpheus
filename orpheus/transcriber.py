"""faster-whisper transcription with CUDA -> CPU int8 fallback."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

_LANGUAGE_MODES = {"auto": None, "en": "en", "he": "he"}


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


def _default_model_factory(model_size: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel  # heavy import, deferred

    return WhisperModel(model_size, device=device, compute_type=compute_type)


class Transcriber:
    def __init__(self, model_size: str = "large-v3", device: str = "auto",
                 compute_type: str = "float16",
                 model_factory: Callable | None = None):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.active_device: str | None = None
        self._factory = model_factory or _default_model_factory
        self._model = None

    def load(self) -> str:
        """Load the model; returns the device actually used ('cuda' or 'cpu')."""
        if self._model is not None:
            return self.active_device
        if self.device == "cpu":
            attempts = [("cpu", "int8")]
        elif self.device == "cuda":
            attempts = [("cuda", self.compute_type)]
        else:  # auto: try CUDA, fall back to CPU int8 (spec: error handling)
            attempts = [("cuda", self.compute_type), ("cpu", "int8")]
        last_exc: Exception | None = None
        for device, compute_type in attempts:
            try:
                self._model = self._factory(self.model_size, device, compute_type)
                self.active_device = device
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
