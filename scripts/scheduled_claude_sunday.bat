@echo off
REM 14:30 AK (18:30 ET) Sundays, after futures open: Claude's Sunday read to logs/claude/sunday.md. No orders.
REM scripts/claude_desk.py sunday
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_claude_sunday.log
echo ===== CLAUDE SUNDAY %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\claude_desk.py" sunday >> "%LOG%" 2>&1
exit /b %errorlevel%
