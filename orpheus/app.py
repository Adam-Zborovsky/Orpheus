"""Dictation sessions, bounded background work, and recoverable delivery."""
from __future__ import annotations

import copy
import enum
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from .audio import AudioCapture, AudioStopError
from .cleanup import Cleanup, OllamaProvider
from .diagnostics import record_event
from .history import HistoryStore
from .injector import DeliveryError, TextInjector, foreground_target
from .settings import Settings, default_config_path
from .transcriber import Transcriber

log = logging.getLogger(__name__)


class State(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"


class DictationStateMachine:
    def __init__(self):
        self.state = State.IDLE

    def on_toggle(self) -> str:
        if self.state is State.IDLE:
            self.state = State.LISTENING
            return "start"
        if self.state is State.LISTENING:
            self.state = State.PROCESSING
            return "stop"
        self.state = State.IDLE
        return "cancel"

    def on_finished(self) -> None:
        self.state = State.IDLE


@dataclass
class PipelineResult:
    ok: bool
    final_text: str = ""
    raw_text: str = ""
    used_llm: bool = False
    error: str | None = None
    notice: str | None = None
    cancelled: bool = False
    history_id: int | None = None
    history_failed: bool = False
    partial: bool = False


def run_pipeline(audio, settings: Settings, transcriber, cleanup, injector,
                 history, is_cancelled: Callable[[], bool] = lambda: False,
                 on_stage: Callable[[str], None] = lambda stage: None
                 ) -> PipelineResult:
    """Preserve usable text before delivery; cancelled calls never start a stage."""
    if is_cancelled():
        return PipelineResult(ok=False, cancelled=True)
    if audio is None or getattr(audio, "size", 0) == 0:
        return PipelineResult(ok=False, error="No audio captured")
    started = time.monotonic()
    record_event("transcription_started")
    on_stage("transcribing")
    try:
        transcription = transcriber.transcribe(
            audio, language_mode=settings.language, vocabulary=settings.vocabulary)
    except Exception as exc:
        if is_cancelled():
            return PipelineResult(ok=False, cancelled=True)
        return PipelineResult(ok=False, error=f"Transcription failed: {exc}. Retry loading the model in Settings.")
    raw = transcription.text.strip()
    record_event("transcription_completed", duration_ms=(time.monotonic() - started) * 1000)
    if is_cancelled():
        return PipelineResult(ok=False, raw_text=raw, cancelled=True)
    if not raw:
        return PipelineResult(ok=False, error="Nothing was heard")
    result = PipelineResult(ok=False, raw_text=raw, final_text=raw)
    notices = []
    if settings.cleanup_enabled and cleanup is not None:
        record_event("cleanup_started")
        on_stage("cleaning")
        try:
            cleaned = cleanup.run(raw, settings.cleanup_prompt, settings.vocabulary)
            result.final_text = cleaned.text or raw
            result.used_llm = cleaned.used_llm
            if cleaned.error:
                notices.append("Cleanup failed; using the raw transcript. Check Ollama in Settings.")
        except Exception:
            notices.append("Cleanup failed; using the raw transcript. Check Ollama in Settings.")
        record_event("cleanup_completed")
    if is_cancelled():
        result.cancelled = True
        return result
    if settings.history_enabled and history is not None:
        try:
            result.history_id = history.add(raw, result.final_text,
                                           duration_s=time.monotonic() - started)
        except Exception:
            result.history_failed = True
            notices.append("History could not be saved. A recovery copy is available until Orpheus exits.")
            log.error("history_write_failed")
            record_event("history_failed", error_category="history")
    elif settings.history_enabled:
        result.history_failed = True
        notices.append("History is unavailable. A recovery copy is available until Orpheus exits.")
    result.notice = " ".join(notices) or None
    if is_cancelled():
        result.cancelled = True
        return result
    on_stage("delivering")
    record_event("delivery_started")
    try:
        injector.inject(result.final_text)
    except Exception as exc:
        result.cancelled = bool(getattr(exc, "cancelled", False))
        result.partial = bool(getattr(exc, "partial", False))
        result.error = f"Could not type text: {exc}. Open Recover text from the tray to copy or retry."
        record_event("delivery_failed", error_category="delivery")
        return result
    result.ok = True
    record_event("delivery_completed")
    return result


@dataclass
class Session:
    id: int
    settings: Settings
    target: object
    cancel: threading.Event = field(default_factory=threading.Event)
    recovery_id: int | None = None


@dataclass
class RecoveryItem:
    id: int
    raw_text: str
    final_text: str
    error: str
    history_id: int | None = None
    partial: bool = False
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class _WorkerResult:
    job_id: int
    value: object


class _WorkerThread(QThread):
    done = Signal(object)

    def __init__(self, fn, parent=None, job_id: int = 0):
        super().__init__(parent)
        self._fn = fn
        self.job_id = job_id

    def run(self):
        fn, self._fn = self._fn, None
        try:
            value = fn()
        except Exception as exc:
            # Drop backend traceback frames, which can retain audio/model locals.
            value = RuntimeError(f"{type(exc).__name__}: {exc}")
        finally:
            fn = None  # release audio/model/client references before finished
        self.done.emit(_WorkerResult(self.job_id, value))


class _SessionInjector:
    def __init__(self, injector, session: Session):
        self.injector, self.session = injector, session

    def inject(self, text: str) -> None:
        if self.session.target is None:
            raise DeliveryError("The original destination could not be identified")
        self.injector.inject(text, target=self.session.target,
                             is_cancelled=self.session.cancel.is_set)


class AppController(QObject):
    state_changed = Signal(str)
    status_changed = Signal(str)
    backend_changed = Signal(str)
    level_changed = Signal(float)
    notice = Signal(str)
    error = Signal(str)
    recovery_changed = Signal()
    shutdown_finished = Signal()
    settings_applied = Signal(object)
    settings_failed = Signal(object, str)
    target_changed = Signal(object)
    _toggle_requested = Signal()
    _limit_requested = Signal(int)
    _stage_requested = Signal(int, str)

    _MODEL_FIELDS = ("model_size", "device", "compute_type", "cpu_threads", "num_workers")
    MAX_RECOVERY_ITEMS = 20

    def __init__(self, settings: Settings, data_dir: Path | None = None,
                 parent=None, target_provider=foreground_target):
        super().__init__(parent)
        self._settings = copy.deepcopy(settings)
        self._data_dir = data_dir or default_config_path().parent
        self._machine = DictationStateMachine()
        self._session: Session | None = None
        self._next_session = 0
        self._target_provider = target_provider
        self._capture = AudioCapture(
            level_callback=self.level_changed.emit,
            max_seconds=getattr(settings, "max_recording_seconds", 1800),
            limit_callback=self._capture_limit)
        self._history = None
        self._threads: list[_WorkerThread] = []
        self._callbacks: dict[int, Callable] = {}
        self._next_job = 0
        self._pipeline_active = False
        self._pending_job = None
        self._pending_settings = None
        self._model_generation = 0
        self._preload_generation: int | None = None
        self._shutting_down = False
        self._shutdown_complete = False
        self._recoveries: dict[int, RecoveryItem] = {}
        self._armed_retry: int | None = None
        self.settings_apply_hook: Callable[[Settings], None] | None = None
        self.last_error = ""
        self._error_recovery_id: int | None = None
        self.status = "Loading model"
        self.backend_status = "Model not loaded"
        self._build_components()
        self._toggle_requested.connect(self._handle_toggle)
        self._limit_requested.connect(self._on_limit, Qt.ConnectionType.QueuedConnection)
        self._stage_requested.connect(self._on_stage, Qt.ConnectionType.QueuedConnection)

    @property
    def recoveries(self) -> list[RecoveryItem]:
        return list(reversed(self._recoveries.values()))

    @property
    def pending_settings(self) -> bool:
        return self._pending_settings is not None

    def _make_transcriber(self):
        s = self._settings
        return Transcriber(s.model_size, s.device, s.compute_type,
                           cpu_threads=s.cpu_threads, num_workers=s.num_workers)

    def _build_components(self):
        s = self._settings
        self._transcriber = self._make_transcriber()
        self._cleanup = Cleanup(OllamaProvider(s.ollama_url, s.ollama_model))
        self._injector = TextInjector(s.delivery)
        self._sync_history()

    def _sync_history(self):
        if not self._settings.history_enabled:
            if self._history is not None:
                self._history.close()
                self._history = None
        elif self._history is None:
            try:
                self._history = HistoryStore(self._data_dir / "history.sqlite3")
            except Exception:
                self._fail("History is unavailable. Dictations will have an in-memory recovery copy; check the data folder permissions.")

    def toggle(self):
        self._toggle_requested.emit()

    def _set_status(self, text: str):
        self.status = text
        self.status_changed.emit(text)

    def _fail(self, text: str, recovery_id: int | None = None):
        self.last_error = text
        self._error_recovery_id = recovery_id
        self.error.emit(text)
        self.recovery_changed.emit()

    def apply_settings(self, settings: Settings):
        from .settings import validate_settings
        validate_settings(settings)
        if self._shutting_down:
            raise RuntimeError("Orpheus is shutting down. Reopen it before changing settings.")
        if self._threads or self._machine.state is not State.IDLE or self._pipeline_active:
            self._pending_settings = copy.deepcopy(settings)
            self.notice.emit("Settings saved. They will apply when the current work finishes.")
            return
        self._apply_settings_now(settings)

    def _apply_settings_now(self, settings):
        old = self._settings
        new_cleanup = Cleanup(OllamaProvider(settings.ollama_url, settings.ollama_model))
        try:
            if self.settings_apply_hook is not None:
                self.settings_apply_hook(settings)
        except Exception:
            new_cleanup.close()
            raise
        old_cleanup = self._cleanup
        self._settings = copy.deepcopy(settings)
        self._cleanup = new_cleanup
        self._injector = TextInjector(settings.delivery)
        self._capture.set_max_seconds(settings.max_recording_seconds)
        self._sync_history()
        old_cleanup.close()
        self._pending_settings = None
        self.settings_applied.emit(copy.deepcopy(self._settings))
        if any(getattr(old, f) != getattr(settings, f) for f in self._MODEL_FIELDS):
            self._model_generation += 1
            self._transcriber = self._make_transcriber()
            self.preload()

    def _apply_pending_if_safe(self):
        if (self._pending_settings is not None and not self._threads
                and self._machine.state is State.IDLE and not self._shutting_down):
            active = copy.deepcopy(self._settings)
            try:
                self._apply_settings_now(self._pending_settings)
            except Exception as exc:
                self._pending_settings = None
                self.settings_failed.emit(active, str(exc))
                self._fail(f"Settings could not be applied: {exc}. Open Settings to correct them.")

    def preload(self):
        if self._shutting_down or self._preload_generation == self._model_generation:
            return
        generation = self._model_generation
        self._preload_generation = generation
        self._set_status("Loading model")
        self._spawn(self._transcriber.load,
                    lambda result: self._on_preloaded(result, generation))

    def _spawn(self, fn, callback):
        self._next_job += 1
        worker = _WorkerThread(fn, self, self._next_job)
        self._callbacks[worker.job_id] = callback
        worker.done.connect(self._on_worker_result)
        worker.finished.connect(self._on_worker_finished)
        self._threads.append(worker)
        worker.start()
        return worker

    @Slot(object)
    def _on_worker_result(self, envelope):
        callback = self._callbacks.pop(envelope.job_id, None)
        if callback is not None:
            callback(envelope.value)

    @Slot()
    def _on_worker_finished(self):
        worker = self.sender()
        if worker in self._threads:
            self._threads.remove(worker)
        worker.deleteLater()
        self._apply_pending_if_safe()
        self._finish_shutdown_if_safe()

    def _on_preloaded(self, result, generation=None):
        if generation is not None and generation != self._model_generation:
            return
        self._preload_generation = None
        if self._shutting_down:
            return
        if isinstance(result, Exception):
            self.backend_status = "Model failed to load"
            self.backend_changed.emit(self.backend_status)
            self._fail(f"Whisper model failed to load: {result}")
            record_event("model_load_failed", error_category="model")
            if self._machine.state is State.IDLE:
                self._set_status("Model unavailable; retry in Settings")
                self.state_changed.emit("error")
        else:
            ct = getattr(self._transcriber, "active_compute_type", None) or "int8"
            self.backend_status = f"{str(result).upper()} · {ct} · {self._settings.model_size}"
            self.backend_changed.emit(self.backend_status)
            log.info("model_ready")
            record_event("model_loaded", backend=str(result), compute_type=ct)
            if self._machine.state is State.IDLE and self._armed_retry is None:
                self._set_status("Ready")

    def _capture_limit(self):
        session = self._session
        self._limit_requested.emit(session.id if session else -1)

    @Slot(int)
    def _on_limit(self, session_id):
        if (not self._shutting_down and self._session is not None
                and self._session.id == session_id and self._machine.state is State.LISTENING):
            self.notice.emit("Recording limit reached. Transcribing the complete recording.")
            record_event("capture_limit")
            self._stop_recording()

    @Slot(int, str)
    def _on_stage(self, session_id, stage):
        if self._shutting_down or self._session is None or self._session.id != session_id:
            return
        labels = {"transcribing": "Transcribing · press hotkey to cancel",
                  "cleaning": "Cleaning up · press hotkey to cancel",
                  "delivering": "Inserting text"}
        self._set_status(labels[stage])
        self.state_changed.emit(stage)

    @Slot()
    def _handle_toggle(self):
        if self._shutting_down:
            return
        if self._armed_retry is not None:
            self._confirm_retry()
            return
        if self._machine.state is State.LISTENING:
            self._stop_recording()
        elif self._machine.state is State.PROCESSING:
            self._cancel_session()
        else:
            if len(self._recoveries) >= self.MAX_RECOVERY_ITEMS:
                self._fail("Recovery is full. Open Recover text and copy or dismiss an entry before recording again.")
                return
            if self._pending_settings is not None:
                self._apply_pending_if_safe()
                if self._pending_settings is not None:
                    self.notice.emit("Waiting for previous work to finish before applying saved settings.")
                    return
            self._next_session += 1
            self._session = Session(self._next_session, copy.deepcopy(self._settings),
                                    self._target_provider())
            if getattr(self._session.target, "pid", None) == os.getpid():
                self._session = None
                self.notice.emit("Focus a destination outside Orpheus before starting dictation.")
                return
            try:
                self._capture.set_limit_callback(
                    lambda sid=self._session.id: self._limit_requested.emit(sid))
                self._capture.start(self._settings.input_device)
            except Exception as exc:
                self._session = None
                self._machine.on_finished()
                self._fail(f"Microphone unavailable: {exc}. Choose a microphone in Settings and try again.")
                self.state_changed.emit("error")
                return
            self._machine.state = State.LISTENING
            self.target_changed.emit(self._session.target)
            record_event("capture_started")
            minutes = self._settings.max_recording_seconds // 60
            self._set_status(f"Recording · up to {minutes} minutes · press hotkey to stop")
            self.state_changed.emit("listening")

    def _stop_recording(self):
        session = self._session
        if session is None:
            return
        try:
            audio = self._capture.stop()
        except AudioStopError as exc:
            audio = exc.audio
            self.notice.emit("Microphone cleanup failed; the captured audio will still be transcribed.")
        except Exception as exc:
            self._session = None
            self._machine.on_finished()
            self._fail(f"Could not stop recording: {exc}. Select a microphone in Settings and retry.")
            self.state_changed.emit("error")
            return
        self._machine.state = State.PROCESSING
        record_event("capture_stopped")
        self._pending_job = (session, audio)
        self._set_status("Waiting for previous work" if self._pipeline_active else "Transcribing")
        self.state_changed.emit("processing")
        self._launch_pending()

    def _launch_pending(self):
        if self._pipeline_active or self._pending_job is None or self._shutting_down:
            return
        session, audio = self._pending_job
        self._pending_job = None
        if session.cancel.is_set():
            return
        self._pipeline_active = True
        transcriber, cleanup = self._transcriber, self._cleanup
        injector, history = _SessionInjector(self._injector, session), self._history
        self._spawn(lambda: run_pipeline(
            audio, session.settings, transcriber, cleanup, injector, history,
            is_cancelled=session.cancel.is_set,
            on_stage=lambda stage: self._stage_requested.emit(session.id, stage)),
            partial(self._on_pipeline_done, session))

    def _cancel_session(self):
        if self._session is not None:
            self._session.cancel.set()
        self._session = None
        self._pending_job = None
        self._machine.on_finished()
        self._set_status("Cancelled")
        self.state_changed.emit("cancelled")
        self._apply_pending_if_safe()

    def _on_pipeline_done(self, session, result):
        self._pipeline_active = False
        if not self._shutting_down:
            if (isinstance(result, PipelineResult) and result.cancelled
                    and result.partial and result.final_text):
                # Cancellation cannot undo accepted keystrokes. Preserve the
                # remainder even if a newer session has already started.
                key = session.recovery_id or session.id
                self._recoveries[key] = RecoveryItem(
                    key, result.raw_text, result.final_text,
                    "Insertion was cancelled after some text may have been inserted.",
                    result.history_id, partial=True)
                self.recovery_changed.emit()
                self.notice.emit("Insertion was partly completed before cancellation. Open Recover text to review it.")
            if self._session is not None and self._session.id == session.id:
                self._session = None
                self._machine.on_finished()
                if isinstance(result, Exception):
                    self._fail("Dictation failed unexpectedly. Try again; check diagnostic logs for the failure category.")
                    self.state_changed.emit("error")
                elif not result.cancelled:
                    if result.final_text and (not result.ok or result.history_failed):
                        key = session.recovery_id or session.id
                        self._recoveries[key] = RecoveryItem(
                            key, result.raw_text, result.final_text,
                            result.error or result.notice or "History was not saved.",
                            result.history_id, result.partial)
                        self.recovery_changed.emit()
                    elif result.ok and session.recovery_id is not None:
                        self._remove_recovery(session.recovery_id)
                        self.recovery_changed.emit()
                    if result.notice:
                        self.notice.emit(result.notice)
                    if result.ok:
                        attention = bool(self.last_error or self._recoveries)
                        self._set_status("Needs attention · open Recover text" if attention else "Ready")
                        self.state_changed.emit("error" if attention else "done")
                    else:
                        self._fail(result.error or "Nothing was heard. Check your microphone and try again.",
                                   (session.recovery_id or session.id) if result.final_text else None)
                        self._set_status("Needs attention · open Recover text")
                        self.state_changed.emit("error")
        self._launch_pending()
        self._apply_pending_if_safe()

    def dismiss_recovery(self, item_id):
        if self._armed_retry == item_id:
            self._armed_retry = None
        self._remove_recovery(item_id)
        self.recovery_changed.emit()

    def _remove_recovery(self, item_id):
        removed = self._recoveries.pop(item_id, None)
        if removed is None:
            return
        if self._error_recovery_id == item_id:
            remaining = next(reversed(self._recoveries.values()), None)
            self.last_error = remaining.error if remaining else ""
            self._error_recovery_id = remaining.id if remaining else None
        if (self._machine.state is State.IDLE and not self._pipeline_active
                and not self._shutting_down and self._armed_retry is None):
            attention = bool(self.last_error or self._recoveries)
            self._set_status("Needs attention · open Recover text" if attention else "Ready")
            self.state_changed.emit("error" if attention else "idle")

    def arm_retry(self, item_id):
        if self._shutting_down or self._machine.state is not State.IDLE or self._pipeline_active:
            raise RuntimeError("Finish or cancel the current dictation before retrying.")
        if item_id not in self._recoveries:
            raise ValueError("This transcript is no longer available.")
        self._armed_retry = item_id
        self._set_status(f"Retry armed · focus a destination and press {self._settings.hotkey}")
        self.recovery_changed.emit()

    def cancel_retry(self):
        self._armed_retry = None
        self._set_status("Needs attention · open Recover text"
                         if self.last_error or self._recoveries else "Ready")
        self.recovery_changed.emit()

    def _confirm_retry(self):
        target = self._target_provider()
        if target is None or getattr(target, "pid", os.getpid()) == os.getpid():
            self.notice.emit("Focus a destination outside Orpheus, then press the hotkey to insert the recovered text.")
            return
        item = self._recoveries.get(self._armed_retry)
        if item is None:
            self.cancel_retry()
            return
        self._armed_retry = None
        self._next_session += 1
        session = Session(self._next_session, copy.deepcopy(self._settings), target,
                          recovery_id=item.id)
        self._session = session
        self._machine.state = State.PROCESSING
        self._pipeline_active = True
        injector = _SessionInjector(self._injector, session)
        def deliver():
            result = PipelineResult(ok=False, raw_text=item.raw_text,
                                    final_text=item.final_text, history_id=item.history_id)
            try:
                injector.inject(item.final_text)
                result.ok = True
            except Exception as exc:
                result.cancelled = bool(getattr(exc, "cancelled", False))
                result.partial = bool(getattr(exc, "partial", False))
                result.error = f"Could not insert recovered text: {exc}. Copy it from Recover text."
            return result
        self._set_status("Inserting recovered text")
        self.target_changed.emit(target)
        self.state_changed.emit("delivering")
        self._spawn(deliver, partial(self._on_pipeline_done, session))

    def shutdown(self):
        """Begin asynchronous shutdown; QApplication quits on shutdown_finished."""
        if self._shutting_down:
            return
        self._shutting_down = True
        record_event("shutdown_started")
        self._armed_retry = None
        if self._session is not None:
            self._session.cancel.set()
        self._session = None
        self._pending_job = None
        self._pending_settings = None
        self._machine.on_finished()
        self._set_status("Shutting down · waiting for current work")
        self.state_changed.emit("shutting_down")
        try:
            self._capture.stop()
        except Exception:
            log.error("microphone_shutdown_failed")
        self._finish_shutdown_if_safe()

    def _finish_shutdown_if_safe(self):
        if not self._shutting_down or self._threads or self._shutdown_complete:
            return
        self._shutdown_complete = True
        if self._history is not None:
            self._history.close()
            self._history = None
        self._cleanup.close()
        self._recoveries.clear()
        record_event("shutdown_completed")
        self.shutdown_finished.emit()
