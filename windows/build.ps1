[CmdletBinding()]
param(
    [switch]$Clean,
    [string]$Python,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 encodes redirected output with the OEM code page, which
# turns non-ASCII paths and messages into mojibake in CI logs.
$previousOutputEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {
    [Console]::OutputEncoding = $previousOutputEncoding
}
$platformRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $platformRoot
Set-Location -LiteralPath $projectRoot

# pip and venv both need a writable TEMP. System temp is not always writable
# (sandboxed shells, restricted profiles), so build scratch space stays in the
# project directory and is ignored by git.
$tempRoot = Join-Path $projectRoot ".review-tmp"
if (-not (Test-Path -LiteralPath $tempRoot)) {
    New-Item -ItemType Directory -Path $tempRoot | Out-Null
}
$env:TMP = $tempRoot
$env:TEMP = $tempRoot

# $ErrorActionPreference = "Stop" turns any native stderr output into a
# terminating error, so native probes go through this wrapper instead.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments = @())

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Exe @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return [pscustomobject]@{
        Output   = @($output)
        ExitCode = $exitCode
    }
}

function Get-InterpreterInfo {
    param([string]$Command, [string[]]$Arguments = @())

    $probe = "import sys, tkinter; print(sys.executable); print('%d.%d' % sys.version_info[:2])"
    $probeResult = Invoke-Native -Exe $Command -Arguments ($Arguments + @("-c", $probe))
    if ($probeResult.ExitCode -ne 0) {
        return $null
    }
    $probeOutput = $probeResult.Output
    $lines = @($probeOutput | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    if ($lines.Count -lt 2) {
        return $null
    }
    $executable = $lines[$lines.Count - 2]
    $version = $lines[$lines.Count - 1]
    if ($version -notmatch "^(\d+)\.(\d+)$") {
        return $null
    }
    if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 11)) {
        return $null
    }
    return [pscustomobject]@{
        Executable = $executable
        Version    = $version
    }
}

function Resolve-TclTkLayout {
    param([string]$BasePrefix)

    $libraryRoots = @(
        (Join-Path $BasePrefix "tcl"),
        (Join-Path $BasePrefix "Library\lib")
    )
    $binaryRoots = @(
        (Join-Path $BasePrefix "DLLs"),
        (Join-Path $BasePrefix "Library\bin")
    )

    $tclLibrary = $null
    $tkLibrary = $null
    foreach ($root in $libraryRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        if (-not $tclLibrary) {
            $tclLibrary = @(
                Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match "^tcl\d+(\.\d+)?$" -and (Test-Path -LiteralPath (Join-Path $_.FullName "init.tcl")) }
            ) | Select-Object -First 1 -ExpandProperty FullName
        }
        if (-not $tkLibrary) {
            $tkLibrary = @(
                Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match "^tk\d+(\.\d+)?$" -and (Test-Path -LiteralPath (Join-Path $_.FullName "tk.tcl")) }
            ) | Select-Object -First 1 -ExpandProperty FullName
        }
    }

    $tclDll = $null
    $tkDll = $null
    foreach ($root in $binaryRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        if (-not $tclDll) {
            $tclDll = @(
                Get-ChildItem -LiteralPath $root -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match "^(tcl|cl)\d+t\.dll$" }
            ) | Select-Object -First 1 -ExpandProperty FullName
        }
        if (-not $tkDll) {
            $tkDll = @(
                Get-ChildItem -LiteralPath $root -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match "^(tk|k)\d+t\.dll$" }
            ) | Select-Object -First 1 -ExpandProperty FullName
        }
    }

    $tkinterPackage = Join-Path $BasePrefix "Lib\tkinter"
    $tkinterExtension = @(
        (Join-Path $BasePrefix "DLLs\_tkinter.pyd"),
        (Join-Path $BasePrefix "Lib\lib-dynload\_tkinter.pyd")
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

    $sqliteDll = @(
        (Join-Path $BasePrefix "DLLs\sqlite3.dll"),
        (Join-Path $BasePrefix "Library\bin\sqlite3.dll")
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

    return [pscustomobject]@{
        TclLibrary       = $tclLibrary
        TkLibrary        = $tkLibrary
        TclDll           = $tclDll
        TkDll            = $tkDll
        TkinterPackage   = $tkinterPackage
        TkinterExtension = $tkinterExtension
        SqliteDll        = $sqliteDll
    }
}

# 1. Pick an interpreter that really has Python 3.11+ and Tcl/Tk. Probing for
#    "import tkinter" rejects the Microsoft Store stubs on PATH, which exist as
#    files but exit with 9009.
$candidates = New-Object System.Collections.ArrayList
function Add-Candidate {
    param([string]$Command, [string[]]$Arguments = @(), [string]$Label)
    [void]$candidates.Add([pscustomobject]@{
        Command   = $Command
        Arguments = $Arguments
        Label     = $Label
    })
}

if ($Python) {
    $resolvedPython = (Resolve-Path -LiteralPath $Python -ErrorAction Stop).Path
    Add-Candidate -Command $resolvedPython -Label "-Python"
} else {
    if ($env:ASM_PYTHON) {
        Add-Candidate -Command $env:ASM_PYTHON -Label "ASM_PYTHON"
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Add-Candidate -Command "py" -Arguments @("-3") -Label "py -3"
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        Add-Candidate -Command "python" -Label "python"
    }
    $venvCandidate = Join-Path $projectRoot ".build-venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvCandidate) {
        Add-Candidate -Command $venvCandidate -Label ".build-venv"
    }
    $condaRoots = New-Object System.Collections.ArrayList
    foreach ($drive in @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
        [void]$condaRoots.Add($drive.Root.TrimEnd("\"))
    }
    foreach ($extra in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:ProgramData)) {
        if ($extra) { [void]$condaRoots.Add($extra) }
    }
    foreach ($root in $condaRoots) {
        if (-not $root) { continue }
        foreach ($distribution in @("miniconda3", "anaconda3", "Miniconda3", "Anaconda3", "miniforge3")) {
            $distributionRoot = Join-Path $root $distribution
            Add-Candidate -Command (Join-Path $distributionRoot "python.exe") -Label $distribution
            Add-Candidate -Command (Join-Path $distributionRoot "envs\agent-session-manager\python.exe") -Label "$distribution agent-session-manager"
        }
    }
    foreach ($programsPython in @(Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA "Programs\Python") -Directory -ErrorAction SilentlyContinue)) {
        Add-Candidate -Command (Join-Path $programsPython.FullName "python.exe") -Label "Programs\Python"
    }
}

