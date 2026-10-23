import sys
from types import ModuleType

import numpy as np
import pytest

from orpheus.audio import AudioCapture, AudioStopError, RingBuffer, rms


class FakeStream:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.device = kwargs["device"]
        self.started = False
        self.closed = False
        self.fail_start = False
        self.fail_stop = False
        self.fail_close = False

    def start(self):
        self.started = True
        if self.fail_start:
            raise RuntimeError("start failed")

    def stop(self):
        if self.fail_stop:
            raise RuntimeError("stop failed")

    def close(self):
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")

    def emit(self, samples):
        data = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        self.callback(data, len(data), None, None)


class FakeSoundDevice(ModuleType):
    def __init__(self, devices=None):
        super().__init__("sounddevice")
        self.devices = devices or [{"name": "Mic", "max_input_channels": 1}]
        self.streams = []
        self.fail_next_start = False

    def query_devices(self):
        return self.devices

    def InputStream(self, **kwargs):
        stream = FakeStream(**kwargs)
        stream.fail_start = self.fail_next_start
        self.fail_next_start = False
        self.streams.append(stream)
        return stream


def fake_sounddevice(monkeypatch, devices=None):
    sd = FakeSoundDevice(devices)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    return sd


def test_rms_silence_is_zero():
    assert rms(np.zeros(100, dtype=np.float32)) == 0.0


def test_rms_full_scale():
    assert rms(np.ones(100, dtype=np.float32)) == pytest.approx(1.0)


def test_rms_empty_is_zero():
    assert rms(np.zeros(0, dtype=np.float32)) == 0.0


def test_ring_buffer_concatenates():
    buf = RingBuffer(max_seconds=1.0, samplerate=10)
    assert not buf.append(np.array([1, 2], dtype=np.float32))
    assert not buf.append(np.array([3], dtype=np.float32))
    assert buf.get().tolist() == [1, 2, 3]


def test_ring_buffer_retains_first_samples_and_truncates_at_cap():
    buf = RingBuffer(max_seconds=0.5, samplerate=10)  # cap = 5 samples
    assert not buf.append(np.array([1, 2, 3], dtype=np.float32))
    assert buf.append(np.array([4, 5, 6], dtype=np.float32))
    assert not buf.append(np.array([7, 8], dtype=np.float32))
    assert buf.get().tolist() == [1, 2, 3, 4, 5]


def test_ring_buffer_clear():
    buf = RingBuffer(max_seconds=1.0, samplerate=10)
    buf.append(np.ones(10, dtype=np.float32))
    buf.clear()
    assert buf.get().size == 0
    assert buf.append(np.ones(1, dtype=np.float32)) is False


def test_ring_buffer_duration_is_seconds_at_scaled_sample_rate():
    buf = RingBuffer(max_seconds=2.0, samplerate=10)
    buf.append(np.ones(7, dtype=np.float32))
    assert buf.duration_s == pytest.approx(0.7)


def test_callback_appends_mono_reports_level_and_duration(monkeypatch):
    sd = fake_sounddevice(monkeypatch)
    levels = []
    capture = AudioCapture(samplerate=10, level_callback=levels.append,
                           max_seconds=2.0)
    capture.start()
    frames = np.full((4, 1), 0.5, dtype=np.float32)
    sd.streams[0].callback(frames, 4, None, None)
    assert capture._buffer.get().tolist() == pytest.approx([0.5] * 4)
    assert levels == [pytest.approx(0.5)]
    assert capture.duration_s == pytest.approx(0.4)


def test_default_device_is_system_default(monkeypatch):
    sd = fake_sounddevice(monkeypatch)
    capture = AudioCapture(max_seconds=1)
    capture.start()
    assert sd.streams[0].device is None
    capture.stop()


def test_configured_missing_device_is_actionable_and_does_not_leak_active_state(monkeypatch):
    fake_sounddevice(monkeypatch)
    capture = AudioCapture(max_seconds=1)
    with pytest.raises(RuntimeError, match="Configured microphone 'Missing' is unavailable"):
        capture.start("Missing")
    assert not capture.is_recording


