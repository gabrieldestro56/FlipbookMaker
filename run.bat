@echo off
REM Flipbook Maker - one-click launcher (Windows)
REM Double-click this file. It sets up a virtual environment on first run,
REM installs (or updates) dependencies, then starts the app in your browser.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

REM --- locate a Python 3 interpreter -------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python 3 was not found on your PATH.
    echo Install Python 3.9+ from https://www.python.org/downloads/ ^(tick
    echo "Add python.exe to PATH" in the installer^) and run this again.
    goto :fail
)

REM --- create the virtual environment on first run ----------------------
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)
set "VENV_PY=.venv\Scripts\python.exe"

REM --- install deps if missing or requirements.txt changed --------------
set "NEED_INSTALL="
if not exist ".venv\.deps.txt" (
    set "NEED_INSTALL=1"
) else (
    fc /b requirements.txt ".venv\.deps.txt" >nul 2>nul || set "NEED_INSTALL=1"
)
if defined NEED_INSTALL (
    echo Installing dependencies ^(first run or requirements changed^)...
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 goto :fail
    copy /y requirements.txt ".venv\.deps.txt" >nul
)

echo.
echo ================================================================
echo   Flipbook Maker is starting at http://127.0.0.1:8090/
echo   Your browser will open automatically. Close this window to stop.
echo   Tip: if the page looks old, press Ctrl+Shift+R to hard-refresh.
echo ================================================================
echo.
"%VENV_PY%" app.py
if errorlevel 1 goto :fail

endlocal
exit /b 0

:fail
echo.
echo [ERROR] Something went wrong - see the messages above.
pause
exit /b 1
