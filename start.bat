@echo off
rem Start Kohya Dataset Tagger. The first run asks for the dataset root once and remembers it in roots.txt.
rem ASCII only: cmd.exe decodes .bat bytes with the console code page (936 here), so UTF-8 Chinese in
rem this file shows up as mojibake. Chinese messages belong to PowerShell, not to a .bat.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1" %*
if errorlevel 1 (
  echo.
  echo Startup failed. See the message above.
  pause
)
