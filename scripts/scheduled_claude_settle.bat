@echo off
REM Claude's research desk, after the close (scripts/claude_desk.py settle): 13:20 AK (17:20 ET)
REM weekdays. Plays the day's plan in the shadow book against daily bars. Places no order.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_claude_settle.log
echo ===== CLAUDE SETTLE %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\claude_desk.py" settle >> "%LOG%" 2>&1
exit /b %errorlevel%
