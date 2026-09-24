@echo off
set "SUITE_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SUITE_DIR%install.ps1" -InstallMissingPython %*
if errorlevel 1 (
  echo.
  echo Deployment failed. Keep this window open and send the error text.
  pause
)
