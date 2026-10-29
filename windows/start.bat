@echo off
setlocal
cd /d "%~dp0.."

if exist "%~dp0..\AgentSessionManager.exe" (
  start "" "%~dp0..\AgentSessionManager.exe"
  exit /b 0
)

if exist "%~dp0..\dist\AgentSessionManager.exe" (
  start "" "%~dp0..\dist\AgentSessionManager.exe"
  exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run_gui.py
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python was not found and no packaged EXE is available.
    echo Download AgentSessionManager.exe from the GitHub Releases page.
    pause
    exit /b 1
  )
  python run_gui.py
)
if errorlevel 1 pause
