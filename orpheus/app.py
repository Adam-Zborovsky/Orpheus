"""AppController: state machine + pipeline orchestration off the UI thread."""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .audio import AudioCapture
from .cleanup import Cleanup, OllamaProvider
from .history import HistoryStore
from .injector import TextInjector
from .settings import Settings, default_config_path
from .transcriber import Transcriber

log = logging.getLogger(__name__)


class State(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"


class DictationStateMachine:
    """Pure toggle logic: which action a hotkey tap triggers in each state."""

    def __init__(self):
        self.state = State.IDLE

    def on_toggle(self) -> str | None:
        if self.state is State.IDLE:
            self.state = State.LISTENING
            return "start"
        if self.state is State.LISTENING:
            self.state = State.PROCESSING
            return "stop"
        return None  # PROCESSING: ignore taps until the pipeline finishes

    def on_finished(self) -> None:
        self.state = State.IDLE


@dataclass
class PipelineResult:
    ok: bool
    final_text: str = ""
    raw_text: str = ""
    used_llm: bool = False
    error: str | None = None
    notice: str | None = None  # non-fatal warning worth surfacing


def run_pipeline(audio, settings: Settings, transcriber, cleanup, injector,
                 history) -> PipelineResult:
    """Audio -> STT -> optional LLM cleanup -> inject -> history.

    Cleanup failures degrade to the raw transcript; captured words are never lost.
    """
    if audio is None or getattr(audio, "size", 0) == 0:
        return PipelineResult(ok=False, error="No audio captured")
    started = time.monotonic()
    try:
        transcription = transcriber.transcribe(
            audio, language_mode=settings.language,
            vocabulary=settings.vocabulary)
    except Exception as exc:
        return PipelineResult(ok=False, error=f"Transcription failed: {exc}")
    raw = transcription.text.strip()
    if not raw:
        return PipelineResult(ok=False, error="Nothing was heard")

    final, used_llm, notice = raw, False, None
    if settings.cleanup_enabled and cleanup is not None:
        result = cleanup.run(raw, settings.cleanup_prompt, settings.vocabulary)
        final, used_llm = result.text, result.used_llm
        if result.error:
            notice = f"Cleanup failed, inserted raw transcript ({result.error})"

    try:
        injector.inject(final)
    except Exception as exc:
        return PipelineResult(ok=False, raw_text=raw, final_text=final,
                              error=f"Could not type text: {exc}")

    if settings.history_enabled and history is not None:
        try:
            history.add(raw, final, duration_s=time.monotonic() - started)
        except Exception:
            log.exception("history write failed")

    return PipelineResult(ok=True, final_text=final, raw_text=raw,
                          used_llm=used_llm, notice=notice)


class _WorkerThread(QThread):
    """Runs a callable off the UI thread and emits its result (or exception)."""

    done = Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as exc:
            self.done.emit(exc)


class AppController(QObject):
    state_changed = Signal(str)  # idle | listening | processing | done | error
    level_changed = Signal(float)
    notice = Signal(str)
    error = Signal(str)
    _toggle_requested = Signal()

    def __init__(self, settings: Settings, data_dir: Path | None = None,
                 parent=None):
        super().__init__(parent)
        self._settings = settings
        self._data_dir = data_dir or default_config_path().parent
        self._machine = DictationStateMachine()
        self._capture = AudioCapture(level_callback=self.level_changed.emit)
        self._history: HistoryStore | None = None
        self._threads: list[_WorkerThread] = []
        self._build_components()
        self._toggle_requested.connect(self._handle_toggle)

    def _build_components(self) -> None:
        s = self._settings
        self._transcriber = Transcriber(s.model_size, s.device, s.compute_type)
        self._cleanup = Cleanup(OllamaProvider(s.ollama_url, s.ollama_model))
        self._injector = TextInjector(s.delivery)
        if self._history is not None:
            self._history.close()
            self._history = None
        if s.history_enabled:
            self._history = HistoryStore(self._data_dir / "history.sqlite3")

    # -- public API -----------------------------------------------------------

    def toggle(self) -> None:
        """Thread-safe: may be called from the pynput listener thread."""
        self._toggle_requested.emit()

    def apply_settings(self, settings: Settings) -> None:
        model_key = (self._settings.model_size, self._settings.device,
                     self._settings.compute_type)
        self._settings = settings
        self._build_components()
        if (settings.model_size, settings.device,
                settings.compute_type) != model_key:
            self.preload()

    def preload(self) -> None:
        self._spawn(self._transcriber.load, self._on_preloaded)

    def shutdown(self) -> None:
        try:
            self._capture.stop()
        except Exception:
            pass
        for thread in list(self._threads):
            thread.wait(5000)
        if self._history is not None:
            self._history.close()

    # -- internals --------------------------------------------------------------

    def _spawn(self, fn, callback) -> None:
        thread = _WorkerThread(fn, parent=self)
        thread.done.connect(callback)
        thread.finished.connect(lambda t=thread: self._threads.remove(t))
        self._threads.append(thread)
        thread.start()

    def _on_preloaded(self, result) -> None:
        if isinstance(result, Exception):
            self.error.emit(f"Whisper model failed to load: {result}")
            self.state_changed.emit("error")
        elif result == "cpu" and self._settings.device != "cpu":
            self.notice.emit("CUDA unavailable — transcribing on CPU (int8). "
                             "Expect slower results.")

    @Slot()
    def _handle_toggle(self) -> None:
        action = self._machine.on_toggle()
        if action == "start":
            try:
                self._capture.start(self._settings.input_device)
            except Exception as exc:
                self._machine.on_finished()
                self.error.emit(f"Microphone unavailable: {exc}")
                self.state_changed.emit("error")
                return
            self.state_changed.emit("listening")
        elif action == "stop":
            audio = self._capture.stop()
            self.state_changed.emit("processing")
            settings = self._settings
            transcriber, cleanup = self._transcriber, self._cleanup
            injector, history = self._injector, self._history
            self._spawn(
                lambda: run_pipeline(audio, settings, transcriber, cleanup,
                                     injector, history),
                self._on_pipeline_done,
            )

    def _on_pipeline_done(self, result) -> None:
        self._machine.on_finished()
        if isinstance(result, Exception):
            self.error.emit(f"Dictation failed: {result}")
            self.state_changed.emit("error")
            return
        if result.ok:
            if result.notice:
                self.notice.emit(result.notice)
            self.state_changed.emit("done")
        else:
            self.error.emit(result.error or "Dictation failed")
            self.state_changed.emit("error")
