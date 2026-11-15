# Registers the position watch only. Report only: no order, no stop change.
# Weekdays 05:33-12:03 Alaska, every 30 minutes. The :33 start matches the scan,
# so the bar it reads has already closed.
#   powershell -ExecutionPolicy Bypass -File scripts\register_position_watch.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 5:33AM
$repeating = New-ScheduledTaskTrigger -Once -At 5:33AM -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 30)
$trigger.Repetition = $repeating.Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$action = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_position_watch.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-position-watch" -Force -Trigger $trigger -Action $action -Settings $settings -Description "Position watch: every 30 min 05:33-12:03 AK weekdays. Reports profit given back on each open position. Places no order." | Out-Null

$task = Get-ScheduledTask -TaskName "stock-scanner-position-watch"
$changed = $false
foreach ($item in $task.Triggers) {
    if ($item.StartBoundary -match '(Z|[+-]\d\d:\d\d)$') {
        $item.StartBoundary = $item.StartBoundary -replace '(Z|[+-]\d\d:\d\d)$', ''
        $changed = $true
    }
}
if ($changed) {
    Set-ScheduledTask -TaskName "stock-scanner-position-watch" -Trigger $task.Triggers | Out-Null
}
$info = Get-ScheduledTaskInfo -TaskName "stock-scanner-position-watch"
$live = Get-ScheduledTask -TaskName "stock-scanner-position-watch"
$start = ($live.Triggers | ForEach-Object { $_.StartBoundary }) -join " ; "
$repeat = ($live.Triggers | ForEach-Object { "$($_.Repetition.Interval) / $($_.Repetition.Duration)" }) -join " ; "
Write-Output "stock-scanner-position-watch state=$($live.State) next=$($info.NextRunTime) start=$start repeat=$repeat"
