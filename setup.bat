@echo off
setlocal EnableExtensions
REM One-time (re-runnable) setup on Windows. Usage: setup.bat [/nopause]
REM Finds or installs Python 3.9+, builds .venv, installs dependencies (with retries),
REM checks Chrome/Edge, and creates a .env template. Safe to run again at any time.
cd /d "%~dp0"
set "NOPAUSE="
if /i "%~1"=="/nopause" set "NOPAUSE=1"
set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PY="
set "VENVPY=%~dp0.venv\Scripts\python.exe"

echo === 1/6 Looking for Python 3.9+ ===
call :findpy
if defined PY goto :havepy

echo Python 3.9+ not found.
where winget >nul 2>nul
if errorlevel 1 goto :nopython
echo Trying to install Python 3.12 with winget (per-user, no admin needed) ...
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
call :findpy
if defined PY goto :havepy
goto :nopython

:havepy
echo Using: %PY%

echo === 2/6 Virtual environment ===
if exist "%VENVPY%" (
    "%VENVPY%" -c "import sys" >nul 2>nul
    if errorlevel 1 (
        echo Existing .venv is broken or was made on another machine - recreating it.
        rmdir /s /q ".venv"
    )
)
if not exist "%VENVPY%" (
    %PY% -m venv .venv
    if errorlevel 1 goto :venvretry
)
goto :venvdone
:venvretry
echo venv failed - removing partial .venv and retrying once ...
if exist ".venv" rmdir /s /q ".venv"
%PY% -m venv .venv || goto :err
:venvdone
if not exist "%VENVPY%" goto :err

echo === 3/6 Installing dependencies ===
"%VENVPY%" -m pip install --upgrade pip
if errorlevel 1 echo pip upgrade failed - continuing with the bundled pip.
set "TRY=0"
:pipretry
set /a TRY+=1
"%VENVPY%" -m pip install -r requirements.txt
if not errorlevel 1 goto :pipok
if %TRY% GEQ 3 goto :neterr
echo pip install failed - retrying %TRY% of 3 in 5 seconds ...
timeout /t 5 /nobreak >nul
goto :pipretry
:pipok

echo === 4/6 Installing Playwright browser ===
set "TRY=0"
:pwretry
set /a TRY+=1
"%VENVPY%" -m playwright install chromium
if not errorlevel 1 goto :pwok
if %TRY% GEQ 3 goto :pwwarn
echo playwright install failed - retrying %TRY% of 3 in 5 seconds ...
timeout /t 5 /nobreak >nul
goto :pwretry
:pwwarn
echo WARNING: could not download Playwright Chromium. The bot drives your installed Chrome or Edge,
echo so this is usually fine. Run setup.bat again later if you need it.
:pwok

echo === 5/6 Checking Chrome / Edge ===
set "BROWSER="
for %%P in (
    "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
    "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
) do if exist %%P set "BROWSER=%%~P"
if not defined BROWSER goto :nobrowser
echo Found: %BROWSER%
goto :browserdone
:nobrowser
echo WARNING: Google Chrome or Edge not found. Install Chrome from https://www.google.com/chrome/
echo before running login.bat or run.bat.
:browserdone

echo === 6/6 Config file ===
if exist ".env" goto :envexists
> ".env" echo SITE_LOGIN=
>> ".env" echo SITE_PASSWORD=
>> ".env" echo MYUPLOAD_API_KEY=
echo Created .env - open it and fill in SITE_LOGIN, SITE_PASSWORD and MYUPLOAD_API_KEY.
goto :envdone
:envexists
echo .env already exists - left unchanged.
:envdone

echo.
echo Setup done. Next: login.bat (once), then run.bat
call :end
exit /b 0

:nopython
echo.
echo Python 3.9+ was not found and could not be installed automatically.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo then close this window, open a new one and run setup.bat again.
echo (If Windows opens the Microsoft Store instead, turn off "App execution aliases" for python.exe
echo  in Settings - Apps - Advanced app settings.)
call :end
exit /b 1

:neterr
echo.
echo Could not install the dependencies. Check your internet connection, proxy or VPN/firewall, then run setup.bat again.
call :end
exit /b 1

:err
echo.
echo Setup failed. Scroll up for the error message.
call :end
exit /b 1

:end
if not defined NOPAUSE pause
exit /b 0

REM ---- sets PY to the first working Python >= 3.9 (ignores the Microsoft Store stub) ----
:findpy
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY call :trypy %%~C
)
if defined PY exit /b 0
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*" "%ProgramFiles(x86)%\Python3*") do (
    if not defined PY if exist "%%~D\python.exe" call :trypy "%%~D\python.exe"
)
exit /b 0

:trypy
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if not errorlevel 1 set PY=%*
exit /b 0
