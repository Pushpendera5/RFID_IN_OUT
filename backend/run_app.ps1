param(
    [string]$EnvFile = ".env.production",
    [int]$Workers = 1
)

$ErrorActionPreference = "Stop"
$baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $baseDir

if (Test-Path "reader.lock") {
    Remove-Item "reader.lock" -Force -ErrorAction SilentlyContinue
}

& "$baseDir\start_production.ps1" -EnvFile $EnvFile -Workers $Workers
