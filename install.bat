@echo off
REM install.bat - Double-click this to install Pawmodoro on Windows.
REM Just runs install.ps1 with the execution-policy restriction bypassed
REM for this one script (doesn't change your system's PowerShell policy).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
