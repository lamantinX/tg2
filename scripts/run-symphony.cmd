@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-symphony.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Symphony failed with exit code %EXIT_CODE%.
  pause
)
endlocal & exit /b %EXIT_CODE%
