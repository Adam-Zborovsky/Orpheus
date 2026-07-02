"""AppController: state machine + pipeline orchestration off the UI thread."""
from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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
        if self.state is State.PROCESSING:
            self.state = State.IDLE  # a tap mid-pipeline cancels it
            return "cancel"
        return None

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
    cancelled: bool = False    # tapped mid-pipeline; suppress error/done UI


def run_pipeline(audio, settings: Settings, transcriber, cleanup, injector,
                 history, is_cancelled: Callable[[], bool] = lambda: False
                 ) -> PipelineResult:
    """Audio -> STT -> optional LLM cleanup -> inject -> history.

    Cleanup failures degrade to the raw transcript; captured words are never
    lost. `is_cancelled` is polled between stages (STT and cleanup calls
    themselves can't be interrupted mid-call) — a cancel skips whatever
    hasn't run yet, so nothing gets typed or saved after the hotkey cancels.
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
    if is_cancelled():
        return PipelineResult(ok=False, raw_text=raw, cancelled=True)

    final, used_llm, notice = raw, False, None
    if settings.cleanup_enabled and cleanup is not None:
        result = cleanup.run(raw, settings.cleanup_prompt, settings.vocabulary)
        final, used_llm = result.text, result.used_llm
        if result.error:
            notice = f"Cleanup failed, inserted raw transcript ({result.error})"
    if is_cancelled():
        return PipelineResult(ok=False, raw_text=raw, final_text=final,
                              cancelled=True)

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
        self._cancel_event: threading.Event | None = None
        self._build_components()
        self._toggle_requested.connect(self._handle_toggle)

    _MODEL_FIELDS = ("model_size", "device", "compute_type", "cpu_threads",
                     "num_workers")

    def _make_transcriber(self) -> Transcriber:
        s = self._settings
        return Transcriber(s.model_size, s.device, s.compute_type,
                           cpu_threads=s.cpu_threads, num_workers=s.num_workers)

    def _sync_history(self) -> None:
        if not self._settings.history_enabled:
            if self._history is not None:
                self._history.close()
                self._history = None
            return
        if self._history is None:
            self._history = HistoryStore(self._data_dir / "history.sqlite3")

    def _build_components(self) -> None:
        s = self._settings
        self._transcriber = self._make_transcriber()
        self._cleanup = Cleanup(OllamaProvider(s.ollama_url, s.ollama_model))
        self._injector = TextInjector(s.delivery)
        self._sync_history()

    # -- public API -----------------------------------------------------------

    def toggle(self) -> None:
        """Thread-safe: may be called from the pynput listener thread."""
        self._toggle_requested.emit()

    def apply_settings(self, settings: Settings) -> None:
        old = self._settings
        self._settings = settings
        # Cheap components: always rebuild to pick up new values.
        self._cleanup = Cleanup(OllamaProvider(settings.ollama_url,
                                               settings.ollama_model))
        self._injector = TextInjector(settings.delivery)
        self._sync_history()
        # Expensive: only rebuild + reload the model when its inputs changed.
        if any(getattr(old, f) != getattr(settings, f) for f in self._MODEL_FIELDS):
            self._transcriber = self._make_transcriber()
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
            ct = self._transcriber.active_compute_type or "int8"
            self.notice.emit(f"CUDA unavailable — transcribing on CPU ({ct}). "
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
            cancel_event = threading.Event()
            self._cancel_event = cancel_event
            self._spawn(
                lambda: run_pipeline(audio, settings, transcriber, cleanup,
                                     injector, history,
                                     is_cancelled=cancel_event.is_set),
                self._on_pipeline_done,
            )
        elif action == "cancel":
            if self._cancel_event is not None:
                self._cancel_event.set()
            self.state_changed.emit("idle")

    def _on_pipeline_done(self, result) -> None:
        if getattr(result, "cancelled", False):
            # State was already reset to idle when the hotkey cancelled it;
            # a concurrent new session may already be listening/processing,
            # so this stale result must not touch state or emit anything.
            return
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
