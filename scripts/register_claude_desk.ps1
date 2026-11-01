# Registers the two research desks (scripts/claude_desk.py), all shadow / research only:
#   Gemma plan 04:30 AK (08:30 ET), Claude plan 04:45 AK (08:45 ET), weekdays
#   Claude settle 13:20 AK, Gemma settle 13:25 AK (17:20 / 17:25 ET), weekdays
#   Claude's Sunday read 14:30 AK (18:30 ET) Sundays, after futures open
# Claude cannot register tasks in auto mode, so the operator runs this once:
#   powershell -ExecutionPolicy Bypass -File scripts\register_claude_desk.ps1
$days = "Monday","Tuesday","Wednesday","Thursday","Friday"
$dir = "C:\Users\kitti\stock-scanner\scripts"
function Add-DeskTask($name, $trigger, $bat, $minutes, $what) {
  $set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes $minutes)
  Register-ScheduledTask -TaskName $name -Force -Trigger $trigger `
    -Action (New-ScheduledTaskAction -Execute "$dir\$bat") -Settings $set -Description $what | Out-Null
}
Add-DeskTask "stock-scanner-gemma-desk-plan" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 4:30AM) `
  "scheduled_gemma_desk_plan.bat" 15 "Gemma research desk: per-stock calls before the open (shadow, no orders)"
Add-DeskTask "stock-scanner-claude-plan" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 4:45AM) `
  "scheduled_claude_plan.bat" 45 "Claude research desk: per-stock calls before the open (shadow, no orders)"
Add-DeskTask "stock-scanner-claude-settle" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 1:20PM) `
  "scheduled_claude_settle.bat" 10 "Claude research desk: play the day's calls in the shadow book"
Add-DeskTask "stock-scanner-gemma-desk-settle" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 1:25PM) `
  "scheduled_gemma_desk_settle.bat" 10 "Gemma research desk: play the day's calls in her shadow book"
Add-DeskTask "stock-scanner-claude-sunday" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 2:30PM) `
  "scheduled_claude_sunday.bat" 45 "Claude's Sunday read: the weekend's news and the week ahead (research only)"
Get-ScheduledTask -TaskName "stock-scanner-*desk*","stock-scanner-claude-*" | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime

# Store start times as local wall time so they follow daylight-saving changes (2026-10-31).
& "$PSScriptRoot\fix_task_times.ps1"
