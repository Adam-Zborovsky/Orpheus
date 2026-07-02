# Orpheus — Local Voice Dictation

Private, self-hosted dictation for Windows. Tap a global hotkey, speak, tap
again: Whisper transcribes locally, a local LLM polishes the text, and the
result is typed into whatever app has focus. Nothing leaves your machine.

## Requirements

- Windows 11, Python 3.12 (ctranslate2 wheels don't cover 3.14 yet)
- [Ollama](https://ollama.com) running locally for LLM cleanup (optional —
  without it the raw transcript is injected)
- NVIDIA GPU for CUDA acceleration (optional — falls back to CPU int8
  automatically; pick a smaller model like `small`/`medium` for speed on CPU)

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
ollama pull llama3.1:8b   # or any model; set it in Settings
```

First run downloads the Whisper model (~3 GB for large-v3) to the Hugging Face
cache.

## Run

```powershell
.\.venv\Scripts\python -m orpheus
```

- Tap `Ctrl+Alt+Space` to start dictating (pill shows the live level).
- Tap again to stop; the polished text is typed into the focused app.
- Right-click the tray icon for Settings / Quit.

Config lives at `%APPDATA%\Orpheus\config.toml`; history at
`%APPDATA%\Orpheus\history.sqlite3`.

## Tests

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

## Notes

- On AMD GPUs (no CUDA) transcription runs on CPU int8 — a tray notice says
  so at startup. A Vulkan/whisper.cpp backend is a possible v2 swap.
- Text is injected with SendInput Unicode events; switch to clipboard-paste in
  Settings for apps that reject synthetic keystrokes.
