@echo off
setlocal
title FigmaToFGUI Client Installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0internal\Install-Client.ps1"
set "installerExitCode=%ERRORLEVEL%"
echo.
if not "%installerExitCode%"=="0" (
  echo Installation failed. Exit code: %installerExitCode%
  echo Keep this window open and send the error text above to the maintainer.
  pause
  exit /b %installerExitCode%
)
echo Installation completed successfully.
echo The plugin manifest folder should now be open in File Explorer.
pause
exit /b 0
