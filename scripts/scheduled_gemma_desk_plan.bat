@echo off
REM 04:30 AK (08:30 ET) weekdays: Gemma's own shadow plan from the same brief. No orders.
REM scripts/claude_desk.py --desk gemma plan
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_gemma_desk_plan.log
echo ===== GEMMA DESK PLAN %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\claude_desk.py" --desk gemma plan >> "%LOG%" 2>&1
exit /b %errorlevel%
