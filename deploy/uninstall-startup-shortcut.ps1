#Requires -Version 5
$ErrorActionPreference = "Stop"
$lnkPath = Join-Path ([Environment]::GetFolderPath("Startup")) "Orpheus.lnk"
if (Test-Path $lnkPath) {
    Remove-Item $lnkPath
    Write-Host "Removed Startup shortcut: $lnkPath"
} else {
    Write-Host "No Startup shortcut found at $lnkPath"
}
