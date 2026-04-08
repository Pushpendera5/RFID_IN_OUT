param(
    [switch]$Smoke,
    [switch]$Headless
)

$ErrorActionPreference = "Stop"
$baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $baseDir

$python = Join-Path $baseDir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$args = @("desktop_launcher.py")
if ($Smoke) {
    $args += "--smoke"
}
if ($Headless) {
    $args += "--headless"
}

& $python @args
