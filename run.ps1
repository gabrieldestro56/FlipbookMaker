# Flipbook Maker - one-click launcher (PowerShell)
# Usage:  right-click -> "Run with PowerShell", or:  ./run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Python {
    foreach ($cmd in @("py", "python")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            if ($cmd -eq "py") { return @("py", "-3") }
            return @("python")
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "[ERROR] Python 3 was not found on your PATH." -ForegroundColor Red
    Write-Host "Install Python 3.9+ from https://www.python.org/downloads/ and run this again."
    Read-Host "Press Enter to exit"
    exit 1
}

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# Create the virtual environment on first run.
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating virtual environment..."
    $exe = $py[0]
    $rest = if ($py.Count -gt 1) { $py[1..($py.Count - 1)] } else { @() }
    & $exe @rest -m venv .venv
}

# Install deps if missing or requirements.txt changed.
$depsMarker = ".venv\.deps.txt"
$needInstall = $true
if (Test-Path $depsMarker) {
    $a = (Get-FileHash requirements.txt).Hash
    $b = (Get-FileHash $depsMarker).Hash
    if ($a -eq $b) { $needInstall = $false }
}
if ($needInstall) {
    Write-Host "Installing dependencies (first run or requirements changed)..."
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r requirements.txt
    Copy-Item requirements.txt $depsMarker -Force
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  Flipbook Maker is starting at http://127.0.0.1:8090/" -ForegroundColor Green
Write-Host "  Your browser will open automatically. Close this window to stop." -ForegroundColor Green
Write-Host "  Tip: if the page looks old, press Ctrl+Shift+R to hard-refresh." -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
& $venvPy app.py
