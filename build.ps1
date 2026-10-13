# Builds dist\SpotifyMonthlyPlaylists.exe (single-file, no console window).
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    python -m venv .venv
    & $py -m pip install --upgrade pip
    & $py -m pip install -r requirements-dev.txt
}

& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; not building." }

& $py -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name SpotifyMonthlyPlaylists `
    --collect-data customtkinter `
    main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

Write-Host "Built: $(Resolve-Path dist\SpotifyMonthlyPlaylists.exe)"
