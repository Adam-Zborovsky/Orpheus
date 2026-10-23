# Orpheus hardening and polish: execution plan

Status: reliability and native UI candidate implemented and automatically verified on 23 October 2026. Live relaunch/hardware checks and measured CPU tuning remain pending; see the execution log.

Evidence: [project review](../../reviews/2026-10-23/REVIEW.md), [functional reproductions](../../reviews/2026-10-23/functional-evidence.json), and [UI measurements](../../reviews/2026-10-23/ui-evidence.json).

## Outcome and scope

Keep Orpheus as a local Python/PySide6 Windows dictation app. Make failed delivery recoverable, release finished work, keep cancelled sessions isolated, preserve long recordings, and shut down safely. Then improve status, accessibility, Settings, and History. Tune CPU performance only after collecting representative measurements.

The user uses Orpheus while this work runs. Implementation and automated checks must leave that running process, its startup shortcut, live config, and live history intact. A final controlled relaunch and hardware checks require a convenient user-operated checkpoint.

The current startup shortcut launches `.venv\Scripts\pythonw.exe -m orpheus` from this repository. No bundled Orpheus executable needs rebuilding. Creating a standalone EXE, changing transcription backends, adding cloud services, or replacing Qt is outside this plan.

The warning fix and processing-device preservation from the review already exist as uncommitted changes. Preserve them and their tests. The existing suite passed **92 tests** at the review baseline; re-establish that baseline before implementation rather than assuming the count is unchanged.

## Decisions and operating constraints

| Topic | Decision |
| --- | --- |
| Long recordings | **User confirmed:** retain the entire recording up to 30 minutes, then stop and transcribe automatically. No rolling overwrite. At 16 kHz mono float32, the raw limit is 115,200,000 bytes, about 110 MiB, before join/transcription overhead. |
| CPU startup | Successful CPU loading stays silent. Show active backend/precision as ordinary status; preserve genuine load errors. |
| Transcription configuration | Preserve the current model, float32 precision, language, thread count, hotkey, delivery mode, and disabled cleanup unless the user explicitly changes them. Benchmark recommendations do not rewrite config. |
| Failed delivery | Preserve completed text for deliberate copy/retry. Never automatically replay potentially partially inserted text. |
| History disabled | Recovery remains available in memory for the process lifetime; it must not write transcripts or audio to disk. Clearly communicate the lifetime. |
| Explicit cancellation | Cancelled work must not insert text or create a new history entry after cancellation is observed before delivery. Already delivered or saved work cannot be undone automatically. |
| Target changes | Proposed plan default: capture the foreground top-level window when recording starts. If it changes before delivery, retain the transcript and request explicit recovery. Check again between delivery chunks. Do not steal focus. Changes to this policy are a product pivot. |
| Settings during work | Capture immutable settings per session. Defer resource replacement/closure until dependent jobs finish. Apply a pending settings update before accepting the next session; show that it is pending. Saving must never close a database/client in use by a worker. |
| History schema | Keep the existing schema for the first reliability release. Recovery/delivery state can remain in memory; use the existing raw/final columns for durable text. Any later schema migration is a separate, explicitly reviewed operation. |
| History retention | Preserve existing entries by default. Search/pagination/export are additive. Automatic retention deletion is opt-in and must preview its effect; never enable deletion by upgrade. |
| Version control | No commits, resets, cleanup deletes, forced operations, hook bypasses, or restoration over existing changes. The old July implementation plan's commit instructions do not apply. |
| Execution | No dev servers/watchers or interactive shell commands. Finite automated tests may pump the Qt event loop. Real GUI/hardware checks are a user-operated checklist. |

This document supersedes the July plan where it calls for CPU warnings, GPU-first defaults, deferred UI polish, or automatic commits. It does not authorize startup replacement, live data migration, destructive history operations, or paid model dispatch without the required model/cost confirmation.

## Task tracking and dependency order

Mark a task complete only when its acceptance checks pass and its result is recorded. Do not broaden a passing task's test scope without a new reason.

