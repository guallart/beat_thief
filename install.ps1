# BeatThief installer for Windows (PowerShell).
# Creates .venv, installs the Python dependencies, makes sure Python and FFmpeg are
# present (via winget) and writes a run.bat launcher. Safe to run again.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "error: $m" -ForegroundColor Red; exit 1 }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Winget-Install($id) {
    if (-not (Have winget)) { return $false }
    winget install --id $id -e --accept-package-agreements --accept-source-agreements
    Refresh-Path
    return $true
}

# --- Python (3.10+) ---------------------------------------------------------
function Find-Python {
    foreach ($cmd in @(@("py", "-3"), @("python"))) {
        if (-not (Have $cmd[0])) { continue }
        $extra = @($cmd | Select-Object -Skip 1)
        & $cmd[0] @extra -c "import sys; sys.exit(sys.version_info < (3,10))" 2>$null
        if ($LASTEXITCODE -eq 0) { return $cmd }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    Say "Python 3.10+ not found, installing it with winget"
    if (-not (Winget-Install "Python.Python.3.12")) { Fail "Install Python 3.10+ from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run this script again." }
    $py = Find-Python
    if (-not $py) { Fail "Python was installed but is not on PATH yet. Open a new terminal and run this script again." }
}
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)

# --- FFmpeg -----------------------------------------------------------------
if (-not (Have ffmpeg)) {
    Say "FFmpeg not found, installing it with winget"
    if (-not (Winget-Install "Gyan.FFmpeg")) { Fail "Install FFmpeg (https://www.gyan.dev/ffmpeg/builds/) and add it to PATH, then run this script again." }
    if (-not (Have ffmpeg)) { Write-Host "FFmpeg was installed but isn't visible in this session yet. Open a new terminal before starting BeatThief." -ForegroundColor Yellow }
}

# --- virtualenv + dependencies ---------------------------------------------
Say "Creating virtual environment in .venv"
& $pyExe @pyArgs -m venv .venv
if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment." }
Say "Installing Python dependencies"
& .venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Could not install the dependencies." }

# --- launcher ---------------------------------------------------------------
@'
@echo off
cd /d "%~dp0"
if "%~1"=="" (".venv\Scripts\python.exe" -m beatthief web) else (".venv\Scripts\python.exe" -m beatthief %*)
'@ | Set-Content -Encoding ASCII run.bat

Say "Done."
Write-Host ""
Write-Host "Start the web interface with:   .\run.bat   (then open http://127.0.0.1:8000)"
Write-Host "Or use the command line:        .\run.bat download `"<url>`" --format flac"
