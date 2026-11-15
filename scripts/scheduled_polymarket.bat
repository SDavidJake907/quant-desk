@echo off
REM 05:15 AK weekdays: Polymarket odds for the open book. No bet, no order.
REM scripts/polymarket_watch.py writes logs\positions\polymarket.md.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_polymarket.log
echo ===== POLYMARKET %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\polymarket_watch.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
