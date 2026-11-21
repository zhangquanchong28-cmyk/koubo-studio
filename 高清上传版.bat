@echo off
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag your original video onto this icon. It makes a 1080p copy under 2GB for uploading to GitHub.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" shrink.py --hd "%~1"
pause
