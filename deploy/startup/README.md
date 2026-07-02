# Silent autostart

Orpheus is a Windows tray app — it needs the interactive desktop (audio, global
hotkey, SendInput into focused windows), so it **cannot run in a Docker
container**. It launches in your login session instead, with no console window.

## Install

```powershell
powershell -ExecutionPolicy Bypass -File deploy\startup\install-startup-task.ps1
```

This registers a Task Scheduler task ("Orpheus") that runs
`.venv\Scripts\pythonw.exe -m orpheus` ~15s after you log in. `pythonw.exe`
has no console, so there's no terminal flash — it goes straight to the tray.

Start it immediately without logging out:

```powershell
Start-ScheduledTask -TaskName Orpheus
```

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File deploy\startup\uninstall-startup-task.ps1
```

## Notes

- The task runs at **normal integrity**. To dictate into apps running as
  administrator, re-run the installer after changing `-RunLevel Limited` to
  `-RunLevel Highest` in `install-startup-task.ps1`.
- Simpler alternative: put a shortcut to `pythonw.exe -m orpheus` in
  `shell:startup`. Task Scheduler is preferred here for the startup delay and
  the "start if available" recovery.
- If you also run the Ollama container, enable Docker Desktop's "Start when you
  log in" so the LLM is up before you dictate (see `../ollama/README.md`).
