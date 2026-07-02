#Requires -Version 5
$ErrorActionPreference = "Stop"
Stop-ScheduledTask -TaskName "Orpheus" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "Orpheus" -Confirm:$false
Write-Host "Removed the 'Orpheus' logon task."
