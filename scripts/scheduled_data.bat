@echo off
REM Off-hours data collection — this is what "ready and loaded with the correct
REM data" actually requires.
REM
REM Running the TRADER around the clock would not do it: trade.py exits within
REM seconds when the market is closed, and the bar cache is TTL-based, so an
REM idle process holds nothing warm. What keeps the system ready is history
REM that keeps growing while the market is shut.
REM
REM collect_intraday.py accumulates 30-minute bars past Yahoo's 60-day window.
REM That window is the hard ceiling on every backtest here, and it only lifts
REM by saving bars before they age out — which cannot be done retroactively.

cd /d "%~dp0.."
if errorlevel 1 exit /b 1

set LOG=logs\scheduled.log
echo. >> "%LOG%"
echo ===== DATA %DATE% %TIME% ===== >> "%LOG%"

".venv\Scripts\python.exe" "scripts\collect_intraday.py" --index all >> "%LOG%" 2>&1
set RESULT=%errorlevel%

REM Gemma reviews any trades that closed today. Display only: the reviews are
REM shown on the desk and never read by the trader. Skips quietly if Ollama
REM is down, and must never fail the data collection above.
".venv\Scripts\python.exe" "scripts\hindsight.py" >> "%LOG%" 2>&1

exit /b %RESULT%
