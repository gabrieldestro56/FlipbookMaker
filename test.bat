@echo off
REM Flipbook Maker - run the test suite (Windows)
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No virtual environment found. Run run.bat once first to create it.
    pause
    exit /b 1
)
set "VENV_PY=.venv\Scripts\python.exe"

echo Installing test dependencies...
"%VENV_PY%" -m pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 goto :fail

echo.
echo Running tests...
"%VENV_PY%" -m pytest
if errorlevel 1 goto :fail

endlocal
exit /b 0

:fail
echo.
echo [ERROR] Tests failed or setup went wrong - see above.
pause
exit /b 1
