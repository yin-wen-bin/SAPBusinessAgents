@echo off
setlocal
title SAPBusinessAgents - Explicit Restart
call "%~dp0start-sap-business-agents.cmd" -Restart %*
exit /b %errorlevel%
