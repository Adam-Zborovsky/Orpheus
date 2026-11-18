"""Finite Qt-controller lifecycle tests with isolated fake dependencies."""
from __future__ import annotations

import gc
import os
import time
import weakref
from threading import Event
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication

from orpheus import app as app_module
from orpheus.app import AppController, State, _WorkerThread
from orpheus.settings import Settings


class FakeCapture:
    def __init__(self):
        self.level_callback = None
        self.limit_callback = None
        self.started = 0
        self.stopped = 0
        self.recording = False
        self.max_seconds = None

    def start(self, device_name=""):
        self.started += 1
        self.recording = True

    def stop(self):
        self.stopped += 1
        self.recording = False
        return np.full(8, self.stopped, dtype=np.float32)

    def set_max_seconds(self, seconds):
        self.max_seconds = seconds

    def set_limit_callback(self, callback):
        self.limit_callback = callback


class FakeTranscriber:
    def __init__(self, behavior=None):
        self.behavior = behavior
        self.calls = []
        self.active_compute_type = "float32"

    def transcribe(self, audio, language_mode="auto", vocabulary=()):
        self.calls.append(np.array(audio, copy=True))
        if self.behavior is not None:
            return self.behavior(audio)
        return SimpleNamespace(text="hello world", language="en", duration_s=0.1)


class FakeCleanup:
    def __init__(self):
        self.closed = 0

    def run(self, raw, prompt, vocabulary):
        return SimpleNamespace(text=raw, used_llm=False, error=None)

    def close(self):
        self.closed += 1


class FakeInjector:
    def __init__(self):
        self.injected = []

    def inject(self, text, **kwargs):
        self.injected.append(text)


