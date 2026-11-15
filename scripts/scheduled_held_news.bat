@echo off
REM 07:15 and 11:15 AK weekdays: label news on the open book. No orders.
REM scripts/held_news.py writes logs\positions\held_news.json. Peak watch only reads it.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
set LOG=logs\scheduled_held_news.log
echo ===== HELD NEWS %DATE% %TIME% ===== >> "%LOG%"
".venv\Scripts\python.exe" "scripts\held_news.py" >> "%LOG%" 2>&1
exit /b %errorlevel%
