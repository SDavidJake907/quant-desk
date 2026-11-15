@echo off
REM Weekdays 05:33-12:03 Alaska, every 30 min: profit given back on each open position.
REM Report only. scripts/position_watch.py places no order and moves no stop.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_position_watch.log
echo ===== POSITION WATCH %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\position_watch.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
