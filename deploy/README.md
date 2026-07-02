# Silent autostart

Orpheus is a Windows tray app — it needs the interactive desktop (audio, global
hotkey, SendInput into focused windows), so it **cannot run in a Docker
container**. It launches in your login session instead, with no console window.

## Install (recommended: no admin)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install-startup-shortcut.ps1
```

Drops a shortcut to `.venv\Scripts\pythonw.exe -m orpheus` in your Startup
folder. `pythonw.exe` has no console, so there's no terminal flash — it goes
straight to the tray at login. Needs no elevation.

## Install (alternative: Task Scheduler, needs admin)

```powershell
# Run from an ELEVATED PowerShell — writing to the task library needs admin.
powershell -ExecutionPolicy Bypass -File deploy\install-startup-task.ps1
```

Registers a Task Scheduler task ("Orpheus") that runs ~15s after login. Use
this only if you want the startup delay or "start if available" recovery;
otherwise the shortcut above is simpler. Without an elevated shell this fails
with `Register-ScheduledTask: Access is denied.`

The installer also sets a persistent user env var `HF_HOME=E:\A_I\huggingface`
so Whisper model downloads land on E: next to the LLM models (instead of
`C:\Users\Adam\.cache\huggingface`). Uninstalling leaves `HF_HOME` in place —
the models live there; clear it manually with
`[Environment]::SetEnvironmentVariable("HF_HOME", $null, "User")` if you want
the default cache back.

Start it immediately without logging out:

```powershell
Start-ScheduledTask -TaskName Orpheus
```

## Uninstall

```powershell
# Whichever you installed:
powershell -ExecutionPolicy Bypass -File deploy\uninstall-startup-shortcut.ps1
powershell -ExecutionPolicy Bypass -File deploy\uninstall-startup-task.ps1   # elevated
```

## Notes

- The task runs at **normal integrity**. To dictate into apps running as
  administrator, re-run the installer after changing `-RunLevel Limited` to
  `-RunLevel Highest` in `install-startup-task.ps1`.
- Simpler alternative: put a shortcut to `pythonw.exe -m orpheus` in
  `shell:startup`. Task Scheduler is preferred here for the startup delay and
  the "start if available" recovery.
- If you also run the Ollama container, enable Docker Desktop's "Start when you
  log in" so the LLM is up before you dictate (see `ollama/README.md`).
