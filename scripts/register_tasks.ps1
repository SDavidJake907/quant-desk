# Registers the market-hours scan and the off-hours data collector.
#
# Written as a file rather than passed inline because schtasks.exe silently
# ignores /ET on a MINUTE schedule: the task it creates repeats every 30
# minutes FOREVER, firing all night. It reports success either way, and the
# only place the truth shows is "Repeat: Until: Duration: Disabled" in the
# verbose query. The PowerShell cmdlets set a real repetition duration.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Remove

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# Every task this script registers. -Remove walks this list, so a task added
# below and not added here would keep firing daily with no documented way to
# tear it down -- which is what happened to stock-scanner-news.
$tasks = @(
    "stock-scanner-scan",
    "stock-scanner-data",
    "stock-scanner-flatten",
    "stock-scanner-outcomes",
    "stock-scanner-gemma",
    "stock-scanner-peakwatch"
)

# Retired 2026-10-06 (WORKLIST §37). Unregistered on every run so a machine
# that still has them stops firing them.
#   sector       fed a relative-strength gate removed as null
#   advisor-live 117 ratings, none scoreable, and it shared the GPU with the
#                news gate every 30 minutes
# Retired 2026-10-23 (operator: trim the repo; FREEZE):
#   extsync      hourly after-hours picks sync -- the picks list is empty since
#                Gemma runs the whole account (10-22)
#   advisor-pre  TradingAgents pre-open ratings, disabled since 10-19
#   signals      Pine signals virtual paper, every 5 min (TRIALS #80, 0/66); trades nothing
#   oneshot-*, picks-pause/resume   one-time tasks, disabled
# Retired 2026-10-26 (operator: "stop Gemma from picking ... strip some of the repo down"; FREEZE):
#   every Gemma / LLM task (gemma-ext, gemma-sunday, gemma-review, gemma-portfolio, hindsight,
#   monitor, news, weekahead, ideas, leaders, journal, weekly, monday, factors), the research
#   data collectors (extended, extras) and the momentum paper rebalance. stock-scanner-gemma
#   now runs the rules book's 9:45 pick (scripts/rules_pick.py).
$retired = @("stock-scanner-sector", "stock-scanner-advisor-live",
    "stock-scanner-extsync", "stock-scanner-advisor-pre", "stock-scanner-signals",
    "stock-scanner-oneshot-fast-exit-1023", "stock-scanner-oneshot-picks-exit-1026",
    "stock-scanner-oneshot-wdc-exit", "stock-scanner-picks-pause", "stock-scanner-picks-resume",
    "stock-scanner-gemma-ext", "stock-scanner-gemma-sunday", "stock-scanner-gemma-review",
    "stock-scanner-gemma-portfolio", "stock-scanner-hindsight", "stock-scanner-monitor",
    "stock-scanner-news", "stock-scanner-weekahead", "stock-scanner-ideas", "stock-scanner-leaders",
    "stock-scanner-journal", "stock-scanner-weekly", "stock-scanner-monday", "stock-scanner-factors",
    "stock-scanner-extended", "stock-scanner-extras", "stock-scanner-momentum", "stock-scanner-reconcile")
foreach ($name in $retired) {
    try {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
        Write-Output "retired $name"
    } catch { }
}

if ($Remove) {
    foreach ($name in $tasks) {
        try {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Output "removed $name"
        } catch { Write-Output "$name was not registered" }
    }
    exit 0
}

# --- market-hours scan -----------------------------------------------------
# 05:30 Alaska = 09:30 Eastern. Both zones observe DST together, so the offset
# is 4 hours year-round and this needs no seasonal correction.
#
# OFFSET BY THREE MINUTES, ON PURPOSE. Bars close on the hour and the half
# hour. A task firing at :00 races the data provider to finalise the bar it
# needs, and a run at exactly 05:30 reads YESTERDAY's bars because the first
# bar of the session does not close until 06:00. Starting at :33 means every
# scan reads a bar that closed three minutes earlier and is complete.
#
# A full run takes about 3.2 minutes with ten candidates — 26s to scan 501
# symbols, ~16s per candidate for news and triage, 9s to rank them all in one
# call — so the 30-minute window is not close to tight.
$scanTrigger = New-ScheduledTaskTrigger -Daily -At 5:33AM
$repeating = New-ScheduledTaskTrigger -Once -At 5:33AM -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 30)
$scanTrigger.Repetition = $repeating.Repetition

# IgnoreNew matters: a slow scan must never have a second copy started on top
# of it. Two concurrent runs could size two positions against the same free
# sleeve capital, because each reads the account before the other has ordered.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 25) -MultipleInstances IgnoreNew

$scanAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_scan.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-scan" -Trigger $scanTrigger -Action $scanAction -Settings $settings -Description "Safety pass every 30 min, US market hours: protect.py re-arms stops, 60-bar limit on scanner positions (05:33-12:03 AK). Scanner entries paused." -Force | Out-Null
Write-Output "registered stock-scanner-scan   05:33-12:03 AK, every 30 min"

