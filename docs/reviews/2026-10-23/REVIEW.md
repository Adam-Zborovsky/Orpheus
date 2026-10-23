# Orpheus review, 23 October 2026

CPU startup is now silent. The larger finding: Orpheus has a sensible small desktop architecture, but its recovery paths and worker lifecycle need attention before a visual overhaul. The interface exposes implementation details while hiding the information needed during actual dictation.

## Scope and foundation

Reviewed every application module, the tests, deployment scripts, README, and `context/DESIGN.md`. The supplied session instructions are the operating instructions; no on-disk `AGENTS.md`, `CLAUDE.md`, or `process.md` was found in this project. The design document explicitly calls the pill a functional placeholder and still describes a GPU-first design.

The app is Python 3.12 with PySide6 widgets and QPainter, faster-whisper/CTranslate2, sounddevice, pynput, SQLite, and optional Ollama cleanup. Qt supplies the native widget styling and most typography. Keeping this stack is the recommended direction. Nothing found justifies a web frontend or transcription-backend rewrite.

The actual installed startup entry is `Orpheus.lnk`, pointing to this repository's `.venv\Scripts\pythonw.exe -m orpheus`, with this repository as its working directory. There is no bundled Orpheus EXE in that Startup entry. Source edits therefore take effect on the next launch. The two associated `pythonw.exe` processes form a parent/child pair; their presence alone is not evidence of duplicate app instances.

Observed hardware: AMD Ryzen 9 7950X3D, 16 physical cores and 32 logical processors. Current configuration uses `large-v3-turbo`, `float32`, automatic device selection, all logical CPU threads, automatic language detection, type-out delivery, history enabled, and cleanup disabled. These settings and the running application were left intact.

Installed versions checked: PySide6 6.12.0, faster-whisper 1.2.1, CTranslate2 4.8.3, pytest 9.1.1.

## Changes applied

- `orpheus/app.py:215`: successful CPU preload logs an informational message instead of showing the CUDA-unavailable tray notice. Actual model-load errors still reach both the error signal and the pill error state.
- `orpheus/ui/settings_window.py:28`: saving the dialog preserves the configured processing device. Previously, any save wrote `device="auto"`, overwriting an explicit CPU or CUDA choice.
- Added regression coverage for silent CPU startup, visible preload failures, and processing-device preservation across Settings saves. Updated the startup behavior in README and the design document.

No broader reliability fixes or redesign were applied as part of this review.

## Functional findings

Ordered by user impact. “Reproduced” means exercised with synthetic audio and fake model, microphone, or delivery components, without recording, typing into other apps, or touching the running instance. Detailed outputs are in [functional-evidence.json](functional-evidence.json).

