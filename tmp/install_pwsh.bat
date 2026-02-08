@echo off
winget install --id Microsoft.PowerShell --source winget --accept-package-agreements --accept-source-agreements 2>&1
echo EXIT_CODE=%ERRORLEVEL%
