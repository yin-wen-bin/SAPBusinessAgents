@echo off
setlocal
title SAPBusinessAgents Launcher

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-SAPBusinessAgents.ps1" -Restart %*
if errorlevel 1 (
  echo.
  echo SAPBusinessAgents failed to start. Review the error above.
  pause
  exit /b 1
)

endlocal
