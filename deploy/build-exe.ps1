# Build from the repository root with Python 3.12 and the project dependencies.
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoPath '.venv\Scripts\python.exe'
$iconPath = Join-Path $repoPath 'orpheus\assets\orpheus.ico'
$assetsPath = Join-Path $repoPath 'orpheus\assets'
Push-Location $repoPath
try {
    & $pythonPath -m PyInstaller --noconfirm --onefile --windowed --name Orpheus `
        --icon $iconPath --paths $repoPath --specpath build `
        --add-data "$assetsPath;orpheus/assets" `
        --collect-all faster_whisper --collect-all ctranslate2 `
        --collect-all tokenizers deploy/launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
} finally {
    Pop-Location
}
