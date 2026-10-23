# Orpheus implementation record

The reliability and native UI work from the [execution plan](../../superpowers/plans/2026-10-23-orpheus-hardening-and-polish.md) is implemented. This is a candidate for a controlled relaunch, not a claim that live microphone/Windows delivery testing has already happened.

## Delivered behavior

| Area | Result |
| --- | --- |
| CPU startup | Successful CPU model loading has no notification. Real model failures remain visible, with a persistent error route. |
| Recording | Complete capture up to 30 minutes, exact sample cap, and one automatic stop/transcription. Start/stop failures release streams; stop errors still carry captured audio for transcription. Limit callbacks retain the original session identity even across a stop/start race. |
| Worker lifetime | Finished callables release captured audio/models; QThread objects are disposed through Qt after completion. Model construction is synchronized. |
| Session isolation | Each session has its own settings, ID, cancellation token, and target. Old results and progress cannot reset a later recording. One active pipeline and at most one pending session bound expensive work. |
| Recovery | Usable text is saved before insertion when history is enabled. Failed insertion/history writes leave a persistent UI route to a recovery copy. History-disabled recovery remains in memory. Partial cancellation keeps a copy; deliberate cancellation before delivery discards the session. |
| Delivery | Original foreground HWND/process is checked before and between chunks. A missing/changed destination stops insertion. Retry requires selecting a destination and confirming with the existing hotkey; it does not duplicate the history row. |
| Settings | Validation, atomic writes, preserved explicit device/settings, deferred resource replacement, and restoration after a rejected deferred hotkey change. Save errors stay inline. A listener startup failure leaves the tray and Settings available for repair. |
| Shutdown | Stops new work and pending delivery, waits asynchronously for workers, then closes clients/history and exits. Unsaved/unfinished work has a confirmation before Quit. |
| Status | Tray readiness/stages, persistent full errors/recovery, and a readable high-contrast status pill with accessible names/announcements and Windows motion-preference support. |
| Settings UI | Native grouped scrollable form, fixed Save/Cancel, readable shortcut with explicit editor, collapsed advanced CPU controls, hidden inactive cleanup fields, independent transcription vocabulary. |
| History | Search of raw/final text, 50-entry pages beyond 200 rows, raw/final comparison, word count, processing duration, selected UTF-8 export, and manual reviewed/confirmed retention deletion. No schema migration or automatic pruning. |
| CPU benchmark | A local cached-model harness for float32/int8 × 8/16/32 threads × beam 5/1. It was tested with fakes/dry-run; no real recognition benchmark or configuration change was performed. |

## Verification

