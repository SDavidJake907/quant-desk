@echo off
REM Pre-close four-sign check (scripts/preclose_check.py). Two triggers, 08:40 and 11:40 AK
REM (12:40 / 15:40 ET); only the run within 25 minutes of the bell acts, so 13:00 ET early closes
REM are covered. Sells only the SPY core, only when losing and all four signs confirm.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_preclose.log
echo. >> "%LOG%"
echo ===== PRECLOSE %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\preclose_check.py" --submit --only-if-closing-within 25 >> "%LOG%" 2>&1
exit /b %errorlevel%