def test_failed_start_closes_clears_and_retry_creates_fresh_stream(monkeypatch):
    sd = fake_sounddevice(monkeypatch)
    sd.fail_next_start = True
    capture = AudioCapture(samplerate=10, max_seconds=1)
    with pytest.raises(RuntimeError, match="start failed"):
        capture.start()
    first = sd.streams[0]
    assert first.closed
    assert not capture.is_recording
    assert capture.duration_s == 0

    capture.start()
    assert len(sd.streams) == 2
    assert sd.streams[1].started
    capture.stop()


def test_limit_callback_fires_once_and_capture_ignores_late_data(monkeypatch):
    sd = fake_sounddevice(monkeypatch)
    limits = []
    capture = AudioCapture(samplerate=10, max_seconds=0.7,
                           limit_callback=lambda: limits.append("limit"))
    capture.start()
    stream = sd.streams[0]
    stream.emit([1, 2, 3, 4])
    stream.emit([5, 6, 7, 8])  # crosses seven-sample boundary
    stream.emit([9, 10])
    assert limits == ["limit"]
    assert capture.duration_s == pytest.approx(0.7)
    assert capture.is_recording  # callback only signals; Qt owner stops it
    audio = capture.stop()
    assert audio.tolist() == pytest.approx([1, 2, 3, 4, 5, 6, 7])
    assert capture.duration_s == 0
    assert stream.closed


def test_limit_callback_fires_at_exact_boundary(monkeypatch):
    sd = fake_sounddevice(monkeypatch)
    limits = []
    capture = AudioCapture(samplerate=10, max_seconds=0.7,
                           limit_callback=lambda: limits.append(True))
    capture.start()
    sd.streams[0].emit(range(7))
    assert limits == [True]
    assert capture.stop().tolist() == list(map(float, range(7)))


def test_old_stream_callback_cannot_append_to_new_recording(monkeypatch):
    sd = fake_sounddevice(monkeypatch)
    capture = AudioCapture(samplerate=10, max_seconds=2)
    capture.start()
    old_stream = sd.streams[0]
    capture.stop()
    capture.start()
    new_stream = sd.streams[1]
    new_stream.emit([4, 5])
    old_stream.emit([9, 9])
    assert capture.stop().tolist() == [4, 5]


def test_stop_failure_still_closes_resets_and_releases_buffer(monkeypatch):
    sd = fake_sounddevice(monkeypatch)
    capture = AudioCapture(samplerate=10, max_seconds=2)
    capture.start()
    stream = sd.streams[0]
    stream.emit([1, 2])
    stream.fail_stop = True
    with pytest.raises(AudioStopError, match="Could not stop microphone cleanly: stop failed") as error:
        capture.stop()
    assert stream.closed
    assert not capture.is_recording
    assert capture.duration_s == 0
    assert error.value.audio.tolist() == [1, 2]

    capture.start()
    sd.streams[1].emit([3, 4])
    assert capture.stop().tolist() == [3, 4]


def test_stop_without_start_returns_empty_and_is_idempotent():
    capture = AudioCapture()
    assert capture.stop().size == 0
    assert capture.stop().size == 0


def test_set_max_seconds_only_while_stopped(monkeypatch):
    sd = fake_sounddevice(monkeypatch)
    capture = AudioCapture(samplerate=10, max_seconds=2)
    capture.set_max_seconds(0.3)
    capture.start()
    with pytest.raises(RuntimeError, match="while recording"):
        capture.set_max_seconds(1)
    sd.streams[0].emit(range(5))
    assert capture.stop().tolist() == [0, 1, 2]


def test_limit_notification_keeps_original_callback_across_stop_start_race(monkeypatch):
    from threading import Event, Thread

    sd = fake_sounddevice(monkeypatch)
    entered, release = Event(), Event()
    notifications = []

    def pause_after_samples(level):
        entered.set()
        assert release.wait(2)

    capture = AudioCapture(samplerate=10, max_seconds=0.1,
                           level_callback=pause_after_samples,
                           limit_callback=lambda: notifications.append("old"))
    capture.start()
    old_stream = sd.streams[0]
    worker = Thread(target=lambda: old_stream.emit([1]))
    worker.start()
    assert entered.wait(2)
    try:
        capture.stop()
        capture.set_limit_callback(lambda: notifications.append("new"))
        capture.start()
    finally:
        release.set()
        worker.join(2)
        capture.stop()
    assert not worker.is_alive()
    assert notifications == ["old"]
