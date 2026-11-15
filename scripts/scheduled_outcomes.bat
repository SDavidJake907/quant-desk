@echo off
REM Append what actually happened to each closed trade.
REM
REM READ-MOSTLY AND OUTSIDE THE FROZEN TRADING PATH. It reads the broker and
REM appends trade_outcome records. It places no order, cancels nothing and
REM touches no policy, so it cannot influence a trading decision and running it
REM does not end an evaluation window.
REM
REM 13:30 AK, half an hour after the data collector, an hour and a half after
REM the close. Late enough that the day's fills have settled at the broker.
REM
REM Idempotent by design: outcomes already in the log are skipped, so a missed
REM day is picked up by the next run and a double run writes nothing twice.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

REM One log per task (WORKLIST §40): a shared log locked out any task that
REM started while another was writing, and that task silently did not run.
set LOG=logs\scheduled_outcomes.log
echo. >> "%LOG%"
echo ===== OUTCOMES %DATE% %TIME% ===== >> "%LOG%"

REM Every step runs. The task fails on the first real failure. The broker
REM archive's exit is ignored on purpose (a snapshot must not paint the task red).
set FAIL=0
".venv\Scripts\python.exe" "scripts\record_outcomes.py" --write >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1

REM One scorecard split by strategy: scanner vs manual picks vs momentum
REM (WORKLIST §37). Read-only.
".venv\Scripts\python.exe" "scripts\strategy_scorecard.py" >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1

REM Second opinions graded: TradingAgents and the Gemma read against the S&P
REM over 21 sessions (WORKLIST §38). Advisory; read-only.

REM The competition (WORKLIST §46): snapshot both books, draw/advance the
REM month's ghost and benchmark paper books, then the standings table.
REM Ghost-scanner: the paused sweep scanner replayed on paper (no orders),
REM before the standings that show it.
".venv\Scripts\python.exe" "scripts\ghost_scanner.py" >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1
REM The book scorecard (2026-11-01): each book since the fresh start vs its benchmark,
REM the live sweep vs the ghost just updated above, and the acceleration shadow's grade.
".venv\Scripts\python.exe" "scripts\book_scorecard.py" >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1
".venv\Scripts\python.exe" "scripts\competition.py" snapshot >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1
".venv\Scripts\python.exe" "scripts\competition.py" standings >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1

REM Selection skill across the whole menu (WORKLIST §47): Gemma's chosen vs
REM passed-over names, TradingAgents' rank correlation. Feeds Gemma's scoreboard.

REM Mark the model's recorded calls against outcomes, the day they land.
REM Read-only, advisory; its exit code is ignored like the archive's.

REM --- archive what the broker holds -------------------------------
REM The audit log records what was DECIDED; the broker records what
REM FILLED, and only the first is in git. reconcile.py already names the
REM exposure: realized R is recomputed from order history the broker
REM prunes. Twelve closed trades is the only non-backtest evidence here.
REM
REM Routine pruning, an account reset, a key rotation or a CLEARING
REM MIGRATION would take it -- and a clearing change is the class of
REM event no identifier in FREEZE captures, the same blind spot as the
REM 23x5 market change on 2026-12-06.
REM
REM Dated snapshots, never overwritten, exit code ignored.
REM The penny watch-list shadow (operator 2026-10-27): records would-buys, grades vs IWM, never orders.
".venv\Scripts\python.exe" "scripts\penny_shadow.py" >> "%LOG%" 2>&1
if errorlevel 1 set FAIL=1
".venv\Scripts\python.exe" "scripts\archive_broker.py" >> "%LOG%" 2>&1
if %FAIL%==1 exit /b 1
exit /b 0
