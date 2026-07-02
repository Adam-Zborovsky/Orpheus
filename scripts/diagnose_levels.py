"""Phase-1 diagnostics for the dead visualizer: real RMS range + cross-thread delivery."""
import os
import statistics
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import sounddevice as sd

# --- Evidence 1: what RMS values does this machine's mic actually produce? ---
values = []


def cb(indata, frames, t, status):
    values.append(float(np.sqrt(np.mean(indata[:, 0] ** 2))))


with sd.InputStream(samplerate=16000, channels=1, dtype="float32", callback=cb):
    time.sleep(2.0)

print(f"callbacks: {len(values)}")
print(f"rms min/median/max: {min(values):.6f} / {statistics.median(values):.6f} / {max(values):.6f}")
print(f"old mapping (x8, bar px of 30): min {min(8*v for v in values)*30:.1f} "
      f"median {statistics.median(values)*8*30:.1f} max {max(values)*8*30:.1f}")

# --- Evidence 2: does a float signal emitted from a plain thread reach a slot? ---
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
received = []


class Emitter(QObject):
    sig = Signal(float)


class Receiver(QObject):
    def on_level(self, v):
        received.append(v)


emitter, receiver = Emitter(), Receiver()
emitter.sig.connect(receiver.on_level)
t = threading.Thread(target=lambda: [emitter.sig.emit(0.123) for _ in range(5)])
t.start()
t.join()
app.processEvents()
print(f"cross-thread delivery: sent 5, received {len(received)} -> {received[:2]}")
