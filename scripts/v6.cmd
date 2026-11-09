@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0v6.ps1" %*
set "V6_EXIT=%ERRORLEVEL%"
endlocal & exit /b %V6_EXIT%
