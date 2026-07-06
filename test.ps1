# Flipbook Maker - run the test suite (PowerShell)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "No virtual environment found. Run ./run.ps1 once first to create it." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Installing test dependencies..."
& $venvPy -m pip install -r requirements.txt -r requirements-dev.txt

Write-Host ""
Write-Host "Running tests..."
& $venvPy -m pytest