| ID | Stage / task | Depends on | Proposed owner |
| --- | --- | --- | --- |
| P0 | Baseline, checkpoints, and internal contracts | None | Main session |
| R1 | Worker disposal and model initialization | P0 | Main session |
| R2 | Session identity, cancellation, settings ownership | R1 | Main session |
| R3 | Microphone recovery and complete 30-minute capture | P0 contracts | Luna audio worker, if approved |
| R4 | Recoverable text and durable history ordering | R2 | Main session |
| R5 | Target-aware, cancellable delivery | R4 | Main session |
| R6 | Validated atomic settings and diagnostic logging | P0 contracts | Luna config worker, if approved |
| R7 | Asynchronous safe shutdown and resource cleanup | R2, R4, R5; integrate R3/R6 | Main session |
| G1 | Reliability acceptance checkpoint | R1–R7 | Main session |
| F1 | Readiness, stage feedback, and persistent recovery | G1 | Main session |
| U1 | Accessible, legible status pill and placement | F1 contracts | Luna pill worker, if approved |
| U2 | Grouped, scrollable Settings | F1 contracts, R6 | Luna settings worker, if approved |
| U3 | Searchable, accessible History | R4, F1 recovery contract | Luna history worker, if approved |
| G2 | Interface and daily-use acceptance checkpoint | F1, U1–U3 | Main session |
| T1 | Reproducible CPU benchmark and recommendation | G2 | Main session; bounded harness work may go to Luna |
| G3 | Release notes, staged validation, handoff | G2; T1 results or explicit deferral | Main session |

R3 and R6 can run beside the main controller work after P0. U1/U2/U3 have separate source ownership, but run at most two Luna workers simultaneously. All application wiring and shared contracts remain with the main session. Each stage is independently reviewable; CPU tuning may be deferred without blocking the reliability/UI release.

## P0 — Establish a reversible baseline

