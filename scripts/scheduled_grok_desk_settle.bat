@echo off
REM 13:15 AK (17:15 ET) weekdays: confirm Gemma's plan is signed. Does not call Grok. Claude is retired.
REM Exit 1 if a written plan is still unsigned. No orders. scripts/claude_desk.py verify --check.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_grok_desk_settle.log
echo ===== GROK VERIFY CHECK %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\claude_desk.py" verify --check >> "%LOG%" 2>&1
exit /b %errorlevel%
