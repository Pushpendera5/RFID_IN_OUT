@echo off
setlocal EnableExtensions

set "TASK_NAME=KolJewelleryBackend"

schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 (
    echo Task "%TASK_NAME%" not found or cannot be removed.
    exit /b 1
)

echo Task "%TASK_NAME%" removed.
endlocal
