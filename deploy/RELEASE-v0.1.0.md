# Orpheus v0.1.0

Windows x64 portable release. Download **Orpheus.exe** and run it; Python is
included. The application lives in the system tray. The default recording
shortcut is **Ctrl+Alt+Space**; existing settings and custom shortcuts are retained.

## Changes

- Compact recording pill with a visualizer and more gradual volume response.
- Text states sized around Transcribing, a softer border, and a shimmer confined
  to the Transcribing letters. Reduced-motion preferences are respected.
- Dismissing recovered text and successfully retrying insertion clear the
  associated error state in the pill and tray. Other recoveries remain available.
- Full recordings up to 30 minutes, guarded text delivery, recovery, searchable
  history, and native Windows settings.

## Running the EXE

- Windows x64 is required. No separate Python installation is needed.
- Whisper model weights are not bundled. The first model load needs internet
  access unless the selected model is already cached. CPU transcription is
  supported; NVIDIA acceleration requires compatible CUDA runtime libraries.
- Optional cleanup requires a separately running Ollama instance and model.
- Quit any source-based or older Orpheus instance before launching the EXE to
  avoid duplicate global shortcuts.
- Configuration and history use `%APPDATA%\Orpheus`; existing data is preserved.
- To start at login, create a shortcut to the downloaded EXE in `shell:startup`.
  This EXE does not install or change Startup automatically.
- This build is unsigned. Windows may show an unknown-publisher prompt.
- Verify the download with the accompanying `SHA256SUMS.txt`.

## Validation

The source test suite passes. The packaged executable is checked separately
for Qt rendering, icon loading, PortAudio, and native transcription dependencies.
Microphone transcription and insertion were tested by the maintainer in the
source-based application; the packaged EXE still needs those real-device checks.
