import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from orpheus.ui.pill import LevelNormalizer, PillOverlay


# --- auto-gain normalization (root-cause fix for the dead visualizer) --------

def test_normalizer_zero_is_zero():
    assert LevelNormalizer().level(0.0) == 0.0


def test_normalizer_quiet_ambient_stays_low():
    n = LevelNormalizer()
    # 7e-5 is the measured ambient RMS on this machine (scripts/diagnose_levels.py)
    levels = [n.level(0.00007) for _ in range(100)]
    assert max(levels) < 0.25


def test_normalizer_speech_reaches_full_scale():
    n = LevelNormalizer()
    for _ in range(50):
        n.level(0.00007)
    assert n.level(0.005) == 1.0  # a loud sample defines the peak -> full bar


def test_normalizer_tracks_relative_dynamics():
    n = LevelNormalizer()
    n.level(0.01)  # loud peak
    mid = n.level(0.004)
    assert 0.3 < mid < 0.9


def test_normalizer_recovers_after_loud_burst():
    n = LevelNormalizer()
    n.level(0.5)  # clap / very loud burst
    last = 0.0
    for _ in range(400):
        last = n.level(0.004)  # sustained quieter speech
    assert last > 0.9  # peak decays; bars must come back to life


# --- pill behavior ------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_only_done_and_error_carry_text(qapp):
    pill = PillOverlay()
    pill.set_state("listening")
    assert pill._display_text() == ""
    pill.set_state("processing")
    assert pill._display_text() == ""  # animation only, no "Transcribing" text
    pill.set_state("done")
    assert pill._display_text() == "Done"
    pill.set_state("idle")


def test_error_message_shown(qapp):
    pill = PillOverlay()
    pill.set_message("Microphone unavailable")
    pill.set_state("error")
    assert pill._display_text() == "Microphone unavailable"
    pill.set_state("idle")


def test_cross_thread_level_delivery(qapp):
    from PySide6.QtCore import QObject, Signal

    class Emitter(QObject):
        sig = Signal(float)

    pill = PillOverlay()
    pill.set_state("listening")
    emitter = Emitter()
    emitter.sig.connect(pill.set_level)
    thread = threading.Thread(
        target=lambda: [emitter.sig.emit(0.02) for _ in range(3)])
    thread.start()
    thread.join()
    qapp.processEvents()
    assert max(pill._levels) > 0.9  # loud samples must draw visibly tall bars
    pill.set_state("idle")
