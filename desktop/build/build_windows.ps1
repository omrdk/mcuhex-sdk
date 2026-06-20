$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = (Resolve-Path "$ScriptDir\..\..").Path
$DesktopDir  = Join-Path $ProjectRoot "desktop"
$BuildDir    = $ScriptDir
$DistDir     = Join-Path $BuildDir "dist"

Write-Host "=== MCUHex Windows Build ==="

if (Test-Path (Join-Path $BuildDir "build")) {
    Remove-Item (Join-Path $BuildDir "build") -Recurse -Force
}
if (Test-Path $DistDir) {
    Remove-Item $DistDir -Recurse -Force
}

$VenvDir = Join-Path $ProjectRoot ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Error "Project venv not found at $VenvDir. Run: python -m venv .venv; pip install -r requirements.txt"
}
& (Join-Path $VenvDir "Scripts\Activate.ps1")

$PyVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($PyVersion -ne "3.11") {
    Write-Error "Build venv is Python $PyVersion; this project ships Python 3.11 exclusively. Recreate the venv with py -3.11 -m venv .venv"
}

# Use "python -m" instead of bare pip/pyinstaller -- the launcher .exe stubs
# bake in absolute paths at venv-creation time and break if the folder moves.
python -m pip install $ProjectRoot --no-deps --force-reinstall -q
python -m pip install -r (Join-Path $DesktopDir "requirements-desktop.txt")

Push-Location $BuildDir
python -m PyInstaller mcuhex.spec --noconfirm
Pop-Location

$OnedirPath = Join-Path $DistDir "MCUHex"
if (-not (Test-Path $OnedirPath)) {
    Write-Error "PyInstaller did not produce $OnedirPath - build failed. Check the output above."
}

$Version = python -c "from desktop.config import VERSION; print(VERSION)"
$ZipName = "MCUHex-$Version-windows-amd64.zip"
$ZipPath = Join-Path $DistDir $ZipName

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

Compress-Archive -Path $OnedirPath -DestinationPath $ZipPath

if (-not (Test-Path $ZipPath)) {
    Write-Error "Compress-Archive failed - $ZipPath was not created."
}

Remove-Item $OnedirPath -Recurse -Force

Write-Host "ZIP created: $ZipPath"
Write-Host "=== Done ==="
