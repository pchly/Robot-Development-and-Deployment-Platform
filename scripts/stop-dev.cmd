@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-dev.ps1" %*
exit /b %ERRORLEVEL%
