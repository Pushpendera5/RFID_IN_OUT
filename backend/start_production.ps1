param(
    [string]$EnvFile = ".env.production",
    [int]$Workers = 1
)

$ErrorActionPreference = "Stop"
$baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $baseDir

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            $name = $parts[0].Trim()
            $value = $parts[1].Trim().Trim('"').Trim("'")
            if ($name) {
                [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
}

$python = Join-Path $baseDir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$hostArg = if ($env:APP_HOST) { $env:APP_HOST } else { "0.0.0.0" }
$portArg = if ($env:APP_PORT) { $env:APP_PORT } else { "8000" }

& $python -m uvicorn main:app --host $hostArg --port $portArg --workers $Workers --proxy-headers
