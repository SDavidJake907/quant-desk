@echo off
REM 13:25 AK (17:25 ET) weekdays: play Gemma's calls in her shadow book. No orders.
REM scripts/claude_desk.py --desk gemma settle
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_gemma_desk_settle.log
echo ===== GEMMA DESK SETTLE %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\claude_desk.py" --desk gemma settle >> "%LOG%" 2>&1
exit /b %errorlevel%
