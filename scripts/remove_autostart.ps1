param([switch]$ConfirmRemove)
if (-not $ConfirmRemove) { throw 'Explicit confirmation required: -ConfirmRemove' }
Unregister-ScheduledTask -TaskName 'ZUUU_Phase10_Realtime_V1' -Confirm:$false
