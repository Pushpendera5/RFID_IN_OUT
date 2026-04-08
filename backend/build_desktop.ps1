param(
    [string]$Name = "KolJewelleryDesktop"
)

$ErrorActionPreference = "Stop"
$baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $baseDir

$python = Join-Path $baseDir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

& $python -m pip install -r requirements-desktop.txt

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $Name `
    --distpath "$baseDir\dist" `
    --workpath "$baseDir\build" `
    --specpath "$baseDir" `
    --hidden-import webview.platforms.winforms `
    --hidden-import webview.platforms.edgechromium `
    desktop_launcher.py

Write-Host "Desktop EXE built at: $baseDir\dist\$Name.exe"
