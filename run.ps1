# Flipbook Maker - one-click launcher (PowerShell)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    & ".\.venv\Scripts\Activate.ps1"
    Write-Host "Installing dependencies..."
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
} else {
    & ".\.venv\Scripts\Activate.ps1"
}

python app.py
