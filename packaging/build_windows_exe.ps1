param(
    [string]$Python = "",
    [string]$OutputRoot = "",
    [switch]$SkipFrozenSelfCheck
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$SpecPath = Join-Path $ScriptDir "FilmFoundry.spec"

if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python executable not found: $Python"
}

$QtVersion = ([string](& $Python -c "from PySide6 import QtCore; print(QtCore.qVersion())")).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($QtVersion)) {
    throw "The selected Python environment does not have a usable PySide6 runtime."
}

& $Python -c "import PyInstaller" *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed in $Python. Install PyInstaller in the selected build environment first."
}

Push-Location $ProjectRoot
$OriginalPath = $env:PATH
try {
    # PyInstaller also resolves native dependencies through PATH. Restrict it to
    # this Python/Conda environment and Windows, not unrelated caller tools.
    $PythonRoot = Split-Path -Parent $Python
    $WindowsRoot = [Environment]::GetFolderPath('Windows')
    $env:PATH = (@(
        $PythonRoot,
        (Join-Path $PythonRoot 'Library\bin'),
        (Join-Path $PythonRoot 'DLLs'),
        (Join-Path $PythonRoot 'Scripts'),
        (Join-Path $WindowsRoot 'System32'),
        $WindowsRoot
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Container }) -join ';'
    if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
        $OutputRoot = Join-Path $ProjectRoot "portable_build"
    }
    $OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
    $ImageOutputRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "outputs"))
    if ($OutputRoot.Equals($ImageOutputRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $OutputRoot.StartsWith($ImageOutputRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "outputs is reserved for processed images. Choose a dedicated build directory."
    }
    $PortableRoot = Join-Path $OutputRoot "FilmFoundry"
    if (Test-Path -LiteralPath $PortableRoot) {
        throw "Build destination already exists: $PortableRoot. Choose a fresh -OutputRoot; existing applications and user data are never overwritten."
    }
    $BuildOptions = @("--distpath", $OutputRoot, "--workpath", (Join-Path $OutputRoot ".build"))
    & $Python -m PyInstaller --noconfirm --clean @BuildOptions $SpecPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE."
    }

    $PortableRoot = Join-Path $OutputRoot "FilmFoundry"
    if (-not (Test-Path -LiteralPath (Join-Path $PortableRoot "FilmFoundry.exe"))) {
        throw "Build completed without $PortableRoot\FilmFoundry.exe."
    }

    $BundledQtPlatform = Get-ChildItem `
        -LiteralPath (Join-Path $PortableRoot "_internal") `
        -Recurse `
        -Filter "qwindows.dll" `
        -File | Select-Object -First 1
    if ($null -eq $BundledQtPlatform) {
        throw "Portable package is missing the PySide6 qwindows platform plugin."
    }

    @(
        "input_images",
        "outputs",
        "outputs\negatives",
        "outputs\positives",
        "user_presets",
        "user_presets\film",
        "user_presets\develop",
        "user_presets\scanner"
    ) | ForEach-Object {
        New-Item -ItemType Directory -Force -Path (Join-Path $PortableRoot $_) | Out-Null
    }

    Copy-Item -LiteralPath (Join-Path $ScriptDir "PORTABLE_README.txt") -Destination (Join-Path $PortableRoot "README.txt") -Force
    # Collect notices without changing the runtime; this also repairs existing packages.
    & (Join-Path $ScriptDir "collect_windows_licenses.ps1") -PortableRoot $PortableRoot -Python $Python
    $NativeNotices = Join-Path $PortableRoot 'licenses/windows-native'
    & $Python (Join-Path $ScriptDir 'collect_native_notices.py') --toc (Join-Path $OutputRoot '.build/FilmFoundry/Analysis-00.toc') --python-root $PythonRoot --output $NativeNotices
    if ($LASTEXITCODE -ne 0) { throw 'Native dependency provenance check failed.' }
    & $Python (Join-Path $ScriptDir 'prepare_source_release.py') $PortableRoot --portable-docs
    if ($LASTEXITCODE -ne 0) { throw 'Public documentation copy failed.' }
    foreach ($Notice in @('LICENSE','COPYING')) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot $Notice) -Destination $PortableRoot
    }

    if (-not $SkipFrozenSelfCheck) {
        $SelfCheck = Start-Process `
            -FilePath (Join-Path $PortableRoot "FilmFoundry.exe") `
            -ArgumentList "--self-check" `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        if ($SelfCheck.ExitCode -ne 0) {
            throw "Frozen runtime self-check failed with exit code $($SelfCheck.ExitCode). See self-check-error.log."
        }
    }
    else {
        Write-Warning "Frozen Qt self-check skipped; manually launch FilmFoundry.exe before release."
    }

    Write-Host "Portable build ready: $PortableRoot (Qt $QtVersion)"
}
finally {
    $env:PATH = $OriginalPath
    Pop-Location
}
