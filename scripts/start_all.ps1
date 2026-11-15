# Bring everything back up after a reboot, and refuse to look healthy if it is not.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
#
# WHY THIS EXISTS AT ALL
# The scheduled tasks are registered with Windows, so they survive a restart on
# their own. Ollama does NOT: there is no service and no auto-start entry, it
# runs as a user process. So after a reboot the scanner wakes up on schedule
# and Gemma is simply gone.
#
# That would be harmless if the news gate failed closed. It does not:
#
#     if not self.checked:
#         return Verdict(allowed=True, reason="news not checked")
#
# A news outage is deliberately non-fatal, because a model being down must
# never stop the deterministic strategy. The cost of that choice is that with
# Ollama down the gate approves EVERYTHING — including the takeover targets and
# structural-news names it exists to refuse. The system keeps trading and looks
# entirely normal while one of its safety checks is silently absent.
#
# So this script's real job is not "start things". It is to make that specific
# failure impossible to miss.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
$problems = @()

function Report($ok, $label, $detail) {
    $mark = if ($ok) { "  OK  " } else { " FAIL " }
    Write-Output "$mark $label  $detail"
    if (-not $ok) { $script:problems += $label }
}

Write-Output ""
Write-Output "STARTUP CHECK  $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
Write-Output ("-" * 62)

# --- 1. Ollama ------------------------------------------------------------
$running = Get-Process ollama* -ErrorAction SilentlyContinue
# One model, one window for every job (2026-10-04). TradingAgents reaches
# Ollama through /v1 and cannot ask for a size, so the server default must be
# the scanner's SCANNER_TRIAGE_NUM_CTX: a mismatch reloads the model each
# time the jobs alternate, and a short window silently drops the front of a
# long prompt. 2 slots keep gemma4:12b + the embedder on 16 GB of VRAM.
# NOTE the Ollama desktop app's own context setting (db.sqlite) overrides
# OLLAMA_CONTEXT_LENGTH; it was set to 24576 to match.
$env:OLLAMA_CONTEXT_LENGTH = "24576"
$env:OLLAMA_NUM_PARALLEL = "2"
if (-not $running) {
    if (Test-Path $ollama) {
        Write-Output "       starting Ollama..."
        Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    }
}
# Polled, not slept. A cold start after a reboot took longer than the fixed 6s
# this used to wait, and the check reported Ollama down while it was coming up.
$tags = $null
$deadline = (Get-Date).AddSeconds(45)
while (-not $tags -and (Get-Date) -lt $deadline) {
    try {
        $tags = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 5
    } catch { Start-Sleep -Seconds 2 }
}
Report ($null -ne $tags) "Ollama API" "http://localhost:11434"

# --- 2. The model the triage layer actually asks for -----------------------
# Read from .env, the same place the scanner reads it. A copy here drifted
# the moment the model changed: this check would pass on gemma3:12b while the
# gate asked for gemma4:12b and failed open.
$wanted = "gemma3:12b"
$envLine = Get-Content "$root\.env" -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*SCANNER_TRIAGE_MODEL\s*=' } | Select-Object -Last 1
if ($envLine) { $wanted = ($envLine -split '=', 2)[1].Trim().Trim('"') }
$have = $false
if ($tags) { $have = @($tags.models | Where-Object { $_.name -eq $wanted }).Count -gt 0 }
Report $have "model $wanted" $(if ($have) { "present" } else { "MISSING - news gate will pass everything" })

# --- 3. Scheduled tasks ---------------------------------------------------
# DISCOVERED, NOT LISTED. This block carried its own four names while the
# scheduler had six: stock-scanner-news was added 2026-09-25 and
# stock-scanner-sector on 09-28, and neither appeared here. A startup check
# reporting "all good" while silently skipping a third of the jobs is worse than
# no check, and it is the same drift that left news out of -Remove.
#
# Both directions are reported. A task expected and absent is a gap; a task that
# exists and is not expected is one somebody forgot to remove.
# Live tasks only. Retired jobs stay registered and Disabled on purpose
# (register_tasks.ps1); a Disabled task is not a gap and not a surprise.
$expected = @(
    "stock-scanner-scan",
    "stock-scanner-picks",
    "stock-scanner-orb",
    "stock-scanner-peakwatch",
    "stock-scanner-held-news",
    "stock-scanner-position-watch",
    "stock-scanner-polymarket",
    "stock-scanner-health",
    "stock-scanner-preclose",
    "stock-scanner-flatten",
    "stock-scanner-data",
    "stock-scanner-outcomes",
    "stock-scanner-grok-desk-plan",
    "stock-scanner-gemma-desk-plan",
    "stock-scanner-grok-desk-settle",
    "stock-scanner-gemma-desk-settle",
    "stock-scanner-grok-sunday"
)
$registered = @(Get-ScheduledTask -TaskName "stock-scanner-*" -ErrorAction SilentlyContinue)
foreach ($name in $expected) {
    $task = $registered | Where-Object { $_.TaskName -eq $name }
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $name
        Report ($task.State -ne "Disabled") $name "$($task.State), next $($info.NextRunTime)"
    } else {
        Report $false $name "NOT REGISTERED - run the matching scripts\register_*.ps1"
    }
}
foreach ($task in $registered) {
    if ($task.State -eq "Disabled") { continue }
    if ($expected -notcontains $task.TaskName) {
        Report $false $task.TaskName "REGISTERED BUT UNEXPECTED"
    }
}

# --- 4. Credentials and kill switch ---------------------------------------
Report (Test-Path "$root\.env") ".env" "broker credentials"
# Must match settings.kill_switch_path exactly. Pointed at the wrong filename
# this check reports "clear" forever, which is worse than not checking at all.
$kill = "$root\STOP_TRADING"
Report (-not (Test-Path $kill)) "kill switch" $(if (Test-Path $kill) { "ACTIVE - no trading until removed" } else { "clear" })

# --- 5. Broker reachable, read-only ---------------------------------------
Push-Location $root
$account = & ".venv\Scripts\python.exe" "scripts\check_alpaca.py" 2>&1 | Out-String
Pop-Location
$brokerOk = $LASTEXITCODE -eq 0
Report $brokerOk "Alpaca paper account" $(if ($brokerOk) { "reachable" } else { "unreachable" })

# --- verdict --------------------------------------------------------------
Write-Output ("-" * 62)
if ($problems.Count -eq 0) {
    Write-Output "ALL CLEAR - the scanner will run on schedule."
    exit 0
}
Write-Output "PROBLEMS: $($problems -join ', ')"
Write-Output ""
Write-Output "If 'model $wanted' is among them, the news gate is FAILING OPEN:"
Write-Output "it will approve takeover targets and structural-news names it would"
Write-Output "normally refuse, and nothing in the logs will look wrong."
Write-Output "Either fix Ollama or create the kill switch:  New-Item '$kill'"
exit 1
