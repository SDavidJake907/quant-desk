@echo off
REM Scheduled market-hours scan. Wrapped rather than called directly because
REM config paths (logs/, data/) are relative to the project root, and a task
REM started from C:\Windows\System32 would write its audit log somewhere nobody
REM ever looks — the classic "works by hand, silently wrong on a schedule" bug.
REM
REM trade.py refuses to run when the market is closed, so weekends and holidays
REM cost one process start and nothing else.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

REM One log per task (WORKLIST §40): a shared log locked out any task that
REM started while another was writing, and that task silently did not run.
set LOG=logs\scheduled_scan.log
echo. >> "%LOG%"
echo ===== %DATE% %TIME% ===== >> "%LOG%"
REM Every step runs. The task fails if any step fails, so a crashed protect.py
REM is not reported as success (health.py reads LastTaskResult).
set FAIL=0

REM OPERATOR'S SELL-AT-OPEN LIST FIRST (data/monitor/sell_at_open.json, 2026-11-01): positions
REM the operator chose to sell at the open, closed before protect.py would re-arm their
REM stops. Does nothing while the market is shut or when the list is gone.
".venv\Scripts\python.exe" "scripts\sell_at_open.py" >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1

REM RE-ARM ANYTHING NAKED BEFORE DOING ANYTHING ELSE.
REM
REM The scan already detects positions with no live stop and shouts about
REM them — and then does nothing but print "re-arm with: protect.py --submit".
REM That is a manual step in an unattended system, and the last time it
REM mattered a power outage meant nobody was watching: three positions worth
REM 58,701 dollars sat with no floor under them through a weekend and all of
REM Monday.
REM
REM protect.py is safe to run on a schedule. It reads the ORIGINAL stop and
REM target back out of the audit log rather than re-deriving them from
REM today's price, and it refuses to re-arm a position whose stop has already
REM been breached, reporting those for a human instead.
REM
REM No market-hours guard, deliberately. A protective order sent after the
REM bell is QUEUED for the next open rather than rejected, and a stop that
REM activates at the open beats a position that is naked into it.
".venv\Scripts\python.exe" "scripts\protect.py" --submit >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1

REM THE OPERATOR'S LIST, KEPT IN SYNC (WORKLIST §41). watchlists/picks.txt:
REM names added are bought, names removed are sold, each with a safety stop.
REM After protect.py, so an existing position is protected before anything new
REM is sent. Acts only while the market is open; reports otherwise.
".venv\Scripts\python.exe" "scripts\picks_sync.py" --submit >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1

REM THE HOLD LIMIT RUNS NEXT, THEN THE SCAN.
REM
REM max_hold_bars=60 is how +0.218R was measured — 5.6% of holdout trades
REM exited on it rather than at a stop or a target. It was declared in
REM TradingPolicy and enforced NOWHERE: not one live trade has ever exited on
REM it, and every recorded outcome is a stop, a target or a weekend flatten.
REM
REM Before the scan rather than after, because the backtest frees the slot at
REM the same bar it exits. Closing first lets this cycle reuse the slot, which
REM is what was measured.
REM
REM It refuses to act when the market is shut, so the 16:03 ET run reports and
REM closes nothing. NOT watchdog-wrapped: it submits closing orders, and a
REM process that may have reached the broker must be reconciled rather than
REM blindly restarted.
".venv\Scripts\python.exe" "scripts\manage_positions.py" --execute >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1

REM --- SWEEP-SCANNER ENTRIES BACK ON, 2026-11-02 (FREEZE.md, operator) ---------
REM
REM Paused 2026-10-06 (-0.041R per trade on the unseen 2018-24 store, WORKLIST §30).
REM Its ghost (ops/ghost_scanner.py) then made +14.1R in 28 trades 10-07..10-30, and
REM the operator turned it back on as ONE BOOK of the account: 20% of equity
REM (policy.swing_allocation_pct), funded by ORB 10 -> 0 and the rules book 30 -> 20.
REM After protect.py and the hold limit above, as it always ran. A failed model or
REM broker pre-flight skips this run only; the kill switch file is set only by a
REM run of broker rejections (it would stop every book).
".venv\Scripts\python.exe" "scripts\trade.py" --submit-paper >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1

if %FAIL%==1 exit /b 1
exit /b 0
