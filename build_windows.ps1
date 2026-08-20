[CmdletBinding()]
param(
    [switch]$Clean,
    [string]$Python
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
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
    throw "Python 3.10+ was not found. Install the standard Windows Python distribution first."
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

$exePath = Join-Path $projectRoot "dist\CodexLocalSessionManager.exe"
$checksumPath = "$exePath.sha256"
foreach ($oldOutput in @($exePath, $checksumPath)) {
    if (Test-Path -LiteralPath $oldOutput) {
        Remove-Item -LiteralPath $oldOutput -Force
    }
}
$versionFile = Join-Path $projectRoot "packaging\windows_version_info.txt"
& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name CodexLocalSessionManager `
    --specpath build `
    --paths src `
    --version-file $versionFile `
    run_gui.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the Windows application."
}

if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "Build completed without producing $exePath"
}

$hash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash
"$($hash.ToLowerInvariant())  CodexLocalSessionManager.exe" | Set-Content -LiteralPath $checksumPath -Encoding ascii
Write-Host "Built: $exePath"
Write-Host "SHA256: $hash"
Write-Host "Checksum: $checksumPath"
