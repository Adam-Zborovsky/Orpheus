# Orpheus — Local GPU Voice Dictation

A private, self-hosted alternative to Wispr Flow. Global hotkey → capture mic →
local Whisper transcription on the GPU → local LLM cleanup → type the polished
text into whatever app has focus. A small floating pill with a voice visualizer
shows state. Everything runs on-machine; nothing is sent to the cloud by default.

Status: **design** (2026-07-02). Visual/aesthetic design deliberately deferred —
the pill is specified functionally only; a later pass picks the look.

---

## Confirmed decisions

| Area | Decision |
|---|---|
| Tech stack | Python 3.11+ / PySide6 (Qt), single language end-to-end |
| STT engine | faster-whisper (CTranslate2) — Whisper large-v3 on CUDA |
| LLM cleanup | Local via Ollama (`localhost:11434`), behind a provider interface |
| Trigger mode | Toggle — tap hotkey to start, tap again to stop |
| Text delivery | SendInput Unicode type-out; clipboard-paste as fallback |
| Language | English + Hebrew (Whisper auto-detect, with force option) |
| v1 extras | Custom vocabulary/dictionary, editable cleanup prompts, history log |
| Deferred to v2 | Multiple modes/profiles (Email / Code / Slack, etc.) |

Rationale in brief: Python keeps the GPU/ML stack (faster-whisper, torch) and the
UI in one process — no IPC bridge to a JS frontend. faster-whisper gives the best
quality/speed/effort balance on a decent GPU. Local Ollama keeps the pipeline
fully private and free per token; the provider interface makes swapping in a
cloud gateway (e.g. LiteLLM) a config change, not a rewrite.

---

## Architecture

Ten focused units, each with one responsibility and independently testable.

| Unit | Responsibility | Key dependency |
|---|---|---|
| `AppController` | State machine + orchestration; runs STT/LLM off the UI thread | Qt `QThread` worker |
| `HotkeyManager` | Global toggle hotkey (tap on / tap off) | `pynput` |
| `AudioCapture` | Mic → 16 kHz mono ring buffer, device selection, live RMS levels | `sounddevice` (PortAudio) |
| `Transcriber` | faster-whisper large-v3 on CUDA; VAD; vocab as `initial_prompt`; `en`/`he`/auto | `faster-whisper` |
| `Cleanup` | LLM formatting via provider interface; default Ollama | `httpx` |
| `TextInjector` | SendInput Unicode type-out; clipboard-paste fallback | `ctypes` |
| `SettingsStore` | Load/save config (hotkey, device, model, language, prompts, vocab, delivery) | TOML file |
| `HistoryStore` | Local transcript log + word-count stats | SQLite |
| `PillOverlay` | Frameless, always-on-top pill; states idle→listening→transcribing→done/error; visualizer fed by RMS. **Appearance is placeholder.** | PySide6 |
| `TrayIcon` + `SettingsWindow` | Tray menu (settings/quit) + Qt settings UI | PySide6 |

### Data flow

```
hotkey tap
  → AudioCapture starts, PillOverlay shows "listening" (live visualizer)
hotkey tap
  → AudioCapture stops, PillOverlay shows "transcribing"
  → Transcriber (faster-whisper) with vocab + language  →  raw text
  → Cleanup (Ollama) with editable prompt + vocab       →  final text
  → TextInjector types final text into the focused app
  → HistoryStore saves the entry
  → PillOverlay fades out
```

Threading: the audio stream runs in a PortAudio callback; STT + LLM run in a
worker `QThread`; the pill updates via Qt signals so the UI thread never blocks.

---

## Error handling

- **No CUDA / model load fails** → fall back to CPU int8 compute, warn in pill/tray.
- **Ollama not running / cleanup fails** → inject the **raw** transcript and notify;
  never lose the captured words.
- **Mic unavailable / empty transcript / focus lost mid-inject** → surface as a
  pill error state. Nothing is silently dropped.

---

## Settings (v1)

- Global hotkey binding
- Microphone device selection
- Whisper model size + compute type (large-v3 default; smaller for speed)
- Language mode: auto / force English / force Hebrew
- Cleanup: enabled toggle, Ollama model name, editable system prompt(s)
- Custom vocabulary / dictionary (fed to Whisper `initial_prompt` and LLM prompt)
- Text delivery method: type-out (default) or clipboard-paste
- History: enabled toggle

Config stored as TOML on disk and editable via the settings window.

---

## Testing

- **pytest** on pure logic: cleanup-prompt building, vocabulary injection, config
  round-trip (load/save), history writes — with audio/STT/LLM mocked.
- **Manual smoke tests** for STT accuracy and text injection (hardware/OS dependent).

---

## Out of scope for v1

- Multiple modes/profiles (v2)
- Aesthetic/visual design of the pill (later pass)
- Cloud LLM routing (interface is ready; not wired or exposed in v1)
