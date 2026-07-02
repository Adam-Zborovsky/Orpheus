import numpy as np
import pytest

from orpheus.audio import AudioCapture, RingBuffer, rms


def test_rms_silence_is_zero():
    assert rms(np.zeros(100, dtype=np.float32)) == 0.0


def test_rms_full_scale():
    assert rms(np.ones(100, dtype=np.float32)) == pytest.approx(1.0)


def test_rms_empty_is_zero():
    assert rms(np.zeros(0, dtype=np.float32)) == 0.0


def test_ring_buffer_concatenates():
    buf = RingBuffer(max_seconds=1.0, samplerate=10)
    buf.append(np.array([1, 2], dtype=np.float32))
    buf.append(np.array([3], dtype=np.float32))
    assert buf.get().tolist() == [1, 2, 3]


def test_ring_buffer_drops_oldest_past_cap():
    buf = RingBuffer(max_seconds=0.5, samplerate=10)  # cap = 5 samples
    buf.append(np.array([1, 2, 3], dtype=np.float32))
    buf.append(np.array([4, 5, 6], dtype=np.float32))  # 6 > 5: drop first chunk
    assert buf.get().tolist() == [4, 5, 6]


def test_ring_buffer_clear():
    buf = RingBuffer()
    buf.append(np.ones(10, dtype=np.float32))
    buf.clear()
    assert buf.get().size == 0


def test_callback_appends_mono_and_reports_level():
    levels = []
    capture = AudioCapture(level_callback=levels.append)
    frames = np.full((160, 1), 0.5, dtype=np.float32)  # 2D like PortAudio gives
    capture._callback(frames, 160, None, None)
    audio = capture._buffer.get()
    assert audio.shape == (160,)
    assert audio[0] == pytest.approx(0.5)
    assert levels == [pytest.approx(0.5)]


def test_stop_without_start_returns_buffer():
    capture = AudioCapture()
    assert capture.stop().size == 0