function Test-PyInstaller {
    param([string]$PythonExe)
    $probeResult = Invoke-Native -Exe $PythonExe -Arguments @("-c", "import PyInstaller")
    return ($probeResult.ExitCode -eq 0)
}

# Prefer an interpreter that already has PyInstaller: installing it needs
# network access, which a prepared environment may not have.
$selected = $null
$preferred = $null
foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate.Command -PathType Leaf) -and -not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) {
        continue
    }
    $info = Get-InterpreterInfo -Command $candidate.Command -Arguments $candidate.Arguments
    if (-not $info) {
        continue
    }
    $entry = [pscustomobject]@{
        Executable = $info.Executable
        Version    = $info.Version
        Source     = $candidate.Label
    }
    if (-not $selected) {
        $selected = $entry
    }
    if (Test-PyInstaller -PythonExe $info.Executable) {
        $preferred = $entry
        break
    }
}
if ($preferred) {
    $selected = $preferred
}
if (-not $selected) {
    throw "No Python 3.11+ interpreter with Tcl/Tk was found. Pass -Python <path to python.exe> or set ASM_PYTHON."
}
Write-Host "Interpreter: $($selected.Executable) (Python $($selected.Version), via $($selected.Source))"

# 2. Build either inside .build-venv or with the selected interpreter itself.
#    A venv is preferred because it isolates dependencies, but it needs network
#    access to install PyInstaller. When the selected interpreter already has
#    PyInstaller (a prepared conda environment, for example), use it directly.
$venvRoot = Join-Path $projectRoot ".build-venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$createdVenv = $false

if ($Clean -and (Test-Path -LiteralPath $venvRoot)) {
    $resolved = (Resolve-Path -LiteralPath $venvRoot).Path
    if ($resolved -ne $venvRoot) {
        throw "Refusing to remove unexpected path: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

$buildPython = $null
if ((Test-Path -LiteralPath $venvPython) -and (Test-PyInstaller -PythonExe $venvPython)) {
    $buildPython = $venvPython
    Write-Host "Build environment: existing .build-venv"
} elseif (Test-PyInstaller -PythonExe $selected.Executable) {
    $buildPython = $selected.Executable
    Write-Host "Build environment: selected interpreter (PyInstaller already installed)"
} else {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $venvResult = Invoke-Native -Exe $selected.Executable -Arguments @("-m", "venv", $venvRoot)
        if ($venvResult.ExitCode -ne 0) {
            throw "Failed to create the build virtual environment in $venvRoot. Output: $(($venvResult.Output | Out-String).Trim())"
        }
        $createdVenv = $true
    }
    if ($createdVenv) {
        $pipUpgrade = Invoke-Native -Exe $venvPython -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"
        )
        if ($pipUpgrade.ExitCode -ne 0) {
            throw "Failed to upgrade pip in the build environment. Output: $(($pipUpgrade.Output | Out-String).Trim())"
        }
    }
    if (-not (Test-PyInstaller -PythonExe $venvPython)) {
        $dependencyInstall = Invoke-Native -Exe $venvPython -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check", ".[build]"
        )
        if ($dependencyInstall.ExitCode -ne 0) {
            throw "Failed to install the Windows build dependencies. Output: $(($dependencyInstall.Output | Out-String).Trim()) The build environment needs network access to PyPI, or pass -Python pointing at an interpreter that already has PyInstaller."
        }
    }
    $buildPython = $venvPython
    Write-Host "Build environment: $venvRoot"
}

