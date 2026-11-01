# Registers the pre-close four-sign check (scripts/preclose_check.py) at 08:40 and 11:40 AK,
# weekdays. Claude cannot register tasks in auto mode, so the operator runs this once:
#   powershell -ExecutionPolicy Bypass -File scripts\register_preclose.ps1
$days = "Monday","Tuesday","Wednesday","Thursday","Friday"
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$triggers = @(
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 8:40AM),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 11:40AM))
Register-ScheduledTask -TaskName "stock-scanner-preclose" -Force -Trigger $triggers `
  -Action (New-ScheduledTaskAction -Execute "C:\Users\kitti\stock-scanner\scripts\scheduled_preclose.bat") `
  -Settings $set -Description "Pre-close four-sign check: sells the SPY core only when losing and 4/4 confirm" | Out-Null
Get-ScheduledTask -TaskName "stock-scanner-preclose" | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime

# Store start times as local wall time so they follow daylight-saving changes (2026-10-31).
& "$PSScriptRoot\fix_task_times.ps1"