# --- off-hours data collection --------------------------------------------
# One hour after the close. This is what actually keeps the system "loaded
# with the correct data": 30-minute bars accumulate past Yahoo's 60-day
# window, which is the hard ceiling on every backtest here. Bars not saved
# before they age out cannot be recovered later.
$dataTrigger = New-ScheduledTaskTrigger -Daily -At 1:00PM
$dataSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$dataAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_data.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-data" -Trigger $dataTrigger -Action $dataAction -Settings $dataSettings -Description "Grow 30m bar history past Yahoo's 60-day window" -Force | Out-Null
Write-Output "registered stock-scanner-data   13:00 AK daily"

# --- Friday close-out ------------------------------------------------------
# 11:45 Alaska = 15:45 Eastern, fifteen minutes before the bell. Late enough
# that the day has had its chance, early enough that a market order fills and
# a failure still leaves time to close by hand.
#
# This is the only scheduled task that SUBMITS orders to close. It is not
# supervised by watchdog.py, which refuses order submission outright: a process
# that may have reached the broker and died before reading the response has
# unknown position state, and restarting it could sell twice. flatten.py is
# instead safe to re-run by hand, because an already-closed position counts as
# success.
#
# The wind-down that stops NEW positions from Thursday lives in TradingPolicy
# (wind_down_from_weekday) and is enforced by trade.py, not here — so a missed
# run of this task cannot also silently re-enable Thursday and Friday entries.
# EVERY WEEKDAY, FOUR TRIGGERS (2026-10-05). It used to be Fridays only, and
# when Friday is a market holiday the last session is Thursday: nothing
# flattened and positions rode a four-day close (FREEZE.md: 2026-12-24, 12-31,
# 2027-03-25, 06-17). flatten.py --only-before-closure asks the broker calendar
# for the NEXT session and acts only on the last session before a 3+ day
# closure, so on an ordinary Monday-Thursday every trigger exits doing nothing.
#
#   08:45 AK = 12:45 ET   a 13:00 ET early close          08:52  its retry
#   11:45 AK = 15:45 ET   the normal bell                 11:52  its retry
#
# --only-if-closing-within 25 picks the pair for the day's actual close. The
# retry re-runs the same idempotent job: flat already means nothing to do, and
# anything a first run could not close gets a second chance before the bell.
# Still not flat after the retry: flatten.py writes FLATTEN_ALERT.txt to the
# Desktop and logs/.
$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$flatTriggers = @("8:45AM", "8:52AM", "11:45AM", "11:52AM") | ForEach-Object {
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $_ }
$flatSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$flatAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_flatten.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-flatten" -Trigger $flatTriggers -Action $flatAction -Settings $flatSettings -Description "Flat before every weekend-length closure: weekdays 08:45/08:52/11:45/11:52 AK, acts only on the last session before a 3+ day close" -Force | Out-Null
Write-Output "registered stock-scanner-flatten  weekdays 08:45/08:52/11:45/11:52 AK, last session before a closure only"

# --- outcome recording -----------------------------------------------------
# Writes back what each closed trade actually produced. The audit log records
# what was DECIDED and never what came of it, and realized R is otherwise
# recomputed from Alpaca's order history, which the broker prunes.
#
# Read-mostly and outside the frozen trading path: it places no order and
# touches no policy, so it cannot influence a decision. Idempotent, so a missed
# day is picked up by the next run.
$outTrigger = New-ScheduledTaskTrigger -Daily -At 1:30PM
$outSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$outAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_outcomes.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-outcomes" -Trigger $outTrigger -Action $outAction -Settings $outSettings -Description "Append realized outcomes to the audit log" -Force | Out-Null
Write-Output "registered stock-scanner-outcomes 13:30 AK daily"


$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

# --- Gemma's book: the junior trader (WORKLIST §42) -------------------------
# 05:45 AK weekdays: after the 05:33 safety pass has placed the picks list's
# orders (so those names are visibly taken) and after the 03:30 ratings have
# freed the GPU. Gemma names up to 5 stocks from core.txt; bookorders prices,
# sizes, stops and sends on its 10% sleeve. Market closed -> nothing sent.
$gemTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 5:45AM
$gemSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$gemAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_gemma.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-gemma" -Trigger $gemTrigger -Action $gemAction -Settings $gemSettings -Description "Gemma's book: morning decision on core.txt names, real paper orders on a 10% sleeve (05:45 AK weekdays)" -Force | Out-Null
Write-Output "registered stock-scanner-gemma   05:45 AK weekdays"

# --- 24/5 (operator 2026-10-22) ------------------------------------------------
# Around the clock; each script asks the broker's clock which session it is and
# exits when none is open (Friday night, weekends before Sunday 16:00 AK).
#   peakwatch  every 5 min: READY / SELL, limit orders outside regular hours
$pwTrigger = New-ScheduledTaskTrigger -Daily -At 12:00AM
$pwTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At 12:00AM -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
$pwSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$pwAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_peak_watch.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-peakwatch" -Trigger $pwTrigger -Action $pwAction -Settings $pwSettings -Description "Peak watch: READY/SELL every 5 min, 24/5 (exits when no session is open)" -Force | Out-Null
Write-Output "registered stock-scanner-peakwatch  every 5 min, 24/5"

# Store start times as local wall time so they follow daylight-saving changes (2026-10-31).
& "$PSScriptRoot\fix_task_times.ps1"
