@echo off
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0check-repository.ps1" %*
exit /b %ERRORLEVEL%
