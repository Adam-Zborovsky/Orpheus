from types import SimpleNamespace

import numpy as np
import pytest

from orpheus.transcriber import (Transcriber, build_initial_prompt,
                                 resolve_cpu_threads, resolve_language)


def test_resolve_cpu_threads_explicit_wins():
    assert resolve_cpu_threads(6, cpu_count=16) == 6


def test_resolve_cpu_threads_auto_uses_all_cores():
    assert resolve_cpu_threads(0, cpu_count=16) == 16


def test_resolve_cpu_threads_auto_handles_unknown_count():
    assert resolve_cpu_threads(0, cpu_count=None) >= 0


def test_resolve_language():
    assert resolve_language("auto") is None
    assert resolve_language("en") == "en"
    assert resolve_language("he") == "he"
    with pytest.raises(ValueError):
        resolve_language("klingon")


def test_build_initial_prompt_empty():
    assert build_initial_prompt([]) is None
    assert build_initial_prompt(["  ", ""]) is None


def test_build_initial_prompt_with_words():
    assert build_initial_prompt(["Orpheus", " PySide6 "]) == "Glossary: Orpheus, PySide6."


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        segments = iter([SimpleNamespace(text=" Hello world. "),
                         SimpleNamespace(text=" Second segment. ")])
        info = SimpleNamespace(language="en", duration=2.5)
        return segments, info


def test_load_auto_prefers_cuda():
    attempts = []

    def factory(model_size, device, compute_type):
        attempts.append((device, compute_type))
        return FakeModel()

    t = Transcriber(device="auto", compute_type="float16", model_factory=factory)
    assert t.load() == "cuda"
    assert t.active_device == "cuda"
    assert attempts == [("cuda", "float16")]


def test_load_auto_falls_back_to_cpu_int8_when_float16_unsupported():
    attempts = []

    def factory(model_size, device, compute_type):
        attempts.append((device, compute_type))
        if device == "cuda" or compute_type == "float16":  # CPU can't do float16
            raise RuntimeError("unsupported")
        return FakeModel()

    t = Transcriber(device="auto", compute_type="float16", model_factory=factory)
    assert t.load() == "cpu"
    assert attempts == [("cuda", "float16"), ("cpu", "float16"), ("cpu", "int8")]
    assert t.active_compute_type == "int8"


def test_load_cpu_honors_requested_compute_type():
    attempts = []

    def factory(model_size, device, compute_type):
        attempts.append((device, compute_type))
        return FakeModel()

    t = Transcriber(device="cpu", compute_type="float32", model_factory=factory)
    assert t.load() == "cpu"
    assert attempts == [("cpu", "float32")]
    assert t.active_compute_type == "float32"


def test_load_cpu_invalid_type_falls_back_to_int8():
    attempts = []

    def factory(model_size, device, compute_type):
        attempts.append((device, compute_type))
        if compute_type == "float16":
            raise RuntimeError("float16 unsupported on CPU")
        return FakeModel()

    t = Transcriber(device="cpu", compute_type="float16", model_factory=factory)
    assert t.load() == "cpu"
    assert attempts == [("cpu", "float16"), ("cpu", "int8")]
    assert t.active_compute_type == "int8"


def test_load_total_failure_raises():
    def factory(model_size, device, compute_type):
        raise RuntimeError("no backend")

    with pytest.raises(RuntimeError, match="could not load"):
        Transcriber(device="auto", model_factory=factory).load()


def test_load_is_idempotent():
    count = {"n": 0}

    def factory(model_size, device, compute_type):
        count["n"] += 1
        return FakeModel()

    t = Transcriber(model_factory=factory)
    t.load()
    t.load()
    assert count["n"] == 1


def test_transcribe_joins_segments_and_passes_options():
    model = FakeModel()
    t = Transcriber(model_factory=lambda *a: model)
    audio = np.zeros(16000, dtype=np.float32)
    result = t.transcribe(audio, language_mode="he", vocabulary=["Orpheus"])
    assert result.text == "Hello world. Second segment."
    assert result.language == "en"
    assert result.duration_s == 2.5
    call = model.calls[0]
    assert call["language"] == "he"
    assert call["initial_prompt"] == "Glossary: Orpheus."
    assert call["vad_filter"] is True
