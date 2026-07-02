from types import SimpleNamespace

import numpy as np

from orpheus.app import DictationStateMachine, State, run_pipeline
from orpheus.settings import Settings


# --- state machine -----------------------------------------------------------

def test_toggle_from_idle_starts():
    machine = DictationStateMachine()
    assert machine.on_toggle() == "start"
    assert machine.state is State.LISTENING


def test_toggle_from_listening_stops():
    machine = DictationStateMachine()
    machine.on_toggle()
    assert machine.on_toggle() == "stop"
    assert machine.state is State.PROCESSING


def test_toggle_while_processing_cancels():
    machine = DictationStateMachine()
    machine.on_toggle()
    machine.on_toggle()
    assert machine.on_toggle() == "cancel"
    assert machine.state is State.IDLE


def test_toggle_after_cancel_starts_a_new_session():
    machine = DictationStateMachine()
    machine.on_toggle()  # start
    machine.on_toggle()  # stop
    machine.on_toggle()  # cancel -> back to idle
    assert machine.on_toggle() == "start"
    assert machine.state is State.LISTENING


def test_finished_returns_to_idle():
    machine = DictationStateMachine()
    machine.on_toggle()
    machine.on_toggle()
    machine.on_finished()
    assert machine.state is State.IDLE


# --- pipeline ----------------------------------------------------------------

class FakeTranscriber:
    def __init__(self, text="hello world"):
        self.text = text
        self.calls = []

    def transcribe(self, audio, language_mode, vocabulary):
        self.calls.append((language_mode, list(vocabulary)))
        return SimpleNamespace(text=self.text, language="en", duration_s=1.0)


class FakeCleanup:
    def __init__(self, text="Hello, world.", used_llm=True, error=None):
        self.result = SimpleNamespace(text=text, used_llm=used_llm, error=error)
        self.calls = []

    def run(self, raw, prompt, vocabulary):
        self.calls.append(raw)
        if self.result.error and not self.result.used_llm:
            return SimpleNamespace(text=raw, used_llm=False, error=self.result.error)
        return self.result


class FakeInjector:
    def __init__(self, fail=False):
        self.fail = fail
        self.injected = []

    def inject(self, text):
        if self.fail:
            raise RuntimeError("focus lost")
        self.injected.append(text)


class FakeHistory:
    def __init__(self):
        self.rows = []

    def add(self, raw, final, duration_s=0.0):
        self.rows.append((raw, final))


AUDIO = np.ones(16000, dtype=np.float32)


def test_pipeline_happy_path():
    injector, history = FakeInjector(), FakeHistory()
    result = run_pipeline(AUDIO, Settings(), FakeTranscriber(), FakeCleanup(),
                          injector, history)
    assert result.ok is True
    assert result.final_text == "Hello, world."
    assert result.raw_text == "hello world"
    assert result.used_llm is True
    assert result.notice is None
    assert injector.injected == ["Hello, world."]
    assert history.rows == [("hello world", "Hello, world.")]


def test_pipeline_no_audio():
    result = run_pipeline(np.zeros(0, dtype=np.float32), Settings(),
                          FakeTranscriber(), FakeCleanup(), FakeInjector(), None)
    assert result.ok is False
    assert "audio" in result.error.lower()


def test_pipeline_empty_transcript():
    result = run_pipeline(AUDIO, Settings(), FakeTranscriber(text="  "),
                          FakeCleanup(), FakeInjector(), None)
    assert result.ok is False
    assert result.error == "Nothing was heard"


def test_pipeline_cleanup_failure_injects_raw_with_notice():
    injector = FakeInjector()
    cleanup = FakeCleanup(used_llm=False, error="connection refused")
    result = run_pipeline(AUDIO, Settings(), FakeTranscriber(), cleanup,
                          injector, None)
    assert result.ok is True
    assert result.final_text == "hello world"  # raw words are never lost
    assert result.used_llm is False
    assert "cleanup failed" in result.notice.lower()
    assert injector.injected == ["hello world"]


def test_pipeline_cleanup_disabled_skips_llm():
    cleanup = FakeCleanup()
    settings = Settings(cleanup_enabled=False)
    result = run_pipeline(AUDIO, settings, FakeTranscriber(), cleanup,
                          FakeInjector(), None)
    assert result.ok is True
    assert result.final_text == "hello world"
    assert cleanup.calls == []


def test_pipeline_injection_failure():
    result = run_pipeline(AUDIO, Settings(), FakeTranscriber(), FakeCleanup(),
                          FakeInjector(fail=True), None)
    assert result.ok is False
    assert "focus lost" in result.error


def test_pipeline_transcriber_failure():
    class Broken:
        def transcribe(self, *a, **k):
            raise RuntimeError("model died")

    result = run_pipeline(AUDIO, Settings(), Broken(), FakeCleanup(),
                          FakeInjector(), None)
    assert result.ok is False
    assert "model died" in result.error


def test_pipeline_history_disabled():
    history = FakeHistory()
    settings = Settings(history_enabled=False)
    result = run_pipeline(AUDIO, settings, FakeTranscriber(), FakeCleanup(),
                          FakeInjector(), history)
    assert result.ok is True
    assert history.rows == []


def test_pipeline_passes_language_and_vocab():
    transcriber = FakeTranscriber()
    settings = Settings(language="he", vocabulary=["Orpheus"])
    run_pipeline(AUDIO, settings, transcriber, FakeCleanup(), FakeInjector(), None)
    assert transcriber.calls == [("he", ["Orpheus"])]


# --- cancellation --------------------------------------------------------------

def test_pipeline_cancelled_after_transcription_skips_cleanup_and_injection():
    cleanup, injector, history = FakeCleanup(), FakeInjector(), FakeHistory()
    result = run_pipeline(AUDIO, Settings(), FakeTranscriber(), cleanup, injector,
                          history, is_cancelled=lambda: True)
    assert result.ok is False
    assert result.cancelled is True
    assert cleanup.calls == []
    assert injector.injected == []
    assert history.rows == []


def test_pipeline_cancelled_after_cleanup_skips_injection():
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] > 1  # not cancelled at the first check, cancelled by the second

    injector, history = FakeInjector(), FakeHistory()
    result = run_pipeline(AUDIO, Settings(), FakeTranscriber(), FakeCleanup(),
                          injector, history, is_cancelled=is_cancelled)
    assert result.ok is False
    assert result.cancelled is True
    assert injector.injected == []
    assert history.rows == []


def test_pipeline_not_cancelled_by_default():
    result = run_pipeline(AUDIO, Settings(), FakeTranscriber(), FakeCleanup(),
                          FakeInjector(), None)
    assert result.cancelled is False
