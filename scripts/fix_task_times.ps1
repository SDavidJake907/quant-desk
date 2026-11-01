# Make every stock-scanner task follow the wall clock across daylight-saving changes.
#
# WHY (found 2026-10-31, the night Alaska fell back): New-ScheduledTaskTrigger -At writes the start
# time WITH the current UTC offset (e.g. 2026-10-07T08:45:00-08:00), which Task Scheduler treats as
# one fixed instant. After the clocks fall back to UTC-9 every task fires an hour EARLY by the local
# clock -- and an hour early against the market, which moves with the clocks: the Friday flatten at
# 14:45 ET (its 25-minute close window says no, so the SPY core would be held over the weekend), the
# pre-close check never in its window, the 9:45 pick before the open. A start time with NO offset is
# local wall time and follows the change. Safe to re-run; changes only triggers that carry an offset.
#   powershell -ExecutionPolicy Bypass -File scripts\fix_task_times.ps1
$fixed = 0
foreach ($task in Get-ScheduledTask -TaskName "stock-scanner-*") {
  $changed = $false
  foreach ($t in $task.Triggers) {
    if ($t.StartBoundary -match '(Z|[+-]\d\d:\d\d)$') {
      $t.StartBoundary = $t.StartBoundary -replace '(Z|[+-]\d\d:\d\d)$', ''
      $changed = $true
    }
  }
  if ($changed) {
    Set-ScheduledTask -TaskName $task.TaskName -Trigger $task.Triggers | Out-Null
    $fixed++
  }
}
Write-Output "triggers fixed on $fixed task(s)"
Get-ScheduledTask -TaskName "stock-scanner-*" | Where-Object State -ne "Disabled" | ForEach-Object {
  [pscustomobject]@{ Task = $_.TaskName; Next = ($_ | Get-ScheduledTaskInfo).NextRunTime
                     Start = ($_.Triggers | ForEach-Object { $_.StartBoundary }) -join " ; " } } |
  Sort-Object Task | Format-Table -AutoSize | Out-String -Width 220