- [x] Read current instructions, review, and working diff. Preserve the existing warning fix and every unrelated change. Record the baseline paths and `git status --short` in the execution log below.
- [x] Keep a task-local copy of the pre-implementation application source and diffs, including untracked source/test files. Do not copy private dictation/history/config into the repository. Do not use `git reset` or `git checkout` to roll back.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q` and `git diff --check` once. Record existing failures verbatim and resolve their ownership before changing code.
- [x] Establish small typed internal contracts: session identity/cancellation/settings snapshot, model generation, stage event, and recovery item. Use dataclasses/enums where useful; do not build a job framework or plugin system.
- [x] Define dependency seams for microphone, model, foreground-window lookup, clipboard/SendInput, clock, and history so acceptance tests use fakes and temporary data.
- [x] Freeze additive signatures before delegating. R3 must keep `AudioCapture.stop()` returning the complete ndarray. R6 must preserve valid existing TOML behavior and expose validation/load failures for the main session to wire. UI workers consume events; they do not change pipeline orchestration.

Acceptance: baseline is known, current changes are recoverable, and each worker has a bounded file list and a written contract.

## R1 — Dispose finished workers and serialize model loading

Files: `orpheus/app.py`, `orpheus/transcriber.py`, `tests/test_app.py`, `tests/test_transcriber.py`; optionally a focused `tests/test_controller.py`.

- [x] Release a worker's callable/captured audio once execution finishes, and dispose its QObject after `finished` using the Qt lifecycle. Make thread-list removal idempotent.
- [x] Keep result handling on the Qt owner thread, without accessing a deleted worker wrapper. Do not retain the entire result/audio in cleanup closures.
- [x] Guard model construction with a lock and a second loaded check inside the lock. Publish model/device/precision together after successful construction; release the lock on failure.
- [x] Tag preload callbacks with model generation. Ignore callbacks from a replaced model.
- [x] Coordinate cancelled expensive work and subsequent work: one active transcription job and at most one pending current session. A new recording may proceed while an obsolete backend call unwinds, but do not launch unlimited concurrent model calls.

Acceptance: two concurrent successful load requests invoke the fake factory once; failure permits a later retry; 100 short synthetic jobs leave no retained worker/audio objects after queued results and deferred deletion are drained. Use object lifetime/weak-reference assertions rather than an arbitrary process-RSS threshold. Existing CPU/CUDA fallback tests still pass.

## R2 — Make session identity authoritative

Files: `orpheus/app.py`, controller tests, with `orpheus/__main__.py` wiring owned by the main session.

- [x] Give each recording/processing session a monotonically increasing ID and its own cancellation token and settings snapshot. Carry the ID in results and progress events, including worker exceptions.
- [x] Ignore obsolete results in every path: success, empty transcript, STT failure, cleanup failure, and unexpected exception. A stale preload failure must not replace an active recording's UI.
- [x] Check cancellation before expensive work and between stages, including before history persistence and delivery. Explicit cancellation must clear that session's pending work and release its audio.
- [x] Keep recording state and model readiness separate; do not let a background ready/error event reset a different active session.
- [x] Defer settings changes that replace live model/history/cleanup resources until their current consumers finish. Close replaced HTTP clients and history connections at that point, not while a job still references them.

Acceptance: start A, stop A, cancel A, start B; every late outcome from A leaves B's recording/state unchanged. Repeated rapid toggles keep expensive-job concurrency bounded. Saving settings during a fake blocked job never closes resources it is using, and the next session receives the new settings.

## R3 — Recover microphone failures and preserve long speech

Files: `orpheus/audio.py`, `tests/test_audio.py`. Main session integrates the limit signal in `app.py` and the setting/default in R6; an audio worker must not edit those files.

- [x] Close a partially created stream and clear its reference when creation/start fails. Make stop/close idempotent and ensure cleanup still runs if stopping raises.
- [x] Report an explicitly configured missing microphone instead of silently using a different device. Preserve system-default behavior when no device was selected.
- [x] Replace rolling overwrite for dictation with append-until-limit semantics. Retain the first sample through the final allowed sample; handle a chunk that crosses the limit exactly.
- [x] Use the confirmed 30-minute limit, measured by captured sample count. Signal the limit once and stop/transcribe through the controller's Qt thread. Never close a PortAudio stream or run STT inside its callback.
- [x] Ignore any subsequent callback data beyond the cap; avoid repeated notifications and duplicate stop jobs. Expose captured duration for status without changing persisted duration semantics.

Acceptance: failed start followed by retry actually opens/starts a new fake stream; stop failure still releases it; a scaled seven-second capture retains its beginning; a scaled 30-minute boundary preserves exactly the allowed samples and triggers one automatic stop. A full-size synthetic test is unnecessary. The controller integration test verifies one processing session at the limit and normal behavior for the next hotkey.

## R4 — Preserve transcripts independently of delivery success

Files: `orpheus/app.py`, `orpheus/history.py` only if needed without schema changes, pipeline/controller tests.

- [x] Produce a recovery item as soon as usable raw/final text exists. If an unexpected cleanup error escapes its normal fallback, retain the raw text.
- [x] With history enabled, persist the completed transcript once before attempting delivery. If saving fails, keep an in-memory recovery copy, continue the intended delivery, and surface the persistence failure.
- [x] With history disabled, retain unresolved failed-delivery items only in memory. Do not create a history database, temporary transcript file, or transcript-bearing diagnostic log.
- [x] Keep unresolved recovery items until explicitly copied/dismissed or deliberately retried. Bound the queue; if full, explain that recovery is required before accepting another recording rather than silently replacing text.
- [x] Make retries explicit and show possible partial delivery. Do not create a duplicate history entry for a retry of the same transcript. Do not imply that partially typed text was rolled back.

Acceptance: zero-input, partial-input, focus-change, and history-write failures leave usable text available; successful delivery creates one history row; history disabled creates zero rows/files; cleanup failure retains/inserts raw text; cancellation observed before persistence creates no new row. Document that in-memory recovery does not survive process termination when history is disabled.

## R5 — Guard the intended target and clipboard

Files: `orpheus/injector.py`, `orpheus/app.py`, `tests/test_injector.py`, controller tests. Introduce a small Win32 target helper only if it keeps OS calls testable.

- [x] Capture the foreground HWND and process identity at recording start. Validate before delivery and between chunks, alongside session cancellation and shutdown state.
- [x] If the target changes, is destroyed, or no longer has the same process identity, abort further automatic delivery and expose the recovery item. Never activate a window automatically.
- [x] Report how much delivery was attempted/confirmed where possible. SendInput partial counts must not be described as an atomic failure with nothing inserted.
- [x] For paste, ensure key-up cleanup on exceptional paths. Restore the previous clipboard only if the clipboard still belongs to this operation; do not overwrite a newer user copy. Preserve clipboard formats where the chosen implementation can support them; name any remaining text-only limitation.
- [x] Re-check the target immediately before paste or a type-out chunk. Acknowledge the small OS race between checking focus and sending input.

Acceptance: fake target switches prevent later chunks; cancellation/shutdown prevent later sends; failures yield recovery; user clipboard changes survive; existing Unicode, Hebrew, emoji, and newline tests pass. Real target switching is a later user-operated check.

Boundary: a top-level HWND is not the identity of a specific text field. Protecting different fields within one window would require another design, potentially Windows UI Automation. Do not claim that guarantee or silently expand scope.

## R6 — Validate config and make failures diagnosable

Files: `orpheus/settings.py`, optional `orpheus/diagnostics.py`, settings tests, optional `tests/test_diagnostics.py`. Main session owns integration into `__main__.py` and the Settings UI.

- [x] Validate enums, numeric ranges, booleans, vocabulary types, nonempty model names, hotkeys, and endpoint syntax. Preserve unknown-key compatibility and legitimate custom model IDs/URLs. Do not replace the user's valid Ctrl+Alt-only shortcut.
- [x] Add the 30-minute recording limit/default agreed in R3, with a safe bounded representation and old-config compatibility.
- [x] Write TOML to a same-directory temporary file and atomically replace it only after a complete successful write. Preserve the old valid file if any step fails.
- [x] Return an actionable error for malformed config. Preserve the invalid file and open repair/settings through main-session wiring; do not silently enable default history/cleanup or activate an unrelated hotkey after a load failure.
- [x] Add rotating diagnostic logs with finite size/retention. Record stages, backend, timings, and diagnostic error categories, excluding audio, transcript text, cleanup prompts, credentials, vocabulary, and full endpoint query strings. Sanitize third-party exception strings before persistence.
- [x] Report save failures inside the dialog and leave it open. Do not close on a save signal when durable save/apply/rebind failed.

Acceptance: malformed TOML and wrong types are recoverable; atomic-write failure leaves the old bytes intact; valid config round-trips without device reset; old config inherits the approved recording limit; log-rotation limits work in a temp directory; a unique fake transcript/credential marker never appears in logs. Save/apply failure leaves dialog state available for correction.

## R7 — Shut down without abandoned work

Files: `orpheus/app.py`, `orpheus/__main__.py`, `orpheus/cleanup.py`, shutdown/controller tests.

- [x] Route Quit through a shutdown request before `QApplication.quit()`. Reject new toggles, stop the hotkey listener/capture, cancel all current/pending sessions, and suppress future delivery.
- [x] Keep the Qt event loop running while workers unwind. Keep model/client/database references alive until their users finish. Close resources once and only then emit shutdown-complete and exit.
- [x] Bound network operations with existing meaningful timeouts; expose a waiting status for uninterruptible backend calls. Do not call `QThread.terminate()` or treat an elapsed five-second wait as completion.
- [x] Handle repeated Quit requests idempotently. Resolve pending config application and release queued audio during shutdown.

Acceptance: a fake backend remains blocked beyond the old five-second threshold while Qt processes a heartbeat; no text is sent after shutdown is observed, no history/client closes while used, and releasing the fake backend completes shutdown. Exercise simulated elapsed time rather than making the test sleep five seconds. No `QThread: Destroyed while thread is still running` warnings.

## G1 — Reliability acceptance checkpoint

- [x] Run the relevant focused tests during each R task, then `.\.venv\Scripts\python.exe -m pytest -q` and `git diff --check` after integration.
- [x] Convert the six synthetic review reproductions into regression tests with assertions about user-visible behavior and resource lifetime.
- [x] Add a deterministic controller scenario combining preload, recording, cancellation, a new session, deferred settings, target mismatch, recovery, and quit. Use a temporary APPDATA/data directory, fake OS dependencies, and bounded event-loop waits.
- [x] Confirm the warning/device regressions remain covered and active user data/startup were not changed.
- [x] Produce a short diff/result summary and record remaining limits. No UI or performance work can be used to excuse an unresolved reliability failure.

## F1 — Make status and recovery usable

Files: `orpheus/app.py`, `orpheus/__main__.py`, `orpheus/ui/tray.py`; add a small `orpheus/ui/recovery_window.py` if necessary, with focused UI tests.

- [x] Publish model readiness separately from session stages: Loading model, Ready, Recording, Transcribing, Cleaning up, Done, Cancelled, Error, and Shutting down as appropriate. CPU readiness is status, not a startup warning.
- [x] Give the tray a current status and an explicit entry to unresolved transcripts/errors. Error details and Copy text remain available after transient notifications disappear.
- [x] Expose recovery without stealing focus or automatically modifying the clipboard. Make Copy, Retry, and Dismiss explicit; explain partial-delivery risk before a retry.
- [x] Define Retry as an explicit handoff: selecting it arms that recovery item; the user focuses the intended text field and presses the existing dictation hotkey to confirm delivery there. Show the armed state and a Cancel retry action in the tray/recovery surface. Never type immediately from the recovery dialog into its own focused controls. After confirmation or cancellation, restore normal recording-hotkey behavior.
- [x] Show the 30-minute recording limit and the automatic-stop outcome. Show cancellation feedback and the hotkey's current action during processing.
- [x] Expose active backend/precision and pending settings in Settings. Keep the pill compact; persistent details belong in a readable native surface.

Acceptance: dismiss/timeout of a tray notification never removes the only recovery route; all actions work without persistent history; readiness and stages are distinguishable in fake-worker tests; opening recovery does not trigger automatic insertion or clipboard writes. Armed Retry requires an explicit hotkey confirmation in a destination outside Orpheus, creates no duplicate history row, and Cancel retry restores ordinary recording.

## U1 — Make the pill legible and accessible

Files: `orpheus/ui/pill.py`, `tests/test_pill.py`. Main session supplies stage/readiness contracts and window-placement data.

- [x] Keep native-compatible typography and a compact recording state. Use verified foreground/background pairs against both light and dark application surfaces; small text must reach 4.5:1.
- [x] Give the custom status widget meaningful accessible information and appropriate state-change announcements. Do not announce every microphone level tick.
- [x] Respect Windows animation preferences and provide a static readable state when motion is reduced. Keep a test seam for the OS preference rather than relying on test-machine settings.
- [x] Place the pill on the intended target's screen with a fallback for a missing/disconnected display. Keep placement within available work-area bounds.
- [x] Show concise errors with an accessible route to persistent full details from F1. Do not squeeze a full traceback into 72 pixels.

Acceptance: accessible state changes have meaningful names; static processing remains understandable; simulated reduced motion stops continuous decorative animation; light/dark contrast is calculated; placement tests cover two screen geometries and a removed target screen. Render real Qt states for visual review.

## U2 — Group and scroll Settings

Files: `orpheus/ui/settings_window.py`, `tests/test_settings_window.py`. Main session owns runtime save/apply and backend status wiring.

- [x] Group Input, Transcription, Cleanup, and History/Delivery using native Qt structure and spacing. Put the editable form inside a QScrollArea with Save/Cancel outside the scroll body.
- [x] Collapse advanced precision/thread controls and collapse or disable cleanup-dependent fields when cleanup is off. Keep explicit device choice and effective runtime device distinguishable.
- [x] Use plain delivery labels and factual precision guidance; retain advanced raw settings where useful.
- [x] Add a shortcut recorder only if it faithfully supports existing pynput bindings. Preserve modifier-only Ctrl+Alt and provide an advanced text editor when the recorder cannot represent a valid binding.
- [x] Keep input values and error focus on save failure; cover keyboard navigation, label associations, and long text/URLs. Preserve the existing prompt-reset behavior within the unsaved dialog.

Acceptance: current config survives an unrelated save; cleanup off visibly reduces irrelevant controls; Save remains reachable at small logical desktop sizes; 200% scaling checks do not strand actions; shortcut changes round-trip, including the existing modifier-only binding. Render before/after native Windows screenshots.

Design route: composed accessibility/layout/typography/color/writing/UI skills applied directly to Qt. Stitch is available for an alternative concept-generation pipeline, but no generation is needed for this restrained restructuring. A new visual direction is a pivot requiring a choice before UI implementation.

## U3 — Make History searchable and useful for recovery

Files: `orpheus/history.py`, `orpheus/ui/history_window.py`, history/UI tests. Main session integrates queries and recovery routing in `__main__.py`.

- [x] Add parameterized search and pagination over the existing schema. Keep deterministic timestamp/ID ordering and access beyond the newest 200 rows. Treat literal search characters safely.
- [x] Give the list/search/preview meaningful accessible names. Support keyboard selection/copy and readable empty, no-results, loading, and database-error states.
- [x] Offer raw/final comparison, word count, and correctly named processing duration. The existing `duration_s` is processing time, not recording time; do not relabel old rows as speech duration.
- [x] Integrate unresolved recovery items without requiring a schema change or implying that durable entries store delivery status when they do not.
- [x] Add explicit local export. Prepare retention/deletion controls with a preview, confirmation, and transactional behavior, tested only on temporary databases. Existing history remains untouched until separately authorized user action.

Acceptance: a temporary database with over 200 rows supports search and later pages with no missing/duplicate rows when timestamps tie; English/Hebrew raw/final content is intact; selecting and copying remain accessible; no-history mode shows memory recovery without database writes. Any schema/index change must be identified before applying it to live data.

## G2 — Interface acceptance checkpoint

- [x] Run the integrated test suite once after F1/U1/U2/U3 pass their focused checks.
- [x] Capture native Windows Settings, History empty/populated/search/error, pill states, and recovery using synthetic text. Verify layouts at normal and constrained logical heights and at 200% scaling where available.
- [x] Check names/roles, keyboard order, focus visibility, full error access, contrast, reduced motion, and simulated multi-monitor bounds. Mark real screen-reader/OS checks unverified until exercised.
- [x] Confirm successful CPU startup has no notification; failure has a durable recovery path; history-disabled behavior writes no transcripts.

## T1 — Measure CPU settings without changing the daily setup

Files: proposed `scripts/benchmark_transcription.py`, `docs/benchmarks/README.md`, and a results report. Use existing cached models and local audio; no automatic downloads.

- [x] Accept an explicit local corpus path and output path. Audio and private transcripts must stay outside source control. Use representative English, Hebrew, mixed-language, numbers, names, short clips, and a long recording.
- [ ] Compare float32/int8 × 8/16/32 threads × beam 5/1 on the same model, for 12 configurations. Include one warm-up and repeated measured runs; record model-load time separately from warmed transcription.
- [x] Record hardware/library versions, elapsed time, real-time factor, memory, and recognition quality against reference text. Keep thread count, beam, VAD, and corpus identical except the parameter under test. Include concurrent everyday-load conditions where practical.
- [ ] Run during a user-chosen idle window so the benchmark does not compete with active dictation. Do not edit APPDATA config, preload the daily process, enable cleanup, or select a winner solely on speed.
- [ ] Recommend a configuration from actual results. If representative audio is unavailable, ship the harness and mark tuning pending; do not fabricate measurements or substitute unrelated public benchmarks.

Acceptance: the command is finite and reproducible, reports all settings, leaves daily config unchanged, and separates performance from accuracy. A user decision is required before applying a different daily transcription configuration.

## G3 — Validate a release without disrupting daily use

- [x] Update README and `context/DESIGN.md` with actual implemented behavior, the 30-minute limit, recovery lifetime/privacy, safe cancellation, and known delivery limits. Preserve the review as historical evidence.
- [x] Stage the candidate source and a temp data directory independently of the daily process. Complete automated checks there; do not modify Startup or run a second real hotkey listener alongside the daily instance.
- [x] Provide a concise user-run checklist: quit current instance when convenient; launch candidate; verify silent CPU startup; dictate English/Hebrew into a chosen test document; cancel and restart; exercise recovery; verify target switching; use Settings/History by keyboard; quit while processing; relaunch.
- [x] Verify the staged app against a synthetic legacy history database before considering a real database operation. No history schema migration is expected for the first release.
- [x] Record passed/unverified checks and exact rollback source location. If the candidate fails, restore only files changed by this work from the captured baseline, preserving the existing warning fix and user changes. Do not delete data or rewrite Startup as rollback.

Done means the agreed stages pass their checks, remaining limitations are stated, and the user has a tested candidate plus a clear relaunch path. A passing test count alone is insufficient. EXE packaging remains a separate optional project.

## Delegation proposal and cost confirmation

The actual `collaboration.spawn_agent` interface exposes `gpt-6.1-sol`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, and `gpt-5.6-sol`. It permits explicit model/reasoning selection when starting without inherited history. There are four total active-agent slots, including the main session.

Terra (`gpt-5.6-terra`) appears in the local Codex model catalog, but **the current delegation tool cannot select it**. Do not infer subagent availability from the general model catalog. Use Luna here, do the work in the main session, or explicitly choose another supported harness for Terra.

| Allocation | Model / reasoning | Exact scope |
| --- | --- | --- |
| Main session | Existing session | Contracts, controller, sessions, persistence ordering, injection, shutdown, wiring, integration review and release decisions. |
| Audio worker | `gpt-6-luna`, `high`, `fork_turns="none"` | R3, `audio.py` and its tests only, against frozen signatures. |
| Config worker | `gpt-6-luna`, `high`, `fork_turns="none"` | R6, settings/diagnostics and their tests only. Main session performs UI/startup integration. |
| Later UI workers | Same explicit Luna selection; at most two at once | One U task per packet, own files only. `history.py` ownership transfers to U3 only after R4 integration. |

For prompts up to 272K input tokens, published GPT-6 Luna API rates are $0.10 input / $0.50 output per million tokens in Standard mode; Fast mode doubles those to **$0.20 / $1.00**. This harness lists the subagent service tier as `priority`, which OpenAI now calls Fast mode. Use the Fast rates for planning. GPT-6.1 Sol's corresponding Fast rates are $4 / $20; GPT-6 Astra's are $20 / $100. No separate expensive reviewer is proposed. [Official pricing](https://developers.openai.com/api/docs/pricing), [Luna model and tier details](https://developers.openai.com/api/docs/models/gpt-6-luna).

Illustration, not a quote or cap: one Luna task using 100K uncached input tokens and 20K output tokens would cost about $0.04 at the short-context Fast API rates, excluding caching writes and other charges. Repeated tool turns add model input; total usage is not the size of the initial task prompt. Actual Codex subscription/account accounting is not exposed by this tool, and it exposes no hard per-agent dollar cap. Do not equate that API illustration with a guaranteed bill.

The recommended execution choice is main session plus at most two Luna workers on the bounded independent tasks above. Main-session-only execution remains viable. **Allocation confirmed by the user before dispatch.** Two explicit GPT-6 Luna workers were used, with at most two active concurrently and no nested delegation. Actual token/billing totals are not exposed by the harness.

### Worker packet template

Send only the assigned task, relevant source/tests, the frozen contracts, and the relevant review reproduction. Do not fork the entire conversation.

```text
Implement task <ID> from docs/superpowers/plans/2026-10-23-orpheus-hardening-and-polish.md.
Owner files: <explicit list>. Other files are read-only; request integration changes from main.
Follow current project/session instructions. Preserve all existing changes.
Use temporary data and fake hardware/OS boundaries. No live config/history/startup writes.
No app launch/hotkey listener, commits, deletion, model downloads, or nested delegation.
Implement the acceptance checks, run the focused tests, and report exact results.
If a shared contract or product policy must change, stop that part and name the pivot.
Return: files changed, behavior, verification commands/results, remaining limitations.
```

The main session reviews every worker diff, owns shared-file integration, and runs the stage gate. Workers never change `app.py`, `__main__.py`, or each other's contracts opportunistically.

## Execution log

| Checkpoint | Status | Evidence |
| --- | --- | --- |
| Review baseline | Complete | 92 tests passed during review; warning/device fixes present. |
| Recording-limit policy | Confirmed | User selected full capture up to 30 minutes, then automatic stop/transcribe. |
| Implementation | Candidate complete | Reliability and UI code, regressions, native captures, docs, and benchmark harness delivered. |
| Delegation allocation | Confirmed and used | Main integration plus two explicit `gpt-6-luna` workers, `high`, without inherited history. |
| P0 | Passed | 92 baseline tests; pre-implementation source and diff snapshot at `C:\Users\Adam\AppData\Local\Temp\orpheus-baseline-72dfbtah`. |
| G1 | Passed | 151 tests at the reliability integration checkpoint, including finite Qt lifecycle checks. |
| G2 | Automated checks passed | Final native UI captures, 200% geometry checks, calculated 17.31:1 pill contrast, and native monitor-handle resolution. Real assistive-technology checks remain manual. |
| T1 | Harness complete; measurements pending | Fake tests, CLI help/dry-run, 12-configuration matrix. No representative corpus or idle-window benchmark was run; daily CPU settings remain intact. |
| G3 | Staging passed; relaunch pending | **210 tests passed in 2.87s** from the isolated candidate, using the repository venv. Source/tests/scripts match the workspace by SHA-256. No live listener/microphone/delivery was exercised. |


Final automated command (working directory `C:\Users\Adam\AppData\Local\Temp\orpheus-candidate-hns0zd3_`):

```powershell
& 'C:\Users\Adam\Documents\Projects\Orpheus\.venv\Scripts\python.exe' -m pytest -q
```

Result: **210 passed**. `git diff --check` passed with only Git's Windows line-ending conversion notices. [Implementation record and user-run checklist](../../reviews/2026-10-23/IMPLEMENTATION.md) contain the remaining live checks, captures, known delivery limits, and rollback paths. The active installation and private APPDATA data were not rewritten. No commits were made.
