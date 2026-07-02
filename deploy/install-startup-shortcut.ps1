<#
.SYNOPSIS
  Installs Orpheus autostart via a Startup-folder shortcut. No admin required.

  Drops a shortcut to pythonw.exe (no console window) in the current user's
  Startup folder, so Orpheus launches silently to the tray at login. Unlike the
  Task Scheduler variant, this needs no elevation.
#>
#Requires -Version 5
$ErrorActionPreference = "Stop"

# Find the repo root by walking up until we see pyproject.toml.
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

# Whisper (Hugging Face) model cache on E:. Keep this in sync with the E: path
# used by deploy/ollama/docker-compose.yml.
$hfHome = "E:\AI\huggingface"
New-Item -ItemType Directory -Force $hfHome | Out-Null
[Environment]::SetEnvironmentVariable("HF_HOME", $hfHome, "User")
Write-Host "HF_HOME set to $hfHome (Whisper models will download there)."

$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "Orpheus.lnk"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $pythonw
$lnk.Arguments = "-m orpheus"
$lnk.WorkingDirectory = $repo
$lnk.WindowStyle = 7   # minimized; pythonw shows no window regardless
$lnk.Description = "Orpheus voice dictation"
$lnk.Save()

Write-Host "Installed Startup shortcut: $lnkPath"
Write-Host "Orpheus will launch silently at login. Start it now with:"
Write-Host "  & '$pythonw' -m orpheus"
