@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "ENV_FILE=.env.production"
if not exist "%ENV_FILE%" (
    if exist ".env.production.example" (
        copy /y ".env.production.example" "%ENV_FILE%" >nul
    )
)

if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        set "K=%%~A"
        set "V=%%~B"
        if defined K (
            if not "!K:~0,1!"=="#" (
                set "!K!=!V!"
            )
        )
    )
)

if not defined APP_HOST set "APP_HOST=0.0.0.0"
if not defined APP_PORT set "APP_PORT=8000"

if /I "%~1"=="--check" (
    echo APP_HOST=%APP_HOST%
    echo APP_PORT=%APP_PORT%
    echo DB_SERVER=%DB_SERVER%
    echo DB_NAME=%DB_NAME%
    exit /b 0
)

if exist "reader.lock" del /f /q "reader.lock" >nul 2>nul

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo Starting backend on %APP_HOST%:%APP_PORT%
"%PYTHON_EXE%" -m uvicorn main:app --host %APP_HOST% --port %APP_PORT%

endlocal
