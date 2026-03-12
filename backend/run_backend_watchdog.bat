@echo off
setlocal EnableExtensions

cd /d "%~dp0"
title KolJewelleryBackendWatchdog

:loop
call "%~dp0run_backend.bat"
echo [%date% %time%] Backend stopped with code %errorlevel%. Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
