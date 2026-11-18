import os
import threading
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QGuiApplication

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
    assert 0.4 < n.level(0.005) < 0.7


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
    assert 0.4 < last < 0.7  # quieter speech retains headroom


# --- pill behavior ------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_each_controller_state_has_a_readable_label(qapp):
    pill = PillOverlay(reduced_motion=True)
    pill.set_state("listening")
    assert pill._display_text() == ""
    assert pill.width() == 88
    assert pill.height() == 30
    pill.set_state("processing")
    assert pill._state == "transcribing"  # processing is an alias
    assert pill._display_text() == "Transcribing"
    pill.set_state("cleaning")
    assert pill._display_text() == "Cleaning up"
    pill.set_state("delivering")
    assert pill._display_text() == "Inserting text"
    pill.set_state("done")
    assert pill._display_text() == "Done"
    pill.set_state("cancelled")
    assert pill._display_text() == "Cancelled"
    pill.set_state("shutting_down")
    assert pill._display_text() == "Shutting down"
    pill.set_state("idle")


def test_error_is_concise_and_full_detail_and_recovery_route_are_available(qapp):
    pill = PillOverlay(reduced_motion=True)
    message = "Microphone unavailable: select another input device"
    pill.set_message(message)
    pill.set_state("error")
    assert pill._display_text() == "Needs attention"
    assert message in pill.toolTip()
    assert "Recover text from the tray" in pill.accessibleDescription()
    pill.set_state("idle")


def test_appear_animation_fades_in(qapp):
    pill = PillOverlay(reduced_motion=False)
    pill.set_state("listening")
    assert pill.isVisible()
    assert pill.windowOpacity() == 0.0  # starts transparent
    for _ in range(15):
        pill._tick()
    assert pill.windowOpacity() > 0.9  # fully faded in
    pill.set_state("idle")


def test_idle_plays_close_animation_then_hides(qapp):
    pill = PillOverlay(reduced_motion=False)
    pill.set_state("listening")
    for _ in range(15):
        pill._tick()
    pill.set_state("idle")
    assert pill.isVisible()  # not an instant hide: close animation runs
    assert pill._closing is True
    for _ in range(15):
        pill._tick()
    assert not pill.isVisible()


def test_bars_ease_toward_levels(qapp):
    pill = PillOverlay(reduced_motion=False)
    pill.set_state("listening")
    pill.set_level(0.02)  # loud -> target 1.0 at the newest bar
    before = pill._shown[-1]
    pill._tick()
    mid = pill._shown[-1]
    pill._tick()
    after = pill._shown[-1]
    assert before < mid < after <= 1.0  # smooth approach, not a jump
    pill.set_state("idle")


def test_cross_thread_level_delivery(qapp):
    from PySide6.QtCore import QObject, Signal

    class Emitter(QObject):
        sig = Signal(float)

    pill = PillOverlay(reduced_motion=True)
    pill.set_state("listening")
    emitter = Emitter()
    emitter.sig.connect(pill.set_level)
    thread = threading.Thread(
        target=lambda: [emitter.sig.emit(0.02) for _ in range(3)])
    thread.start()
    thread.join()
    qapp.processEvents()
    assert max(pill._levels) > 0.7  # loud samples must draw visibly tall bars
    pill.set_state("idle")


def test_accessible_name_updates_for_status_without_level_announcements(qapp, monkeypatch):
    from PySide6.QtGui import QAccessible

    announcements = []
    original = QAccessible.updateAccessibility
    monkeypatch.setattr(QAccessible, "updateAccessibility",
                        staticmethod(lambda event: announcements.append(event)))
    pill = PillOverlay(reduced_motion=True)
    pill.set_state("listening")
    assert pill.accessibleName() == "Orpheus status: Recording"
    state_events = len(announcements)
    pill.set_level(0.2)
    assert len(announcements) == state_events
    pill.set_state("transcribing")
    assert pill.accessibleName() == "Orpheus status: Transcribing"
    assert len(announcements) > state_events
    monkeypatch.setattr(QAccessible, "updateAccessibility", original)
    pill.set_state("idle")


def test_reduced_motion_uses_static_states_and_no_animation_timer(qapp):
    pill = PillOverlay(reduced_motion=True)
    pill.set_state("listening")
    assert pill.isVisible()
    assert pill.windowOpacity() == 1.0
    assert not pill._anim.isActive()
    pill.set_level(0.5)
    assert pill._shown[-1] == 1.0
    assert not pill._anim.isActive()
    pill.set_state("idle")
    assert not pill.isVisible()


def test_static_error_stops_entrance_timer_and_does_not_expire(qapp):
    pill = PillOverlay(reduced_motion=False)
    pill.set_message("Backend unavailable")
    pill.set_state("error")
    for _ in range(8):
        pill._tick()
    assert not pill._anim.isActive()
    assert pill.isVisible()
    assert pill._display_text() == "Needs attention"
    pill.set_state("idle")


def _luminance(color):
    values = []
    for value in (color.red(), color.green(), color.blue()):
        channel = value / 255
        values.append(channel / 12.92 if channel <= 0.04045
                      else ((channel + 0.055) / 1.055) ** 2.4)
    return sum(weight * value for weight, value in
               zip((0.2126, 0.7152, 0.0722), values))


