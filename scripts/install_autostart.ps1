param([switch]$ConfirmInstall)
$ErrorActionPreference='Stop'
if (-not $ConfirmInstall) { throw 'Explicit user confirmation required; rerun with -ConfirmInstall after approval.' }
$phase10Root = Split-Path $PSScriptRoot -Parent
$phase10Python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe'
$phase10Action = New-ScheduledTaskAction -Execute $phase10Python -Argument ('"' + $phase10Root + '\scripts\phase10_realtime.py" start') -WorkingDirectory $phase10Root
$phase10Trigger = New-ScheduledTaskTrigger -AtStartup
$phase10Principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$phase10Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'ZUUU_Phase10_Realtime_V1' -Action $phase10Action -Trigger $phase10Trigger -Settings $phase10Settings -Principal $phase10Principal -Description 'ZUUU weather inference only; no retraining'
