@echo off
setlocal
title SAPBusinessAgents Launcher
set "SAPBA_NATIVE_ARGS="

rem Plain double-click avoids loading PowerShell before the shared orchestrator.
rem Unknown/complex parameters retain the existing PowerShell parsing contract.
if "%~1"=="" goto native
if /I not "%~1"=="-NoBrowser" goto parameters
if "%~2"=="" (
  set "SAPBA_NATIVE_ARGS=--no-browser"
  goto native
)
if /I not "%~2"=="-VerifyReuseOnly" goto parameters
if not "%~3"=="" goto parameters
set "SAPBA_NATIVE_ARGS=--no-browser --reuse-only"
goto native

:parameters
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-SAPBusinessAgents.ps1" %*
goto result

:native
set "PUBLIC_SAPBA_API_URL=http://127.0.0.1:8765"
set "SAPBA_INTERNAL_API_URL=http://127.0.0.1:8765"
"%~dp0.venv\Scripts\python.exe" -I -B "%~dp0scripts\start-active.py" ensure --root "%~dp0." %SAPBA_NATIVE_ARGS%

:result
if errorlevel 1 (
  echo.
  echo SAPBusinessAgents failed to start. Review the error above.
  pause
  exit /b 1
)

endlocal
