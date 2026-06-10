@echo off
REM Flipbook Maker - one-click launcher (Windows)
setlocal
cd /d "%~dp0"

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    echo Installing dependencies...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
) else (
    call ".venv\Scripts\activate.bat"
)

python app.py
endlocal
