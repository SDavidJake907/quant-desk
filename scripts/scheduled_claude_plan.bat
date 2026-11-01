@echo off
REM Claude's research desk, morning plan (scripts/claude_desk.py plan): 04:45 AK (08:45 ET) weekdays.
REM Claude, headless with read + web tools only, writes per-stock calls to data/claude/plans/.
REM Shadow only -- no order is placed. Operator 2026-10-31 ("lets have you do the research").
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_claude_plan.log
echo ===== CLAUDE PLAN %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\claude_desk.py" plan >> "%LOG%" 2>&1
exit /b %errorlevel%
