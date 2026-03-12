param(
    [string]$TaskName = "KolJewelleryBackend",
    [string]$EnvFile = ".env.production",
    [int]$Workers = 1
)

$ErrorActionPreference = "Stop"
$baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $baseDir

$psExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path $psExe)) {
    throw "PowerShell executable not found at $psExe"
}

$runScript = Join-Path $baseDir "run_app.ps1"
if (-not (Test-Path $runScript)) {
    throw "run_app.ps1 not found in $baseDir"
}

$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`" -EnvFile `"$EnvFile`" -Workers $Workers"

$action = New-ScheduledTaskAction -Execute $psExe -Argument $arg
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "Task '$TaskName' installed and started."
Write-Host "Backend will auto-start on boot and auto-restart if it exits."
