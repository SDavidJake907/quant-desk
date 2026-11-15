# Registers the Polymarket read only. Report only: no bet, no order, no stop change.
# Weekdays at 05:15 Alaska, before the 05:45 pick.
#   powershell -ExecutionPolicy Bypass -File scripts\register_polymarket.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 5:15AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$action = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_polymarket.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-polymarket" -Force -Trigger $trigger -Action $action -Settings $settings -Description "Polymarket odds for the open book at 05:15 AK weekdays. Report only. Places no bet and no order." | Out-Null

$task = Get-ScheduledTask -TaskName "stock-scanner-polymarket"
$changed = $false
foreach ($item in $task.Triggers) {
    if ($item.StartBoundary -match '(Z|[+-]\d\d:\d\d)$') {
        $item.StartBoundary = $item.StartBoundary -replace '(Z|[+-]\d\d:\d\d)$', ''
        $changed = $true
    }
}
if ($changed) {
    Set-ScheduledTask -TaskName "stock-scanner-polymarket" -Trigger $task.Triggers | Out-Null
}
$info = Get-ScheduledTaskInfo -TaskName "stock-scanner-polymarket"
$live = Get-ScheduledTask -TaskName "stock-scanner-polymarket"
$start = ($live.Triggers | ForEach-Object { $_.StartBoundary }) -join " ; "
Write-Output "stock-scanner-polymarket state=$($live.State) next=$($info.NextRunTime) start=$start"
