@echo off
REM 05:45 AK weekdays. No model is asked. The Windows task is stock-scanner-picks.
REM rules_pick.py queues the 10-7 rules book on a refill day. small_pick.py keeps its buy checks.
REM Operator 2026-10-26: "stop Gemma from picking". Renamed 2026-11-10. The batch file name stays.
REM The gem- order prefix is unchanged.
REM Own log (WORKLIST §40): never share a log with another task.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled_rules_pick.log
echo. >> "%LOG%"
echo ===== RULES PICK %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\rules_pick.py" --submit >> "%LOG%" 2>&1
set RC=%errorlevel%
REM The small-cap book (operator 2026-10-27): S&P 600, IWM gate, 10%. Same log, after the rules pick.
echo ===== SMALL PICK %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\small_pick.py" --submit >> "%LOG%" 2>&1
if %RC% neq 0 exit /b %RC%
exit /b %errorlevel%
