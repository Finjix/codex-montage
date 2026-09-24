@echo off
set "SUITE_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SUITE_DIR%install.ps1" %*
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Deployment failed. Keep this window open and send the error text.
  pause
)
exit /b %EXIT_CODE%
