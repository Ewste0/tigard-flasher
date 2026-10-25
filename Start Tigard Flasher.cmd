@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call "%~dp0Setup Windows.cmd"
  exit /b
)
".venv\Scripts\python.exe" launch.py %*
