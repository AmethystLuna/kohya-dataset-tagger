@echo off
rem Initialize the Python environment (idempotent). Defaults to the DirectML build of onnxruntime.
rem For China mirrors use setup_env_cn.bat (same script with -Index cn).
rem ASCII only -- see start.bat for why.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup_env.ps1" %*
echo.
pause
