@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Local.ps1"
if errorlevel 1 (
  echo.
  echo Startup failed. Copy the red error above to the project maintainer.
  pause
)