- Baseline: **92 tests passed** before implementation.
- Reliability integration: **151 tests passed** before UI work.
- Integrated UI/recovery work: **203 tests passed** before the final callback/monitor checks.
- Final isolated candidate validation: **210 tests passed in 2.87 seconds**. The tested import path was the staged source, and source/tests/scripts were checked against the workspace by SHA-256. `git diff --check` passed with Windows line-ending advisories only.
- Regression scenarios include 100 completed workers with weak-reference/deferred-delete checks, cancelled A returning after B starts, complete capped recording, a callback paused across stop/start, failed insertion with history off, partial cancellation, deferred settings rejection, retry without duplicate history, and responsive quit while a backend is blocked.
- Startup wiring tests fake application/tray/listener/model/data boundaries. They verify preload/reload, scoped history-store lifetime, shutdown ordering, invalid-config preservation, deferred rollback, and access to Settings after hotkey startup failure.
- Diagnostic tests verify event allowlisting, rotation, and exclusion of fake transcript/credential markers. Application tests do not open a real hotkey listener, microphone stream, or delivery target.
- Native Windows 11 Qt style / Segoe UI captures used hidden widgets and synthetic data. The small pill text measures **17.31:1** on its opaque surface. Settings Save/Cancel and History Close remain inside their dialogs in a **200% scale-factor** check at 500 × 500 and 640 × 440 logical sizes. [UI evidence](after/ui-evidence.json), [scaling evidence](after/scaling-evidence.json).
- Monitor resolution was checked read-only against this machine's three connected screens. Matching uses native HMONITOR handles, because Qt's display names here differ from Win32 device names. Automated tests cover non-primary matching and removed-screen fallback. Actual live dictation on different monitors remains a manual check. [Qt native screen interface](https://doc.qt.io/qt-6/qnativeinterface-qwindowsscreen.html), [Win32 MonitorFromWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-monitorfromwindow).

Captures: [Settings](after/settings.png), [small Settings](after/settings-small.png), [200% Settings](after/settings-200-percent.png), [History](after/history.png), [empty search](after/history-empty-search.png), [Recovery](after/recovery.png), [pill states](after/pill-states.png).

The active dictation process was not stopped or restarted. Live config/history and Startup were not rewritten, and no commits were made.

## Relaunch and hardware checklist

When convenient, Quit the current Orpheus instance and reopen its existing Startup shortcut. It points to this repository; no EXE rebuild or installer is required. Do not launch a second instance alongside the existing listener.

- [ ] Successful CPU startup produces no warning; tray/Settings report the active backend and readiness.
- [ ] Dictate English, Hebrew, numbers, and a mixed-language sentence into a chosen disposable document. Verify text and formatting.
- [ ] Cancel processing, immediately start another recording, and confirm that the old result neither inserts text nor resets the new session.
- [ ] Switch to a different application while transcription is pending. Verify insertion stops and Recover text contains the complete transcript. Copy it, or arm Retry, focus the intended field, and confirm with the hotkey.
- [ ] Exercise a deliberately blocked target and inspect partial-delivery messaging. Do not automatically retry text already present in the document.
- [ ] Disable history temporarily through Settings only if desired. Verify an insertion failure still offers a memory-only copy and warns about its lifetime. Restore the prior preference afterward.
- [ ] Use Settings and History by keyboard. Check focus visibility, screen-reader announcements, and Windows animation preference with the actual assistive setup.
- [ ] Dictate on a secondary monitor and check pill placement; test disconnect/fallback only if convenient.
- [ ] Quit while processing. Verify the confirmation, responsive waiting status, eventual exit, and absence of a running-thread destruction crash. Relaunch afterward.
- [ ] The 30-minute automatic-stop path is tested with scaled sample counts. A real 30-minute recording was not captured during development; optionally exercise that boundary during an idle window.

Elevated application delivery, real microphone-driver behavior, screen-reader operation, and exact recognition quality remain unverified until these checks are exercised.

## CPU tuning and limits

Use the [benchmark instructions](../../benchmarks/README.md) with representative local audio and reference text during an idle window. The matrix is ready; no speed/accuracy result is inferred from another CPU or model. Existing model, precision, threads, hotkey, and cleanup choices remain intact.

The target guard protects a top-level window/process, not a particular text field. Focus check and SendInput are separate OS operations. Paste supports plain-text clipboard contents and refuses rich/image formats; sequence-based restoration still has a narrow check/restore race. Copy recovery is available when automatic delivery is unsuitable. In-memory recovery cannot survive process termination. History stores plaintext locally when enabled. SQLite history search is synchronous and page-bounded; exceptionally large databases may need a separate performance pass.

No cloud service, background server, standalone EXE packaging, live data migration, or automatic history deletion was added.

## Checkpoints and rollback

- Pre-implementation source snapshot: `C:\Users\Adam\AppData\Local\Temp\orpheus-baseline-72dfbtah`.
- Isolated candidate copy: `C:\Users\Adam\AppData\Local\Temp\orpheus-candidate-hns0zd3_`.

The source snapshot includes the earlier warning fix and existing working changes, plus the patch/status record. It contains no private APPDATA dictations/config/history. Temp directories are convenience checkpoints, not permanent backups.

If rollback is needed, stop the candidate at a convenient time and restore only the affected source files from that snapshot after comparing any later edits. Preserve private data and Startup. Do not reset Git, delete history, or overwrite new unrelated user changes as a rollback shortcut.
