@echo off
rem Same as setup_env.bat, but installs through China mirrors (USTC, Aliyun, then pypi.org).
rem The real switch lives in scripts\setup_env.ps1 -Index cn. ASCII only -- see start.bat.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup_env.ps1" -Index cn %*
echo.
pause