def test_opaque_status_surface_keeps_small_text_contrast_on_light_and_dark_desktops(
        qapp):
    from orpheus.ui.pill import _SURFACE, _TEXT

    light_wallpaper = QColor(255, 255, 255)
    dark_wallpaper = QColor(0, 0, 0)
    assert _SURFACE.alpha() == 255
    for wallpaper in (light_wallpaper, dark_wallpaper):
        effective_surface = QColor(
            round(_SURFACE.red() * _SURFACE.alphaF()
                  + wallpaper.red() * (1 - _SURFACE.alphaF())),
            round(_SURFACE.green() * _SURFACE.alphaF()
                  + wallpaper.green() * (1 - _SURFACE.alphaF())),
            round(_SURFACE.blue() * _SURFACE.alphaF()
                  + wallpaper.blue() * (1 - _SURFACE.alphaF())),
        )
        high, low = sorted((_luminance(_TEXT), _luminance(effective_surface)),
                           reverse=True)
        assert (high + 0.05) / (low + 0.05) >= 4.5


class _FakeSignal:
    def connect(self, callback):
        self.callback = callback

    def disconnect(self, callback):
        if getattr(self, "callback", None) == callback:
            self.callback = None


class _FakeScreen:
    def __init__(self, rect):
        self.rect = rect
        self.availableGeometryChanged = _FakeSignal()

    def availableGeometry(self):
        return self.rect


def test_target_monitor_position_is_clamped_and_removed_monitor_falls_back(
        qapp, monkeypatch):
    primary = _FakeScreen(QRect(0, 0, 1280, 720))
    target = _FakeScreen(QRect(1280, 0, 300, 180))
    screens = [primary, target]
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: screens))
    monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: primary))

    pill = PillOverlay(reduced_motion=True)
    pill.set_target_screen(target)
    geometry = target.availableGeometry()
    assert geometry.left() <= pill.x()
    assert pill.x() + pill.width() - 1 <= geometry.right()
    assert geometry.top() <= pill.y()
    assert pill.y() + pill.height() - 1 <= geometry.bottom()

    screens.remove(target)
    pill._on_screen_removed(target)
    assert pill._target_screen is primary
    assert pill.x() + pill.width() - 1 <= primary.availableGeometry().right()


def test_missing_target_monitor_uses_primary_screen(qapp, monkeypatch):
    primary = _FakeScreen(QRect(0, 0, 640, 360))
    missing = _FakeScreen(QRect(3000, 0, 300, 180))
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: [primary]))
    monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: primary))
    pill = PillOverlay(reduced_motion=True)
    pill.set_target_screen(missing)
    assert pill._target_screen is primary
    assert primary.availableGeometry().left() <= pill.x()
    assert pill.x() + pill.width() <= primary.availableGeometry().right() + 1


def test_motion_preference_is_refreshed_between_states(qapp, monkeypatch):
    import orpheus.ui.pill as module

    enabled = {"value": True}
    monkeypatch.setattr(module, "_client_area_animations_enabled", lambda: enabled["value"])
    pill = PillOverlay()
    pill.set_state("listening")
    assert pill._anim.isActive()
    enabled["value"] = False
    pill.set_state("transcribing")
    assert not pill._anim.isActive()
    assert pill.windowOpacity() == 1.0
    pill.set_state("idle")


def test_sustained_speech_preserves_dynamics():
    normalizer = LevelNormalizer()
    samples = [0.001, 0.002, 0.004, 0.008, 0.016]
    levels = [normalizer.level(value) for value in samples]
    assert levels == sorted(set(levels))
    assert all(0 < value < 1 for value in levels)
    for _ in range(500):
        assert normalizer.level(0.004) == levels[2]
    assert normalizer.level(float("nan")) == 0
    assert normalizer.level(float("inf")) == 0


def test_transcribing_replaces_bars_with_glow(qapp, monkeypatch):
    pill = PillOverlay(reduced_motion=False)
    pill.set_state("listening")
    pill.set_level(0.01)
    pill.set_state("transcribing")
    assert pill.width() < pill.WIDTH
    assert pill._display_text() == "Transcribing"
    monkeypatch.setattr(pill, "_paint_bars", lambda *args: pytest.fail("busy state drew audio bars"))
    first = pill.grab().toImage()
    for _ in range(25):
        pill._tick()
    second = pill.grab().toImage()
    assert first != second
    pill.set_state("idle")
    pill._finish_close()


def test_status_size_uses_transcribing_and_preserves_font_size(qapp):
    from PySide6.QtGui import QFontMetricsF
    pill = PillOverlay(reduced_motion=True)
    pill.set_state("transcribing")
    width = pill.width()
    metrics = QFontMetricsF(pill._label_font())
    assert width == __import__("math").ceil(metrics.horizontalAdvance("Transcribing")) + 24
    assert pill._label_font().pointSizeF() == 9.0
    for state in ("cancelled", "done", "cleaning"):
        pill.set_state(state)
        assert pill.width() == width
    pill.set_state("error")
    assert pill.width() >= metrics.horizontalAdvance("Needs attention") + 36
    pill.set_state("idle")


def test_shimmer_changes_letters_only(qapp):
    from orpheus.ui.pill import _EDGE, _SURFACE
    pill = PillOverlay(reduced_motion=False)
    pill.set_state("transcribing")
    pill._appear_t = 1.0
    pill._t = 0.5
    first = pill.grab().toImage()
    pill._t = 1.3
    second = pill.grab().toImage()
    assert first != second
    for x in range(15, pill.width() - 15):
        for y in (4, pill.height() - 5):
            assert first.pixelColor(x, y) == second.pixelColor(x, y) == _SURFACE
    assert _EDGE.alpha() == 65
    pill._finish_close()
