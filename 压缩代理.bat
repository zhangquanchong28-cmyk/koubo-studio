@echo off
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag your original video onto this icon. It will be shrunk to under 30MB for uploading to Claude.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" shrink.py "%~1"
pause
