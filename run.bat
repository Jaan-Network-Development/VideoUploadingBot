@echo off
REM Usage: run.bat [--pick]   Uploads every model folder, then keeps watching for new downloads.
REM Reads the downloader's folder (..\VideoDowloaderBot\downloads) unless VIDEOBOT_DOWNLOADS is set.
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
.venv\Scripts\python run.py --watch %*
