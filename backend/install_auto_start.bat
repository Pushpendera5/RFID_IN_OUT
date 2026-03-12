@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "TASK_NAME=KolJewelleryBackend"
set "RUN_FILE=%~dp0run_backend_watchdog.bat"
set "TASK_CMD=cmd.exe /c ""%RUN_FILE%"""

echo Creating startup task: %TASK_NAME%
schtasks /Create /TN "%TASK_NAME%" /SC ONSTART /RU "SYSTEM" /RL HIGHEST /TR "%TASK_CMD%" /F
if errorlevel 1 (
    echo Failed to create task. Run this file as Administrator.
    exit /b 1
)

echo Starting task now...
schtasks /Run /TN "%TASK_NAME%"

echo Done.
echo Backend will auto-start after reboot/power loss and auto-restart if it stops.
endlocal
