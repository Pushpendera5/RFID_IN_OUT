param(
    [Parameter(Mandatory = $true)]
    [string]$DbServer,

    [int]$DbPort = 1433,

    [Parameter(Mandatory = $true)]
    [string]$DbName,

    [Parameter(Mandatory = $true)]
    [string]$DbUser,

    [Parameter(Mandatory = $true)]
    [string]$DbPassword,

    [string]$AppHost = "0.0.0.0",
    [int]$AppPort = 8000,
    [string]$CorsOrigin = "",
    [string]$AdminUsername = "admin",
    [string]$AdminPassword = "admin123",
    [switch]$UseHttps
)

$ErrorActionPreference = "Stop"
$baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $baseDir

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $lines = @()
    if (Test-Path $Path) {
        $lines = Get-Content -LiteralPath $Path
    }

    $pattern = "^\s*" + [regex]::Escape($Key) + "\s*="
    $updated = $false

    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = "$Key=$Value"
            $updated = $true
        }
    }

    if (-not $updated) {
        $lines += "$Key=$Value"
    }

    Set-Content -LiteralPath $Path -Value $lines -Encoding UTF8
}

function New-SecretKey {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd("=")
}

Write-Host "[1/5] Creating Python virtual environment..."
$venvPython = Join-Path $baseDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    python -m venv .venv
}

Write-Host "[2/5] Installing dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

Write-Host "[3/5] Preparing environment file..."
$envFile = Join-Path $baseDir ".env.production"
$envExample = Join-Path $baseDir ".env.production.example"
if (-not (Test-Path $envFile)) {
    Copy-Item -Path $envExample -Destination $envFile -Force
}

if (-not $CorsOrigin) {
    $CorsOrigin = "http://localhost:$AppPort"
}

$secret = New-SecretKey
$cookieSecure = if ($UseHttps) { "true" } else { "false" }

Set-EnvValue -Path $envFile -Key "APP_ENV" -Value "production"
Set-EnvValue -Path $envFile -Key "APP_HOST" -Value $AppHost
Set-EnvValue -Path $envFile -Key "APP_PORT" -Value "$AppPort"
Set-EnvValue -Path $envFile -Key "APP_FAIL_FAST" -Value "false"
Set-EnvValue -Path $envFile -Key "CORS_ALLOW_ORIGINS" -Value $CorsOrigin

Set-EnvValue -Path $envFile -Key "DB_SERVER" -Value $DbServer
Set-EnvValue -Path $envFile -Key "DB_PORT" -Value "$DbPort"
Set-EnvValue -Path $envFile -Key "DB_NAME" -Value $DbName
Set-EnvValue -Path $envFile -Key "DB_TRUSTED_CONNECTION" -Value "no"
Set-EnvValue -Path $envFile -Key "DB_USERNAME" -Value $DbUser
Set-EnvValue -Path $envFile -Key "DB_PASSWORD" -Value $DbPassword
Set-EnvValue -Path $envFile -Key "DB_ENCRYPT" -Value "no"
Set-EnvValue -Path $envFile -Key "DB_TRUST_SERVER_CERTIFICATE" -Value "yes"

Set-EnvValue -Path $envFile -Key "COOKIE_SECURE" -Value $cookieSecure
Set-EnvValue -Path $envFile -Key "SECRET_KEY" -Value $secret
Set-EnvValue -Path $envFile -Key "BOOTSTRAP_ADMIN_USERNAME" -Value $AdminUsername
Set-EnvValue -Path $envFile -Key "BOOTSTRAP_ADMIN_PASSWORD" -Value $AdminPassword
Set-EnvValue -Path $envFile -Key "BOOTSTRAP_ADMIN_ROLE" -Value "admin"

Write-Host "[4/5] Checking SQL connectivity..."
$sqlCmd = Get-Command sqlcmd -ErrorAction SilentlyContinue
if ($sqlCmd) {
    & sqlcmd -S "$DbServer,$DbPort" -U $DbUser -P $DbPassword -d $DbName -Q "SELECT 1"
    if ($LASTEXITCODE -ne 0) {
        throw "SQL connectivity test failed. Check DB server/user/password/port."
    }
} else {
    Write-Host "sqlcmd not found. Skipping DB test."
}

Write-Host "[5/5] Setup complete."
Write-Host ""
Write-Host "Next commands:"
Write-Host "1) .\run_app.ps1"
Write-Host "2) Open http://<THIS_PC_IP>:$AppPort/login"