class FakeHistory:
    def __init__(self):
        self.rows = []
        self.closed = 0

    def add(self, raw_text, final_text, duration_s=0):
        self.rows.append((raw_text, final_text))
        return len(self.rows)

    def close(self):
        self.closed += 1


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump_until(qapp, predicate, timeout=3.0):
    """Process Qt and deferred deletion until predicate or a bounded deadline."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        QCore_send_deferred_deletes(qapp)
        if predicate():
            return True
    return predicate()


def QCore_send_deferred_deletes(qapp):
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def controller_factory(monkeypatch, tmp_path, qapp):
    made = []

    def make(*, transcriber=None, history=False):
        dependencies = {}

        def build(controller):
            dependencies["transcriber"] = transcriber or FakeTranscriber()
            dependencies["cleanup"] = FakeCleanup()
            dependencies["injector"] = FakeInjector()
            dependencies["history"] = FakeHistory() if history else None
            controller._transcriber = dependencies["transcriber"]
            controller._cleanup = dependencies["cleanup"]
            controller._injector = dependencies["injector"]
            controller._history = dependencies["history"]

        monkeypatch.setattr(AppController, "_build_components", build)
        capture = FakeCapture()
        controller = AppController(
            Settings(cleanup_enabled=False, history_enabled=history),
            data_dir=tmp_path,
            target_provider=lambda: SimpleNamespace(hwnd=1, pid=999_999),
        )
        controller._capture = capture
        capture.limit_callback = controller._capture_limit
        record = {"controller": controller, "capture": capture, **dependencies}
        made.append(record)
        return record

    yield make

    for record in made:
        controller = record["controller"]
        if not controller._shutdown_complete:
            controller.shutdown()
        assert pump_until(qapp, lambda: controller._shutdown_complete), (
            "controller workers did not finish during fixture cleanup"
        )


def start_recording(controller, qapp):
    controller.toggle()
    assert pump_until(qapp, lambda: controller._machine.state is State.LISTENING)


def stop_recording(controller, qapp):
    controller.toggle()
    assert pump_until(qapp, lambda: controller._machine.state is State.PROCESSING)


def test_one_hundred_short_workers_are_disposed(qapp, controller_factory):
    record = controller_factory()
    controller = record["controller"]
    refs = []

    for _ in range(100):
        completed = Event()
        worker = controller._spawn(lambda: 1, lambda result: completed.set())
        refs.append(weakref.ref(worker))
        del worker
        assert pump_until(qapp, lambda: completed.is_set() and not controller._threads)
        assert pump_until(qapp, lambda: not controller.findChildren(_WorkerThread))

    gc.collect()
    assert not controller.findChildren(_WorkerThread)
    assert all(ref() is None for ref in refs)


@pytest.mark.parametrize("outcome", ["empty", "error"])
def test_cancelled_session_outcomes_and_stale_stage_do_not_reset_new_recording(
        qapp, controller_factory, outcome):
    entered_a, release_a = Event(), Event()

    def transcribe(audio):
        if audio[0] == 1:
            entered_a.set()
            assert release_a.wait(2), "test did not release blocked session A"
            if outcome == "error":
                raise RuntimeError("late A failure")
            return SimpleNamespace(text="", language="en", duration_s=0.1)
        return SimpleNamespace(text="B transcript", language="en", duration_s=0.1)

    record = controller_factory(transcriber=FakeTranscriber(transcribe))
    controller = record["controller"]
    start_recording(controller, qapp)
    session_a = controller._session.id
    stop_recording(controller, qapp)
    assert entered_a.wait(2)

    try:
        controller.toggle()  # cancel A
        assert pump_until(qapp, lambda: controller._session is None)
        start_recording(controller, qapp)
        session_b = controller._session.id
        assert session_b != session_a
        status_b = controller.status

        controller._stage_requested.emit(session_a, "cleaning")
        qapp.processEvents()
        assert controller.status == status_b
    finally:
        release_a.set()

    assert pump_until(qapp, lambda: not controller._pipeline_active)
    assert controller._session is not None and controller._session.id == session_b
    assert controller._machine.state is State.LISTENING
    assert controller.status == status_b


def test_pipeline_concurrency_is_one_active_and_one_latest_pending(qapp, controller_factory):
    entered_a, release_a, entered_c = Event(), Event(), Event()

    def transcribe(audio):
        marker = int(audio[0])
        if marker == 1:
            entered_a.set()
            assert release_a.wait(2), "test did not release active session A"
        if marker == 3:
            entered_c.set()
        return SimpleNamespace(text=f"session {marker}", language="en", duration_s=0.1)

    record = controller_factory(transcriber=FakeTranscriber(transcribe))
    controller = record["controller"]
    start_recording(controller, qapp)
    stop_recording(controller, qapp)
    assert entered_a.wait(2)

    try:
        controller.toggle()             # cancel A, while its backend call unwinds
        start_recording(controller, qapp)
        stop_recording(controller, qapp)  # B becomes pending
        assert controller._pipeline_active
        assert controller._pending_job is not None
        controller.toggle()             # cancel and discard pending B
        start_recording(controller, qapp)  # C replaces B
        stop_recording(controller, qapp)
        assert controller._pending_job[0].id == controller._session.id
        assert len([w for w in controller._threads if w.isRunning()]) == 1
        assert len(record["transcriber"].calls) == 1
    finally:
        release_a.set()

    assert pump_until(qapp, entered_c.is_set)
    assert pump_until(qapp, lambda: not controller._pipeline_active
                      and not controller._threads)
    assert [int(call[0]) for call in record["transcriber"].calls] == [1, 3]


def test_settings_wait_until_worker_releases_old_cleanup_and_history(
        monkeypatch, qapp, controller_factory):
    record = controller_factory(history=True)
    controller = record["controller"]
    old_cleanup, old_history = controller._cleanup, controller._history
    entered, release = Event(), Event()

    def use_resources():
        assert controller._cleanup is old_cleanup
        assert controller._history is old_history
        entered.set()
        assert release.wait(2), "test did not release settings consumer"
        return old_cleanup.closed, old_history.closed

    completed = Event()
    controller._spawn(use_resources, lambda result: completed.set())
    assert entered.wait(2)
    updated = Settings(cleanup_enabled=False, history_enabled=False,
                       max_recording_seconds=1200)
    controller.apply_settings(updated)
    assert controller.pending_settings
    assert old_cleanup.closed == old_history.closed == 0

    class ReplacementCleanup(FakeCleanup):
        def __init__(self, provider):
            super().__init__()

    monkeypatch.setattr(app_module, "OllamaProvider", lambda *args: object())
    monkeypatch.setattr(app_module, "Cleanup", ReplacementCleanup)
    monkeypatch.setattr(app_module, "TextInjector", lambda *args: FakeInjector())
    release.set()
    assert pump_until(qapp, lambda: completed.is_set() and not controller._threads
                      and not controller.pending_settings)
    assert old_cleanup.closed == 1
    assert old_history.closed == 1
    assert record["capture"].max_seconds == 1200


def test_limit_signal_stops_current_recording_once_and_ignores_late_signal(
        qapp, controller_factory):
    record = controller_factory()
    controller, capture = record["controller"], record["capture"]
    start_recording(controller, qapp)
    session_id = controller._session.id
    capture.limit_callback()
    capture.limit_callback()
    assert pump_until(qapp, lambda: not controller._pipeline_active
                      and not controller._threads)
    assert capture.stopped == 1
    assert len(record["transcriber"].calls) == 1
    controller._limit_requested.emit(session_id)
    qapp.processEvents()
    assert capture.stopped == 1


def test_shutdown_keeps_qt_responsive_and_resources_alive_until_worker_finishes(
        qapp, controller_factory):
    entered, release = Event(), Event()

    def blocked_transcription(audio):
        entered.set()
        assert release.wait(2), "test did not release shutdown backend call"
        return SimpleNamespace(text="must not deliver", language="en", duration_s=0.1)

    record = controller_factory(transcriber=FakeTranscriber(blocked_transcription), history=True)
    controller = record["controller"]
    cleanup, history, injector = controller._cleanup, controller._history, controller._injector
    start_recording(controller, qapp)
    stop_recording(controller, qapp)
    assert entered.wait(2)

    shutdown_events = []
    controller.shutdown_finished.connect(lambda: shutdown_events.append("done"))
    heartbeat = []
    timer = QTimer()
    timer.setInterval(0)
    timer.timeout.connect(lambda: heartbeat.append(1))
    timer.start()
    try:
        controller.shutdown()
        assert not shutdown_events
        assert cleanup.closed == history.closed == 0
        assert pump_until(qapp, lambda: len(heartbeat) >= 3)
        assert not shutdown_events
        assert injector.injected == []
    finally:
        release.set()

    assert pump_until(qapp, lambda: shutdown_events == ["done"])
    timer.stop()
    assert cleanup.closed == 1
    assert history.closed == 1
    assert injector.injected == []


def test_failed_delivery_is_recoverable_without_history_or_disk(qapp, controller_factory, tmp_path):
    record = controller_factory()
    controller = record["controller"]

    def blocked(text, **kwargs):
        raise RuntimeError("blocked")

    record["injector"].inject = blocked
    start_recording(controller, qapp)
    stop_recording(controller, qapp)
    assert pump_until(qapp, lambda: not controller._threads)
    assert controller.recoveries[0].final_text == "hello world"
    assert controller.recoveries[0].history_id is None
    assert list(tmp_path.iterdir()) == []


def test_retry_requires_destination_confirmation_and_does_not_duplicate_history(qapp, controller_factory):
    record = controller_factory(history=True)
    controller = record["controller"]
    injector = record["injector"]
    original = injector.inject
    injector.inject = lambda text, **kwargs: (_ for _ in ()).throw(RuntimeError("blocked"))
    start_recording(controller, qapp)
    stop_recording(controller, qapp)
    assert pump_until(qapp, lambda: not controller._threads)
    item = controller.recoveries[0]
    assert len(record["history"].rows) == 1
    injector.inject = original
    controller.arm_retry(item.id)
    assert injector.injected == []
    controller.toggle()
    assert pump_until(qapp, lambda: not controller._threads)
    assert injector.injected == ["hello world"]
    assert len(record["history"].rows) == 1
    assert controller.recoveries == []
    assert controller.last_error == ""
    assert controller.status == "Ready"


@pytest.mark.parametrize("active", [False, True])
def test_copy_and_dismiss_resolves_pill_tray_and_error(qapp, controller_factory, active):
    from orpheus.ui.pill import PillOverlay
    from orpheus.ui.recovery_window import RecoveryWindow
    from orpheus.ui.tray import TrayIcon

    record = controller_factory()
    controller = record["controller"]
    record["injector"].inject = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked"))
    pill = PillOverlay(reduced_motion=True)
    tray = TrayIcon()
    controller.state_changed.connect(pill.set_state)
    controller.status_changed.connect(tray.set_status)
    controller.recovery_changed.connect(lambda: tray.set_recovery_count(len(controller.recoveries)))
    start_recording(controller, qapp)
    stop_recording(controller, qapp)
    assert pump_until(qapp, lambda: not controller._threads)
    assert pill._state == "error"
    window = RecoveryWindow(controller)
    window._copy.click()
    assert qapp.clipboard().text() == "hello world"
    assert len(controller.recoveries) == 1
    if active:
        start_recording(controller, qapp)
    window._discard_confirm.setChecked(True)
    window._dismiss.click()
    assert controller.last_error == ""
    assert controller.recoveries == []
    assert "(1)" not in tray._recovery_action.text()
    assert not window._copy.isEnabled()
    assert not window._dismiss.isEnabled()
    if active:
        assert pill._state == "listening"
        assert controller.status.startswith("Recording")
    else:
        assert not pill.isVisible()
        assert controller.status == "Ready"
        assert tray._status_action.text() == "Ready"
        assert window._status.text() == "No errors need attention."
    pill.hide()


def test_unidentified_target_never_delivers(qapp, controller_factory):
    record = controller_factory()
    controller = record["controller"]
    controller._target_provider = lambda: None
    start_recording(controller, qapp)
    stop_recording(controller, qapp)
    assert pump_until(qapp, lambda: not controller._threads)
    assert record["injector"].injected == []
    assert len(controller.recoveries) == 1


def test_partial_cancel_preserves_recovery_without_resetting_new_session(qapp, controller_factory):
    from orpheus.app import PipelineResult

    record = controller_factory()
    controller = record["controller"]
    start_recording(controller, qapp)
    old_session = controller._session
    controller._cancel_session()
    start_recording(controller, qapp)
    current = controller._session.id
    controller._on_pipeline_done(old_session, PipelineResult(
        ok=False, cancelled=True, partial=True, raw_text="raw", final_text="complete text"))
    assert controller._session.id == current
    assert controller._machine.state is State.LISTENING
    assert controller.recoveries[0].final_text == "complete text"
    assert controller.recoveries[0].partial


def test_rejected_pending_settings_clear_and_report_active_configuration(qapp, controller_factory, monkeypatch):
    record = controller_factory()
    controller = record["controller"]
    active_hotkey = controller._settings.hotkey
    entered, release = Event(), Event()
    controller._spawn(lambda: (entered.set(), release.wait(2)), lambda result: None)
    assert entered.wait(2)
    rejected = []
    controller.settings_failed.connect(lambda settings, reason: rejected.append(settings))
    controller.apply_settings(Settings(hotkey="<f9>", cleanup_enabled=False, history_enabled=False))
    monkeypatch.setattr(controller, "_apply_settings_now",
                        lambda settings: (_ for _ in ()).throw(RuntimeError("registration failed")))
    release.set()
    assert pump_until(qapp, lambda: not controller._threads)
    assert not controller.pending_settings
    assert rejected[0].hotkey == active_hotkey
    assert controller._settings.hotkey == active_hotkey


def test_complete_preload_cancel_recovery_settings_and_quit_flow(qapp, controller_factory, monkeypatch):
    from orpheus.injector import DeliveryError

    entered, release = Event(), Event()

    def transcribe(audio):
        if audio[0] == 1:
            entered.set()
            assert release.wait(2)
            return SimpleNamespace(text="")
        return SimpleNamespace(text="preserved B text")

    transcriber = FakeTranscriber(transcribe)
    transcriber.load = lambda: "cpu"
    record = controller_factory(transcriber=transcriber, history=True)
    controller = record["controller"]
    controller.preload()
    assert pump_until(qapp, lambda: not controller._threads)
    assert controller.status == "Ready"
    start_recording(controller, qapp)
    stop_recording(controller, qapp)
    assert entered.wait(2)
    try:
        controller.toggle()
        start_recording(controller, qapp)
        controller.apply_settings(Settings(hotkey="<f9>", cleanup_enabled=False, history_enabled=False))
        assert controller.pending_settings
        record["injector"].inject = lambda text, **kwargs: (_ for _ in ()).throw(DeliveryError("Delivery target changed"))
        stop_recording(controller, qapp)
        assert controller._pending_job is not None

        class ReplacementCleanup(FakeCleanup):
            def __init__(self, provider):
                super().__init__()

        monkeypatch.setattr(app_module, "OllamaProvider", lambda *args: object())
        monkeypatch.setattr(app_module, "Cleanup", ReplacementCleanup)
        monkeypatch.setattr(app_module, "TextInjector", lambda *args: FakeInjector())
    finally:
        release.set()
    assert pump_until(qapp, lambda: not controller._threads and not controller.pending_settings)
    assert controller.recoveries[0].final_text == "preserved B text"
    assert record["history"].rows == [("preserved B text", "preserved B text")]
    assert record["history"].closed == 1
    assert controller._settings.hotkey == "<f9>"
    controller.shutdown()
    assert controller._shutdown_complete
    assert record["injector"].injected == []


def test_dismiss_preserves_other_recoveries_and_unrelated_errors(qapp, controller_factory):
    record = controller_factory()
    controller = record["controller"]
    record["injector"].inject = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked"))
    for _ in range(2):
        start_recording(controller, qapp)
        stop_recording(controller, qapp)
        assert pump_until(qapp, lambda: not controller._threads)
    newest, oldest = controller.recoveries
    controller.dismiss_recovery(newest.id)
    assert controller.last_error == oldest.error
    assert controller.status.startswith("Needs attention")
    controller._fail("Microphone unavailable")
    controller.dismiss_recovery(oldest.id)
    assert controller.last_error == "Microphone unavailable"
    assert controller.status.startswith("Needs attention")
