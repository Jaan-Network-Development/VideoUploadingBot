@echo off
REM One-time setup on Windows. Requires Python 3.9+ (https://www.python.org/downloads/, tick "Add to PATH")
REM and Google Chrome (or Edge) installed.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m venv .venv || goto :err
.venv\Scripts\python -m pip install --upgrade pip || goto :err
.venv\Scripts\python -m pip install -r requirements.txt || goto :err
.venv\Scripts\python -m playwright install chromium || goto :err
echo.
echo Setup done. Next: login.bat (once), then run.bat
exit /b 0
:err
echo Setup failed.
exit /b 1
