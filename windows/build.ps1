[CmdletBinding()]
param(
    [switch]$Clean,
    [string]$Python
)

$ErrorActionPreference = "Stop"
$platformRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $platformRoot
Set-Location -LiteralPath $projectRoot

if ($Python) {
    $pythonPath = (Resolve-Path -LiteralPath $Python -ErrorAction Stop).Path
    $launcher = $pythonPath
    $launcherArgs = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $launcher = "py"
    $launcherArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $launcher = "python"
    $launcherArgs = @()
} else {
    throw "Python 3.11+ was not found. Install the standard Windows Python distribution first."
}

$venvRoot = Join-Path $projectRoot ".build-venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$createdVenv = $false
if ($Clean -and (Test-Path -LiteralPath $venvRoot)) {
    $resolved = (Resolve-Path -LiteralPath $venvRoot).Path
    if ($resolved -ne (Join-Path $projectRoot ".build-venv")) {
        throw "Refusing to remove unexpected path: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $launcher @launcherArgs -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the build virtual environment."
    }
    $createdVenv = $true
}

if ($createdVenv) {
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip in the build environment."
    }
    & $venvPython -m pip install ".[build]"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install the Windows build dependencies."
    }
} else {
    & $venvPython -c "import PyInstaller"
    if ($LASTEXITCODE -ne 0) {
        & $venvPython -m pip install ".[build]"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install the Windows build dependencies."
        }
    }
}

$exePath = Join-Path $projectRoot "dist\AgentSessionManager.exe"
$checksumPath = "$exePath.sha256"
foreach ($oldOutput in @($exePath, $checksumPath)) {
    if (Test-Path -LiteralPath $oldOutput) {
        Remove-Item -LiteralPath $oldOutput -Force
    }
}
$versionFile = Join-Path $projectRoot "windows\packaging\version_info.txt"
$pythonBase = (& $venvPython -c "import sys; print(sys.base_prefix)").Trim()
$tclLibrary = Join-Path $pythonBase "tcl\tcl8.6"
$tkLibrary = Join-Path $pythonBase "tcl\tk8.6"
$tkinterPackage = Join-Path $pythonBase "Lib\tkinter"
$tkinterExtension = Join-Path $pythonBase "DLLs\_tkinter.pyd"
$tclDll = Join-Path $pythonBase "DLLs\tcl86t.dll"
$tkDll = Join-Path $pythonBase "DLLs\tk86t.dll"
if (-not (Test-Path -LiteralPath $tclLibrary) -or
    -not (Test-Path -LiteralPath $tkLibrary) -or
    -not (Test-Path -LiteralPath $tkinterPackage) -or
    -not (Test-Path -LiteralPath $tkinterExtension) -or
    -not (Test-Path -LiteralPath $tclDll) -or
    -not (Test-Path -LiteralPath $tkDll)) {
    throw "The selected Python runtime does not include a complete Tcl/Tk installation."
}
$env:TCL_LIBRARY = $tclLibrary
$env:TK_LIBRARY = $tkLibrary
& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name AgentSessionManager `
    --specpath build `
    --paths src `
    --version-file $versionFile `
    --hidden-import tkinter `
    --hidden-import _tkinter `
    --hidden-import agent_session_manager.platforms.windows `
    --add-binary "$tkinterExtension;." `
    --add-binary "$tclDll;." `
    --add-binary "$tkDll;." `
    --add-data "$tkinterPackage;tkinter" `
    --add-data "$tclLibrary;_tcl_data" `
    --add-data "$tkLibrary;_tk_data" `
    run_gui.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the Windows application."
}

if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "Build completed without producing $exePath"
}

$archiveViewer = Join-Path $venvRoot "Scripts\pyi-archive_viewer.exe"
$archiveListing = & $archiveViewer -r -b $exePath
foreach ($required in @(
    "tkinter\__init__.py", "_tkinter.pyd", "tcl86t.dll", "tk86t.dll",
    "_tcl_data\init.tcl", "_tk_data\tk.tcl", "sqlite3.dll",
    "agent_session_manager.orphan_cleaner", "agent_session_manager.platforms.windows"
)) {
    if (-not ($archiveListing | Select-String -SimpleMatch $required -Quiet)) {
        throw "Built EXE is missing required runtime file: $required"
    }
}

$existingProcesses = @(Get-Process -Name "AgentSessionManager" -ErrorAction SilentlyContinue)
if ($existingProcesses.Count -gt 0) {
    throw "Close the existing AgentSessionManager process before the GUI smoke test."
}
$smokeHome = Join-Path ([IO.Path]::GetTempPath()) ("agent-session-manager-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $smokeHome | Out-Null
$previousCodexHome = $env:CODEX_HOME
$smokeStarted = Get-Date
try {
    $env:CODEX_HOME = $smokeHome
    $smokeProcess = Start-Process -FilePath $exePath -PassThru -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(25)
    $mainWindowFound = $false
    do {
        Start-Sleep -Milliseconds 250
        $launched = @(Get-Process -Name "AgentSessionManager" -ErrorAction SilentlyContinue)
        $titles = @($launched | ForEach-Object { $_.MainWindowTitle } | Where-Object { $_ })
        if ($titles | Where-Object { $_ -like "Unhandled exception*" }) {
            throw "GUI smoke test opened an unhandled-exception window: $($titles -join ' | ')"
        }
        if ($titles | Where-Object { $_ -like "*Agent Session Manager*" }) {
            $mainWindowFound = $true
            break
        }
    } while ((Get-Date) -lt $deadline -and -not $smokeProcess.HasExited)
    if (-not $mainWindowFound) {
        throw "GUI smoke test did not find the expected main window. Observed titles: $($titles -join ' | ')"
    }
} finally {
    if ($null -eq $previousCodexHome) {
        Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue
    } else {
        $env:CODEX_HOME = $previousCodexHome
    }
    Get-Process -Name "AgentSessionManager" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    $stopDeadline = (Get-Date).AddSeconds(5)
    do {
        Start-Sleep -Milliseconds 100
        $remainingSmokeProcesses = @(
            Get-Process -Name "AgentSessionManager" -ErrorAction SilentlyContinue
        )
    } while ($remainingSmokeProcesses.Count -gt 0 -and (Get-Date) -lt $stopDeadline)
    Remove-Item -LiteralPath $smokeHome -Recurse -Force -ErrorAction SilentlyContinue
}
if (Get-Process -Name "AgentSessionManager" -ErrorAction SilentlyContinue) {
    throw "GUI smoke-test process remains after cleanup."
}

$hash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash
"$($hash.ToLowerInvariant())  AgentSessionManager.exe" | Set-Content -LiteralPath $checksumPath -Encoding ascii
Write-Host "Built: $exePath"
Write-Host "SHA256: $hash"
Write-Host "Checksum: $checksumPath"
