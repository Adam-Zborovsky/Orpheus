<#
.SYNOPSIS
  Registers Orpheus to launch silently at login via Task Scheduler.

  Uses pythonw.exe (no console window) so it starts straight to the tray with
  no flash of a terminal. A 15s delay lets audio devices and the network settle.
#>
#Requires -Version 5
$ErrorActionPreference = "Stop"

# Find the repo root by walking up until we see pyproject.toml — robust to
# wherever under the repo this script is placed.
$repo = $PSScriptRoot
while ($repo -and -not (Test-Path (Join-Path $repo "pyproject.toml"))) {
    $repo = Split-Path -Parent $repo
}
if (-not $repo) {
    throw "could not locate the repo root (no pyproject.toml above $PSScriptRoot)."
}
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    throw "pythonw.exe not found at $pythonw — create the venv first (py -3.12 -m venv .venv)."
}

# Store Whisper (Hugging Face) model downloads on E:\A_I alongside the LLM
# models, instead of C:\Users\Adam\.cache\huggingface. Set as a persistent
# user env var so both the task and manual launches inherit it.
$hfHome = "E:\AI\huggingface"
New-Item -ItemType Directory -Force $hfHome | Out-Null
[Environment]::SetEnvironmentVariable("HF_HOME", $hfHome, "User")
Write-Host "HF_HOME set to $hfHome (Whisper models will download there)."

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m orpheus" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT15S"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
# LogonType Interactive + RunLevel Limited: runs in your desktop session at
# normal integrity. Change to -RunLevel Highest if you need to type into
# elevated windows (SendInput can't reach a higher-integrity app otherwise).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "Orpheus" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Registered 'Orpheus' logon task — starts silently ~15s after you log in."
Write-Host "Start it now without logging out:  Start-ScheduledTask -TaskName Orpheus"
