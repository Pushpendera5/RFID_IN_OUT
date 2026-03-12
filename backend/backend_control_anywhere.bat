@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "ACTION=%~1"
if not defined ACTION set "ACTION=start"
set "ACTION=%ACTION:"=%"

for /f "tokens=* delims= " %%A in ("%USERPROFILE%") do set "USERPROFILE=%%~A"
set "CFG_FILE=backend_path.txt"

if /I "%ACTION%"=="help" goto :help
if /I "%ACTION%"=="configure" goto :configure
if /I "%ACTION%"=="start" goto :start
if /I "%ACTION%"=="stop" goto :stop
if /I "%ACTION%"=="restart" goto :restart
if /I "%ACTION%"=="status" goto :status

echo Unknown action: %ACTION%
goto :help

:help
echo.
echo Usage:
echo   backend_control_anywhere.bat configure "C:\path\to\Kol_jewellery\backend"
echo   backend_control_anywhere.bat start
echo   backend_control_anywhere.bat stop
echo   backend_control_anywhere.bat restart
echo   backend_control_anywhere.bat status
echo.
exit /b 0

:configure
set "NEW_PATH=%~2"
if not defined NEW_PATH (
    set /p NEW_PATH=Enter full backend path:
)
for /f "tokens=* delims= " %%A in ("%NEW_PATH%") do set "NEW_PATH=%%~A"
if not exist "%NEW_PATH%\main.py" (
    echo Invalid backend path. main.py not found in: %NEW_PATH%
    exit /b 1
)
echo %NEW_PATH%>"%CFG_FILE%"
echo Saved backend path: %NEW_PATH%
exit /b 0

:resolve_backend
set "BACKEND_DIR="

if defined KOL_BACKEND_DIR (
    if exist "%KOL_BACKEND_DIR%\main.py" set "BACKEND_DIR=%KOL_BACKEND_DIR%"
)

if not defined BACKEND_DIR (
    if exist "%CFG_FILE%" (
        set /p SAVED_PATH=<"%CFG_FILE%"
        if exist "!SAVED_PATH!\main.py" set "BACKEND_DIR=!SAVED_PATH!"
    )
)

if not defined BACKEND_DIR (
    if exist "%USERPROFILE%\Desktop\Kol_jewellery\backend\main.py" (
        set "BACKEND_DIR=%USERPROFILE%\Desktop\Kol_jewellery\backend"
    )
)

if not defined BACKEND_DIR (
    if exist "%~dp0main.py" set "BACKEND_DIR=%~dp0"
)

if not defined BACKEND_DIR (
    if exist "%~dp0backend\main.py" set "BACKEND_DIR=%~dp0backend"
)

if not defined BACKEND_DIR (
    echo Could not find backend folder.
    echo First run:
    echo   %~nx0 configure "C:\path\to\Kol_jewellery\backend"
    exit /b 1
)
exit /b 0

:load_app_port
set "APP_PORT=8000"
set "ENV_FILE=%BACKEND_DIR%\.env.production"
if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /I "%%~A"=="APP_PORT" (
            set "APP_PORT=%%~B"
        )
    )
)
exit /b 0

:start
call :resolve_backend || exit /b 1
if not exist "%BACKEND_DIR%\run_backend_watchdog.bat" (
    echo run_backend_watchdog.bat not found in: %BACKEND_DIR%
    exit /b 1
)
start "KolJewelleryBackendWatchdog" cmd /c ""%BACKEND_DIR%\run_backend_watchdog.bat""
echo Backend start command sent.
exit /b 0

:stop
call :resolve_backend || exit /b 1
call :load_app_port

rem stop scheduled task instance if installed
schtasks /Query /TN "KolJewelleryBackend" >nul 2>nul
if %errorlevel%==0 schtasks /End /TN "KolJewelleryBackend" >nul 2>nul

rem stop watchdog console instances started manually
taskkill /FI "WINDOWTITLE eq KolJewelleryBackendWatchdog*" /T /F >nul 2>nul

rem stop uvicorn/python bound to APP_PORT
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%APP_PORT% .*LISTENING"') do (
    taskkill /PID %%P /T /F >nul 2>nul
)

if exist "%BACKEND_DIR%\reader.lock" del /f /q "%BACKEND_DIR%\reader.lock" >nul 2>nul
echo Backend stop command sent.
exit /b 0

:restart
call :stop
timeout /t 2 /nobreak >nul
call :start
exit /b 0

:status
call :resolve_backend || exit /b 1
call :load_app_port

set "FOUND="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%APP_PORT% .*LISTENING"') do (
    echo Backend listening on port %APP_PORT% ^(PID %%P^)
    set "FOUND=1"
)

if not defined FOUND (
    echo Backend not listening on port %APP_PORT%.
)

exit /b 0