# 3. Locate the Tcl/Tk runtime. Standard CPython keeps it under tcl\ and DLLs\;
#    conda keeps it under Library\lib and Library\bin. Both layouts are valid.
$basePrefixResult = Invoke-Native -Exe $buildPython -Arguments @("-c", "import sys; print(sys.base_prefix)")
if ($basePrefixResult.ExitCode -ne 0) {
    throw "Could not query sys.base_prefix from $buildPython."
}
$pythonBase = (($basePrefixResult.Output | ForEach-Object { "$_".Trim() } | Where-Object { $_ }) -join "")
$pythonBase = $pythonBase.Trim().Trim('"')
if (-not $pythonBase) {
    throw "Could not query sys.base_prefix from $buildPython."
}
$layout = Resolve-TclTkLayout -BasePrefix $pythonBase
foreach ($entry in @(
    @{ Name = "tcl library"; Value = $layout.TclLibrary },
    @{ Name = "tk library"; Value = $layout.TkLibrary },
    @{ Name = "tkinter package"; Value = $layout.TkinterPackage },
    @{ Name = "_tkinter extension"; Value = $layout.TkinterExtension },
    @{ Name = "tcl dll"; Value = $layout.TclDll },
    @{ Name = "tk dll"; Value = $layout.TkDll }
)) {
    if (-not $entry.Value) {
        throw "The selected Python runtime does not include a complete Tcl/Tk installation (missing $($entry.Name) under $pythonBase)."
    }
}
Write-Host "Tcl/Tk: $($layout.TclLibrary)"

# Make the Tcl/Tk DLLs and their dependencies (ffi.dll, liblzma.dll, ...) visible
# to PyInstaller so they get collected into the executable.
$env:TCL_LIBRARY = $layout.TclLibrary
$env:TK_LIBRARY = $layout.TkLibrary
$binaryPaths = @(
    (Split-Path -Parent $layout.TclDll),
    (Join-Path $pythonBase "Library\bin"),
    (Join-Path $pythonBase "DLLs")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique
$env:PATH = (($binaryPaths -join ";") + ";" + $env:PATH)

$exePath = Join-Path $projectRoot "dist\AgentSessionManager.exe"
$checksumPath = "$exePath.sha256"
foreach ($oldOutput in @($exePath, $checksumPath)) {
    if (Test-Path -LiteralPath $oldOutput) {
        Remove-Item -LiteralPath $oldOutput -Force
    }
}

$versionFile = Join-Path $projectRoot "windows\packaging\version_info.txt"
if (-not (Test-Path -LiteralPath $versionFile)) {
    throw "Missing version resource: $versionFile"
}

$pyInstallerArgs = New-Object System.Collections.ArrayList
foreach ($argument in @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "AgentSessionManager",
    "--specpath", (Join-Path $projectRoot "build"),
    "--workpath", (Join-Path $projectRoot "build"),
    "--distpath", (Join-Path $projectRoot "dist"),
    "--paths", (Join-Path $projectRoot "src"),
    "--version-file", $versionFile,
    "--hidden-import", "tkinter",
    "--hidden-import", "_tkinter",
    "--hidden-import", "sqlite3",
    "--hidden-import", "lzma",
    "--hidden-import", "bz2",
    "--hidden-import", "agent_session_manager.platforms.windows",
    "--add-binary", "$($layout.TkinterExtension);.",
    "--add-binary", "$($layout.TclDll);.",
    "--add-binary", "$($layout.TkDll);.",
    "--add-data", "$($layout.TkinterPackage);tkinter",
    "--add-data", "$($layout.TclLibrary);_tcl_data",
    "--add-data", "$($layout.TkLibrary);_tk_data"
)) {
    [void]$pyInstallerArgs.Add($argument)
}
if ($layout.SqliteDll) {
    [void]$pyInstallerArgs.Add("--add-binary")
    [void]$pyInstallerArgs.Add("$($layout.SqliteDll);.")
}
[void]$pyInstallerArgs.Add((Join-Path $projectRoot "run_gui.py"))

