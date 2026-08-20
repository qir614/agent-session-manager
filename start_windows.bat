@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0CodexLocalSessionManager.exe" (
  start "" "%~dp0CodexLocalSessionManager.exe"
  exit /b 0
)

if exist "%~dp0dist\CodexLocalSessionManager.exe" (
  start "" "%~dp0dist\CodexLocalSessionManager.exe"
  exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run_gui.py
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python was not found and no packaged EXE is available.
    echo Download CodexLocalSessionManager.exe from the GitHub Releases page.
    pause
    exit /b 1
  )
  python run_gui.py
)
if errorlevel 1 pause
