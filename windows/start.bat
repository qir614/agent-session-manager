@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

if exist "AgentSessionManager.exe" (
  start "" "AgentSessionManager.exe"
  exit /b 0
)

if exist "dist\AgentSessionManager.exe" (
  start "" "dist\AgentSessionManager.exe"
  exit /b 0
)

rem 1. Explicit interpreter.
if defined ASM_PYTHON call :probe "%ASM_PYTHON%"

rem 2. The project's own conda environment: conda env create -f environment.yml
for %%D in (C D E F G) do (
  call :probe_env "%%D:\miniconda3"
  call :probe_env "%%D:\anaconda3"
  call :probe_env "%%D:\Miniconda3"
  call :probe_env "%%D:\Anaconda3"
)
for %%R in ("%USERPROFILE%" "%LOCALAPPDATA%" "%ProgramData%") do (
  if not "%%~R"=="" (
    call :probe_env "%%~R\miniconda3"
    call :probe_env "%%~R\anaconda3"
    call :probe_env "%%~R\Miniconda3"
    call :probe_env "%%~R\Anaconda3"
  )
)

rem 3. Any other Python that really has Tcl/Tk.
where py >nul 2>nul
if not errorlevel 1 call :probe_py

for %%F in (python.exe) do if not defined PYTHON_EXE set "PYTHON_EXE=%%~$PATH:F"
if defined PYTHON_EXE call :probe "%PYTHON_EXE%"

for %%D in (C D E F G) do (
  call :probe "%%D:\miniconda3\python.exe"
  call :probe "%%D:\anaconda3\python.exe"
  call :probe "%%D:\Miniconda3\python.exe"
  call :probe "%%D:\Anaconda3\python.exe"
)

for %%D in ("%LOCALAPPDATA%\Programs\Python" "%ProgramFiles%\Python" "%SystemDrive%\Python") do (
  if exist "%%~D" for /d %%V in ("%%~D\Python3*") do call :probe "%%~V\python.exe"
)

if defined PYTHON_LAUNCH goto :launch

echo Python 3.11+ with Tcl/Tk was not found and no packaged EXE is available.
echo.
echo Options:
echo   - Download AgentSessionManager.exe from the GitHub Releases page.
echo   - Create the project environment: conda env create -f environment.yml
echo   - Install Python 3.11+ with Tcl/Tk support.
echo   - Set ASM_PYTHON to the full path of a python.exe that has Tcl/Tk.
pause
exit /b 1

:launch
start "" %PYTHON_LAUNCH% run_gui.py
exit /b 0

:probe_py
if defined PYTHON_LAUNCH exit /b 0
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do call :probe "%%P"
exit /b 0

:probe_env
if defined PYTHON_LAUNCH exit /b 0
if "%~1"=="" exit /b 0
call :probe "%~1\envs\agent-session-manager\python.exe"
exit /b 0

:probe
if defined PYTHON_LAUNCH exit /b 0
set "CANDIDATE=%~1"
if "%CANDIDATE%"=="" exit /b 0
if not exist "%CANDIDATE%" exit /b 0
echo "%CANDIDATE%" | findstr /i /c:"\\WindowsApps\\" >nul
if not errorlevel 1 exit /b 0
"%CANDIDATE%" -c "import tkinter" >nul 2>nul
if errorlevel 1 exit /b 0
set "CANDIDATE_W=%CANDIDATE:python.exe=pythonw.exe%"
if exist "%CANDIDATE_W%" (set "PYTHON_LAUNCH="%CANDIDATE_W%"") else (set "PYTHON_LAUNCH="%CANDIDATE%"")
exit /b 0