| Priority | Location | Finding and evidence | Recommended correction |
| --- | --- | --- | --- |
| HIGH | `orpheus/app.py:98`, `orpheus/app.py:106`, `orpheus/app.py:271` | **Failed insertion loses the recovery copy.** History is written only after injection succeeds. Reproduced `Could not type text: SendInput was blocked`: the result contained the transcript, but history had zero rows. The controller then discards the result's text. This contradicts the README's promise that words are never lost. | Preserve completed transcripts independently of successful insertion when history is enabled. Expose the failed result for copy/retry even when persistent history is disabled. If insertion partially succeeded, do not automatically retry the full text. |
| HIGH | `orpheus/app.py:208` | **Finished workers retain audio and other captured resources.** Workers are parented to the controller and removed from `_threads`, but never deleted. Reproduced 20 completed workers, zero active-list entries, 20 retained child workers, and 1,280,000 retained audio bytes. At 16 kHz mono float32, captured audio alone is 3.84 MB per minute. Old models can also remain reachable through worker closures. | Release completed QObjects with `deleteLater` through the finished lifecycle and release callable references after execution. Verify that repeated dictations leave a bounded number of worker children and release audio/model references. |
| HIGH | `orpheus/app.py:74`, `orpheus/app.py:81`, `orpheus/app.py:84`, `orpheus/app.py:256` | **An old cancelled session can reset a newer session.** Cancellation flags are checked only after successful nonempty transcription and cleanup. A cancelled transcription that returns empty or raises produces an ordinary failure. Reproduced a new session changing from `listening` to `idle` when the old empty result arrived. The capture can remain running while the state machine thinks it is idle. | Give each session an identity and ignore all results from obsolete sessions, including exceptions and empty results. Apply cancellation checks to failure returns as well. |
| HIGH | `orpheus/audio.py:22`, `orpheus/audio.py:33`, `orpheus/audio.py:53` | **Dictation longer than five minutes silently loses its beginning.** The recording buffer discards oldest chunks at 300 seconds, while the app continues to show listening. A scaled seven-second recording with a five-second cap returned only samples 2 through 6. | Choose an explicit recording-limit policy: retain all captured speech with a suitable storage strategy, or stop at the limit and explain it. Do not silently overwrite the beginning of a dictation. |
| HIGH | `orpheus/audio.py:78`, `orpheus/audio.py:82`, `orpheus/audio.py:69` | **A microphone start failure poisons subsequent attempts.** `_stream` is assigned before `.start()`. Reproduced `Device unavailable`, followed by a second start returning without creating or starting a stream. | Close and clear a partially created stream on failure. Verify that the next toggle genuinely retries microphone startup. |
| HIGH | `orpheus/app.py:193`, `orpheus/transcriber.py:88` | **Preload and transcription can load the same model concurrently.** `load()` has an unsynchronized check followed by model construction. The hotkey is enabled before preload finishes; cancelling and immediately starting again creates another route to concurrent work. Two simultaneous fake calls constructed two models. | Serialize model construction and publish the loaded model only after construction succeeds. Coordinate model reloads and active sessions so obsolete work cannot publish status for the replacement model. |
| HIGH | `orpheus/app.py:196`, `orpheus/app.py:202`, `orpheus/__main__.py:87` | **Shutdown does not establish that work has stopped.** It waits five seconds per worker, ignores the wait result, closes history, and returns. It also does not cancel the pipeline before waiting, so pending work may still reach text insertion while the app is quitting. Long CPU transcription or the 60-second Ollama request can outlive the wait. Code-confirmed; an actual process crash was not induced. | Stop accepting work, cancel pending delivery, keep worker dependencies alive, and complete shutdown after workers finish. Keep the UI responsive while waiting. Qt explicitly warns against destroying a running QThread. [Qt QThread documentation](https://doc.qt.io/qt-6/qthread.html). |
| HIGH | `orpheus/app.py:98`, `orpheus/injector.py:52`, `orpheus/injector.py:80` | **Delayed output can go to a different application.** SendInput targets the foreground window at delivery time, with no comparison to the intended target. Switching windows while CPU transcription runs changes the recipient; a switch during chunked type-out can split delivery. Code-confirmed; real cross-application typing was not exercised. | Track the intended target, check it before delivery, and preserve the transcript with an explicit recovery action when the target changes. Avoid forcibly stealing focus. |
| MEDIUM | `orpheus/settings.py:73`, `orpheus/settings.py:81`, `orpheus/__main__.py:20`, `orpheus/__main__.py:40` | **Startup/configuration failures are hard to diagnose.** Parsing has no recovery path, loaded values are not validated, saves overwrite the file directly, and logging has no persistent file handler. A malformed config can prevent a tray app launched through `pythonw.exe` from appearing. | Validate configuration, preserve invalid files, provide an actionable startup error, write config atomically, and keep bounded diagnostic logs without transcript content. |
| MEDIUM | `orpheus/history.py:43`, `orpheus/history.py:55`, `orpheus/__main__.py:74` | **History grows indefinitely but exposes only 200 entries.** There is no search, pagination, export, or deletion path. Older text remains stored locally without being reachable through the viewer. | Add search/pagination and deliberate retention controls. Keep persistent history opt-in behavior explicit; disabling new history should not be described as erasing existing entries. |

The next implementation should address transcript recovery, worker cleanup, session identity, and microphone retry first. These are concrete defects in the existing design. Recording-limit behavior and focus-change behavior need explicit product policies before implementation.

## Interface scope and coverage

Applied the `better-interface` review with its accessibility, layout, writing, typography, color, and UI domain skills. Web-specific rules were translated to the native Qt platform; the app is not being judged as a responsive website.

Rendered the actual widgets using the Windows platform plugin, Windows 11 Qt style, Segoe UI, and the current system appearance. Captures used hidden, finite-lived widget instances, not the running application's windows. History uses invented English/Hebrew sample text. Pill captures show steady states composited onto explicit light and dark backgrounds. An initial offscreen-plugin capture had missing glyphs and was replaced with native Windows rendering; it was not treated as an app defect.

| Domain | Evidence inspected | Result |
| --- | --- | --- |
| Accessibility | Qt accessible names, label associations, custom-painted status, native controls, motion implementation | Missing status semantics and history names; motion preference absent. Settings fields correctly inherit names through QFormLayout buddies. Full screen-reader and keyboard walkthrough not verified. |
| Layout | Actual Settings and History captures, minimum geometry, empty state, primary-screen placement | Flat hierarchy, large minimum height, primary-screen-only positioning. |
| Writing | All settings labels, tray actions, empty/error messages, processing feedback | Technical syntax and transient errors obscure useful actions. |
| Typography | Native Segoe UI rendering, pill's 8-point text, realistic error length, mixed Hebrew/English sample | Native form type is coherent; pill errors are heavily truncated. Mixed text is readable in the captured sample; complete bidi coverage not verified. |
| Color | Native palette, source RGBA values, pill captures against light/dark backgrounds | Native Text/Base palette ratio measured 13.77:1. Pill error contrast fails on the tested light background. |
| UI polish | Listening, processing, done, error, idle logic; entrance/exit code; settings/history empty and populated states | Compact pill is useful, but has insufficient status and recovery feedback. Live timing at slowed playback not verified. |

### Ranked interface findings

| Severity | Domain | Location | Before | After | Why |
| --- | --- | --- | --- | --- | --- |
| HIGH | Accessibility | `orpheus/ui/pill.py:53`, `orpheus/ui/history_window.py:36`, `orpheus/ui/history_window.py:46` | QAccessible reports empty names for the history list and preview. The pill exposes `Role.Client` with empty name and value, without accessible state announcements. | Give history panes descriptive accessible names; expose meaningful status and appropriate Qt accessibility updates for the pill. | The custom visual feedback is unavailable to assistive technology, and two interactive history controls have no names. Native Settings labels already work and should be preserved. |
| HIGH | Colors | `orpheus/ui/pill.py:177`, `orpheus/ui/pill.py:192` | Red error text `#ff6363` sits on a roughly 31% opaque dark background. Against `#f5f5f5`, the composited background is about `#aaaaab`; measured text contrast is **1.25:1**. | Give the error surface a sufficiently opaque background and a verified text color, at least 4.5:1 for this small text. | The small error text is difficult to read over light application surfaces. Dark-background appearance alone is insufficient for an overlay. |
| HIGH | Writing / typography | `orpheus/ui/pill.py:22`, `orpheus/ui/pill.py:193`, `orpheus/ui/tray.py:57` | A 72-pixel pill renders a long failure as “Could not…” and hides after three seconds. The tray notice is also temporary and uses the Information icon. | Keep the pill concise, but provide a persistent recoverable error with the full message and a next action such as copy transcript, choose microphone, or retry loading. | A hidden or suppressed tray notification leaves no durable route to understand and recover from the error. This is especially consequential when insertion failed. |
| HIGH | Accessibility | `orpheus/ui/pill.py:142`, `orpheus/ui/pill.py:166`, `orpheus/ui/pill.py:211` | Fade/slide, continuously animated levels, and pulsing dots run without consulting the Windows animation preference or offering reduced motion. | Respect the platform preference and provide static readable states when motion is reduced. | The status surface has no motion accommodation; processing feedback is almost entirely animated. |
| MEDIUM | Layout | `orpheus/ui/settings_window.py:72`, `orpheus/ui/settings_window.py:101` | Everything occupies one undivided form. Cleanup is disabled in the actual config, but its URL, model, long prompt, and reset button remain enabled and visually dominant. | Group Input, Transcription, Cleanup, and History/Delivery. Collapse or clearly disable cleanup-dependent controls while cleanup is off; place precision and threads under an explicit advanced section. | The layout gives infrequent implementation details the same weight as everyday choices. This is the clearest visual source of the unfinished feeling. |
| MEDIUM | Layout | `orpheus/ui/settings_window.py:26`, `orpheus/ui/settings_window.py:80`, `orpheus/ui/settings_window.py:86`, `orpheus/ui/settings_window.py:124` | Native initial capture is 514 × 856 logical pixels. The form cannot shrink below 672 pixels high and has no outer scrolling region. A 480 × 500 request remains 480 × 672; Save remains inside that resized dialog. | Put the form in a QScrollArea and keep Save/Cancel outside the scrolling body. Remove unnecessary expansion when a dependent feature is off. | A 1080-pixel display at 200% scaling has less logical height than this minimum. This is a geometry-derived risk, not a claimed observed clipping failure on the current 2560 × 1440 desktop. |
| MEDIUM | UI polish | `orpheus/app.py:193`, `orpheus/app.py:215`, `orpheus/ui/pill.py:112`, `orpheus/ui/tray.py:42` | Model initialization has no visible readiness state. All processing stages share three dots, and the tray tooltip never changes from “voice dictation.” | Show Loading model / Ready in a quiet persistent location, and distinguish Transcribing from Cleanup when applicable. Show CPU and active compute type in Settings as ordinary status. | Silence at successful CPU startup is correct; lack of readiness information is a separate problem. Users cannot tell slow work from an unready or failed backend. |
| MEDIUM | UI polish | `orpheus/app.py:43`, `orpheus/ui/pill.py:112` | Pressing the dictation hotkey during processing cancels it, but the pill offers no cancellation hint and disappears without a cancellation result. | Document the current action near processing feedback and give concise confirmation when it is cancelled. Keep any change to cancellation behavior a separate product decision. | A habitual extra tap can intentionally discard a dictation without an understandable outcome. |
| MEDIUM | Layout | `orpheus/ui/pill.py:119` | The pill always uses `primaryScreen()`. | Place it relative to the intended dictation window's screen or a saved user placement. | The current implementation cannot follow work onto a secondary monitor. Actual multi-monitor behavior was not exercised. |
| MEDIUM | Writing | `orpheus/ui/settings_window.py:30`, `orpheus/ui/settings_window.py:57`, `orpheus/ui/settings_window.py:92` | Users edit pynput syntax, select low-level compute types, and see “Type out (SendInput).” The compute tooltip states float32 is “most accurate” without evidence from this user's speech. | Use a shortcut recorder and readable shortcut display, plain “Type text” / “Paste text,” and a factual advanced precision explanation. Keep raw values available to advanced users. | Settings currently resemble a development control panel. Precision affects computation; it should not promise a universal real-world accuracy ranking. |

History also cannot show the stored raw transcript, word count, or duration even though the database contains those fields (`orpheus/ui/history_window.py:73`). Add raw/final comparison when cleanup is in use. Note that `duration_s` currently measures pipeline processing time, not recorded speech length (`orpheus/app.py:75`, `orpheus/app.py:106`); label it correctly or store both before showing timing statistics.

## CPU performance assessment

There is no reason to warn this machine's owner about choosing CPU. The current backend is an established choice for local transcription, and the observed working process used roughly 3.2 GiB at inspection. That snapshot does not measure peak memory or latency. Available RAM is not the same as fast decoding, and the worker-retention issue still matters on a 64 GB machine.

The most useful experiment is a small matrix on the same representative English and Hebrew recordings:

| Parameter | Compare | Decision criterion |
| --- | --- | --- |
| Compute type | Current float32 versus int8 | Stop-to-insertion latency, proper names, numbers, punctuation, and language preservation. |
| CPU threads | 8, 16, and current 32 | Median and slow-case latency while other everyday applications are open. |
| Decode beam | Current library default 5 versus 1 | Speed relative to actual transcription errors. This is not currently configurable in the app. |

CTranslate2 recommends int8 for CPU and avoiding worker × intra-operation thread counts greater than the number of physical cores. This makes 16 threads and int8 useful candidates, not proven winners for this AMD CPU. [CTranslate2 performance recommendations](https://opennmt.net/CTranslate2/performance.html).

The faster-whisper maintainers explicitly require matched beam sizes, thread counts, and comparable recognition quality when comparing performance. Their published CPU results use different hardware and a different model, so they are not predictions for this machine. [faster-whisper benchmark methodology](https://github.com/SYSTRAN/faster-whisper#comparing-performance-against-other-implementations).

No transcription benchmark, model download, or precision change was performed during this review. Cleanup remains disabled; enabling a local LLM would add a processing stage and should be measured separately.

## Recommended sequence

1. **Reliability:** recoverable transcripts, finished-worker cleanup, session identity, microphone retry, serialized model load, safe shutdown. Add integration coverage at the controller/resource boundary; the current tests largely exercise pure logic and fakes independently.
2. **Daily-use feedback:** readiness, distinct processing stages, understandable cancellation, persistent recoverable errors, accessible names/status, contrast, and motion preference.
3. **Native UI polish:** a grouped, scrollable Settings dialog with progressive disclosure; improved history navigation and recovery actions. Keep native Windows type and controls. A wholesale visual redesign is unnecessary.
4. **Measured CPU tuning:** use a bilingual benchmark corpus, then choose thread/precision/decode defaults from results.

For design work, two available paths were assessed: Stitch-generated screen concepts converted into Qt, or composed domain skills applied directly to the existing Qt interface. The latter is recommended for this project. Stitch is available but was not invoked: generating new screens would not resolve the lifecycle and recovery defects.

## Verification

Passed:

- Baseline `.\.venv\Scripts\python.exe -m pytest -q`: **86 passed**.
- After the warning and Settings fixes, same command: **92 passed**.
- `git diff --check`: no whitespace errors; Git reports the repository's existing Windows line-ending conversion notices.
- Native Windows widget captures: [Settings](settings.png), [smaller Settings](settings-small.png), [populated History](history.png), [empty History](history-empty.png), [pill states on light/dark surfaces](pill-states.png).
- QAccessible names and geometry queries, plus calculated contrast: [ui-evidence.json](ui-evidence.json). Qt's documented QFormLayout label/buddy behavior was confirmed in the generated Settings accessible names. [Qt QFormLayout documentation](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QFormLayout.html).
- Synthetic reproductions: failed-insertion history loss, stale cancelled-result state reset, buffer overwrite, concurrent model loading, microphone retry failure, and completed-worker audio retention.
- Read-only inspection of the Startup shortcut, running process relationship, installed dependencies, current configuration, and CPU topology.

Not verified:

- An actual relaunch of the daily-use process and the Windows notification experience after relaunch. The running instance was deliberately left available for dictation.
- Live microphone capture, recognition accuracy, real SendInput or paste delivery, elevated target windows, and focus switching during insertion.
- Real screen-reader operation, a full keyboard-only desktop walkthrough, multiple monitors, OS-level 200% scaling, and Windows animation-setting behavior.
- Destruction of a running worker during an actual quit. The shutdown risk is based on the code and documented Qt contract.

The pytest suite is valuable, but 92 passing tests do not establish the missing lifecycle and hardware integration guarantees. The synthetic failures above occur outside the current assertions.

## Verdict

**Block a wider polish release until the HIGH reliability and accessibility findings are addressed.** This is not an instruction to stop using the current app. The scoped CPU-warning fix passes its regression checks and will be active on the next launch. The broader project should be hardened before it is presented as reliably preserving every dictation.
