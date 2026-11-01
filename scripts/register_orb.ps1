# Registers the ORB day book task (scripts/scheduled_orb.bat): every 5 min, 05:46-11:56 AK
# (9:46-15:56 ET), weekdays. Claude cannot register tasks in auto mode, so the operator runs this once.
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 5:46AM
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 5:46AM -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 10)).Repetition
$action = New-ScheduledTaskAction -Execute "C:\Users\kitti\stock-scanner\scripts\scheduled_orb.bat"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
Register-ScheduledTask -TaskName "stock-scanner-orb" -Trigger $trigger -Action $action -Settings $settings -Description "ORB day book: every 5 min 05:46-11:56 AK weekdays, flat by the close (orb- ids)" -Force | Out-Null
Get-ScheduledTask -TaskName "stock-scanner-orb" | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime

# Store start times as local wall time so they follow daylight-saving changes (2026-10-31).
& "$PSScriptRoot\fix_task_times.ps1"
