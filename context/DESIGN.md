# Orpheus: active product and implementation constraints

Updated 23 October 2026. This document describes the hardening/polish candidate. The July implementation plan is historical; the [current plan](../docs/superpowers/plans/2026-10-23-orpheus-hardening-and-polish.md) and user decisions supersede its GPU-first warnings, deferred UI work, and automatic commits.

## Product

Windows tray-based local dictation for daily use. Keep Python 3.12, native PySide6 widgets, faster-whisper/CTranslate2, sounddevice, pynput, and SQLite. CPU is a supported intentional backend. NVIDIA CUDA is optional. Do not replace the stack or transcription backend without an explicit product decision.

Default sequence: shortcut starts capture; shortcut stops; Whisper transcribes; optional Ollama cleanup falls back to raw text; history persists usable text when enabled; guarded delivery inserts text into the intended application. The current user's custom settings must be preserved.

User-confirmed recording policy: keep the full first 30 minutes and automatically stop/transcribe at the limit. Never discard the beginning silently or write temporary audio without a separate decision. The capture limit is configurable from 60 to 1800 seconds; default 1800.

## Runtime ownership

- The Qt owner thread manages recording state, readiness, resource replacement, recovery actions, and shutdown.
- Each session has an ID, cancellation event, copied settings, and foreground-window/process identity captured at start. Stale results/progress/limit signals cannot affect a later recording.
- One transcription pipeline is active, with at most one pending current session. Cancellation skips later stages and delivery; model/STT/cleanup calls already in progress may need to unwind.
- Model construction uses a lock. Preload events carry model generation. Finished QThreads release their callable references and are disposed after completion.
- Settings that replace live resources wait until consumers finish. Deferred apply failures are reported back to restore active persisted settings. Old HTTP clients/database connections close only after consumers release them.
- Quit blocks new work, cancels delivery, stops capture/hotkeys, and waits asynchronously for jobs. Do not terminate running QThreads or close their dependencies early.

## Recovery and privacy

Persist raw/final text once before delivery when history is enabled. Failed/partial delivery or history failure keeps an explicit recovery copy. History-disabled recovery stays in memory until exit; no transcript/audio files are created for it. A full recovery queue blocks new recording rather than silently replacing text.

Explicit cancellation before delivery discards the session. Partial insertion after cancellation remains recoverable. Copy/retry/dismiss actions are explicit. Retry arms a copy, then the existing hotkey confirms insertion after the user focuses a destination outside Orpheus. Partial retries require acknowledgement. Quitting with unfinished or unsaved work requires confirmation.

The history schema remains unchanged for this release. Search/paging/export use existing columns. Retention deletion is manual, previewed, confirmed, and transactional. Never delete live history, change Startup, or migrate a live database as an automatic verification step.

Diagnostics use a dedicated allowlisted logger, capped at four 1 MiB files. Never attach a general file handler that persists arbitrary third-party messages, exception strings, transcripts, prompts, vocabulary, or credentials.

The default Ollama URL is local. A user-configured remote endpoint can receive transcript text; do not claim unconditional offline behavior. Model weights may download at first load. Benchmarking must use local cached models and explicit local audio.

## Native UI direction

Use the user's Windows appearance and Segoe UI/native control typography. Keep visual changes restrained and focused on daily dictation feedback. Compose accessibility, layout, typography, colors, copy, and UI skills directly in Qt. Stitch is available as an alternative concept path but is not needed for the agreed restructuring.

Settings groups Input, Transcription, Cleanup, and History/Delivery. Its form scrolls while Save/Cancel remain visible. Show a readable shortcut with an explicit syntax editor, preserve modifier-only bindings, and keep advanced CPU controls collapsed. Vocabulary belongs to transcription and stays usable with cleanup off. Show active backend/precision and pending settings without a CPU warning.

The pill labels recording, transcription, cleanup, insertion, completion, cancellation, errors, and shutdown. Use an opaque dark surface `#14161A` and small-text foreground `#F8FAFC` (measured 17.31:1). Provide meaningful accessible names and announcements, honour Windows reduced-motion preferences, avoid focus activation, and anchor to the intended target's monitor with a primary-screen fallback. Full errors and text recovery belong in a persistent readable window accessible from the tray.

History is a searchable/paged native master/detail view with raw/final text, word count, and processing-time metadata. Existing `duration_s` means pipeline processing time, not spoken duration. Never silently reinterpret old rows.

## Known delivery boundaries

Win32 focus checking and sending input are not atomic. The target check identifies a top-level HWND/process, not a particular text field. It cannot guarantee safety against same-window field changes or the narrow check/send race. Do not steal focus or automatically replay partial insertion.

Paste supports plain-text clipboard contents and refuses rich/image formats. It restores previous text only when the clipboard sequence still matches; the check/restore race remains. Elevated destinations may reject synthetic input. Keep explicit copy recovery available.

## Verification

Tests use the repository venv, temporary data, fake hardware/OS boundaries, bounded Qt event loops, and native/offscreen widget captures. No dev servers/watchers, commits, or live dictation interruption. Real microphone/insertion/Windows accessibility and a controlled relaunch use the user's checklist. CPU speed/quality decisions require representative bilingual measurements; the current precision/model/thread settings remain intact until a measured choice is approved.
