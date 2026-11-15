@echo off
REM 06:15 AK (10:15 ET) weekdays: Grok approves or disapproves Gemma's plan. Claude is retired.
REM No book and no orders. He cannot add a symbol. scripts/claude_desk.py verify.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_grok_desk_plan.log
echo ===== GROK VERIFY %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\claude_desk.py" verify >> "%LOG%" 2>&1
exit /b %errorlevel%