# PyInstaller writes its progress to stderr. When this script's own output is
# redirected (CI logs, Tee-Object), Windows PowerShell 5.1 turns that stderr into
# terminating error records and the build would abort mid-flight, so the call goes
# through Invoke-Native and its output is replayed afterwards.
$buildResult = Invoke-Native -Exe $buildPython -Arguments $pyInstallerArgs
foreach ($line in $buildResult.Output) {
    Write-Host "$line"
}
if ($buildResult.ExitCode -ne 0) {
    throw "PyInstaller failed to build the Windows application."
}

if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "Build completed without producing $exePath"
}

# 4. Verify that the archive really contains the runtime pieces that PyInstaller
#    silently drops when DLL dependencies cannot be resolved.
$archiveResult = Invoke-Native -Exe $buildPython -Arguments @(
    "-m", "PyInstaller.utils.cliutils.archive_viewer", "-r", "-b", $exePath
)
if ($archiveResult.ExitCode -ne 0) {
    throw "Could not inspect the built executable with pyi-archive_viewer."
}
$archiveListing = $archiveResult.Output
foreach ($required in @(
    "tkinter\__init__.py", "_tkinter.pyd", "_tcl_data\init.tcl", "_tk_data\tk.tcl",
    "sqlite3.dll", "_sqlite3.pyd", "ffi.dll", "_ctypes.pyd",
    "agent_session_manager.orphan_cleaner", "agent_session_manager.platforms.windows"
)) {
    if (-not ($archiveListing | Select-String -SimpleMatch $required -Quiet)) {
        throw "Built EXE is missing required runtime file: $required"
    }
}
if (-not ($archiveListing | Select-String -Pattern "^(tcl|cl)\d+t\.dll$|(tcl|cl)\d+t\.dll\s*$" -Quiet)) {
    throw "Built EXE is missing the Tcl runtime DLL."
}

# 5. Start the executable once and confirm that the main window appears. The
#    window must be created visible: a hidden or minimized process does not
#    expose MainWindowTitle, so the check would time out on a healthy build.
if ($SkipSmokeTest) {
    Write-Host "GUI smoke test: skipped (-SkipSmokeTest)"
} elseif (-not [Environment]::UserInteractive) {
    Write-Host "GUI smoke test: skipped (no interactive desktop session)"
} else {
    $existingProcesses = @(Get-Process -Name "AgentSessionManager" -ErrorAction SilentlyContinue)
    if ($existingProcesses.Count -gt 0) {
        throw "Close the existing AgentSessionManager process before the GUI smoke test."
    }
    $smokeHome = Join-Path $tempRoot ("gui-smoke-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $smokeHome | Out-Null
    $previousCodexHome = $env:CODEX_HOME
    try {
        $env:CODEX_HOME = $smokeHome
        $smokeProcess = Start-Process -FilePath $exePath -PassThru
        # The first launch of a freshly written EXE can be delayed for tens of
        # seconds by on-access antivirus scanning, so the deadline is generous.
        $smokeDeadline = (Get-Date).AddSeconds(120)
        $mainWindowFound = $false
        $observedTitles = @()
        do {
            Start-Sleep -Milliseconds 250
            $launched = @(Get-Process -Name "AgentSessionManager" -ErrorAction SilentlyContinue)
            $observedTitles = @($launched | ForEach-Object { $_.MainWindowTitle } | Where-Object { $_ })
            if ($observedTitles | Where-Object { $_ -like "Unhandled exception*" }) {
                throw "GUI smoke test opened an unhandled-exception window: $($observedTitles -join ' | ')"
            }
            if ($observedTitles | Where-Object { $_ -like "*Agent Session Manager*" }) {
                $mainWindowFound = $true
                break
            }
            if ($launched.Count -eq 0 -and $smokeProcess.HasExited) {
                throw "GUI smoke test process exited before opening a window (exit code $($smokeProcess.ExitCode))."
            }
        } while ((Get-Date) -lt $smokeDeadline)
        if (-not $mainWindowFound) {
            throw "GUI smoke test did not find the expected main window. Observed titles: $($observedTitles -join ' | ')"
        }
        Write-Host "GUI smoke test: main window opened"
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
}

$hash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash
"$($hash.ToLowerInvariant())  AgentSessionManager.exe" | Set-Content -LiteralPath $checksumPath -Encoding ascii
Write-Host "Built: $exePath"
Write-Host "SHA256: $hash"
Write-Host "Checksum: $checksumPath"
