@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create .venv and install the package first; see README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" launch.py %*
