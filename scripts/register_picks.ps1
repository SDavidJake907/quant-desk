# Registers the 05:45 Alaska picks task only, and disables the old name.
# The batch file is still scheduled_gemma.bat. It runs rules_pick.py, then
# small_pick.py. No model. This script does not call fix_task_times.ps1 and
# does not register any other task.
#   powershell -ExecutionPolicy Bypass -File scripts\register_picks.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 5:45AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$action = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_gemma.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-picks" -Force -Trigger $trigger -Action $action -Settings $settings -Description "Rules and small-cap picks at 05:45 AK weekdays. Queues buys. Peak watch sends them. No model." | Out-Null

$task = Get-ScheduledTask -TaskName "stock-scanner-picks"
$changed = $false
foreach ($item in $task.Triggers) {
    if ($item.StartBoundary -match '(Z|[+-]\d\d:\d\d)$') {
        $item.StartBoundary = $item.StartBoundary -replace '(Z|[+-]\d\d:\d\d)$', ''
        $changed = $true
    }
}
if ($changed) {
    Set-ScheduledTask -TaskName "stock-scanner-picks" -Trigger $task.Triggers | Out-Null
}

Disable-ScheduledTask -TaskName "stock-scanner-gemma" | Out-Null

$info = Get-ScheduledTaskInfo -TaskName "stock-scanner-picks"
$live = Get-ScheduledTask -TaskName "stock-scanner-picks"
$old = Get-ScheduledTask -TaskName "stock-scanner-gemma"
$start = ($live.Triggers | ForEach-Object { $_.StartBoundary }) -join " ; "
Write-Output "stock-scanner-picks state=$($live.State) next=$($info.NextRunTime) start=$start action=$($live.Actions.Execute)"
Write-Output "stock-scanner-gemma state=$($old.State)"
