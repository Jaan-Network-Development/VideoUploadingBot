@echo off
REM One-time: log in by hand (solve the captcha); the session is saved to storage_state.json.
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python login.py %*
