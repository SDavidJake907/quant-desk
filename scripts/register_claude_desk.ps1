# Registers the research desks (scripts/claude_desk.py). No orders.
#   Gemma plan 04:30 AK (08:30 ET) weekdays. Claude is retired and is not registered.
#   Grok verifies Gemma's plan 06:15 AK (10:15 ET). He keeps no book.
#   Grok check 13:15 AK (no model), Gemma settle 13:25 AK, weekdays
#   Grok's Sunday read 14:30 AK (18:30 ET) Sundays
# The operator runs this once:
#   powershell -ExecutionPolicy Bypass -File scripts\register_claude_desk.ps1
$days = "Monday","Tuesday","Wednesday","Thursday","Friday"
$dir = "C:\Users\kitti\stock-scanner\scripts"
function Add-DeskTask($name, $trigger, $bat, $minutes, $what) {
  $set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes $minutes)
  Register-ScheduledTask -TaskName $name -Force -Trigger $trigger `
    -Action (New-ScheduledTaskAction -Execute "$dir\$bat") -Settings $set -Description $what | Out-Null
}
Add-DeskTask "stock-scanner-grok-desk-plan" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 6:15AM) `
  "scheduled_grok_desk_plan.bat" 90 "Grok verifies Gemma's calls. No book and no orders."
Add-DeskTask "stock-scanner-gemma-desk-plan" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 4:30AM) `
  "scheduled_gemma_desk_plan.bat" 15 "Gemma research desk: per-stock calls before the open (shadow, no orders)"
Add-DeskTask "stock-scanner-grok-desk-settle" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 1:15PM) `
  "scheduled_grok_desk_settle.bat" 10 "Grok: confirm Gemma's plan is signed. Unsigned calls are not played. No model call."
Add-DeskTask "stock-scanner-gemma-desk-settle" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 1:25PM) `
  "scheduled_gemma_desk_settle.bat" 10 "Gemma research desk: play the day's calls in her shadow book"
Add-DeskTask "stock-scanner-grok-sunday" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 2:30PM) `
  "scheduled_grok_sunday.bat" 90 "Grok's Sunday read (research only, no orders)"
foreach ($retired in "stock-scanner-claude-plan","stock-scanner-claude-settle","stock-scanner-claude-sunday") {
  if (Get-ScheduledTask -TaskName $retired -ErrorAction SilentlyContinue) {
    Disable-ScheduledTask -TaskName $retired | Out-Null
  }
}
Get-ScheduledTask -TaskName "stock-scanner-*desk*","stock-scanner-claude-*" | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime

# Store start times as local wall time so they follow daylight-saving changes (2026-10-31).
& "$PSScriptRoot\fix_task_times.ps1"
