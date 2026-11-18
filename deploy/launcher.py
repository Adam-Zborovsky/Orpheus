"""Entry point for the frozen Windows executable."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def smoke_test(destination: Path) -> int:
    """Exercise bundled native dependencies without hotkeys or user data."""
    import av
    import ctranslate2
    import faster_whisper
    import sounddevice
    import tokenizers
    from PySide6.QtWidgets import QApplication
    from orpheus.ui.pill import PillOverlay
    from orpheus.ui.tray import app_icon

    app = QApplication([])
    pill = PillOverlay(reduced_motion=True)
    states = []
    for state in ("listening", "transcribing", "cancelled", "error"):
        pill.set_state(state)
        app.processEvents()
        if pill.grab().isNull():
            raise RuntimeError(f"Could not render {state}")
        states.append(state)
    pill.set_state("idle")
    if app_icon().isNull():
        raise RuntimeError("Application icon is missing")
    # Querying the library version loads PortAudio without opening a microphone.
    portaudio = sounddevice.get_portaudio_version()
    destination.write_text(json.dumps({
        "ok": True, "states": states, "portaudio": portaudio,
        "cpu_compute_types": sorted(ctranslate2.get_supported_compute_types("cpu")),
        "faster_whisper": faster_whisper.__version__,
        "av": av.__version__, "tokenizers": tokenizers.__version__,
    }), encoding="utf-8")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        raise SystemExit(smoke_test(Path(sys.argv[2])))
    from orpheus.__main__ import main

    raise SystemExit(main())
