param(
    [Parameter(Mandatory = $true)][string]$PortableRoot,
    [Parameter(Mandatory = $true)][string]$Python
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PortableRoot = (Resolve-Path -LiteralPath $PortableRoot).Path
$RuntimeRoot = Join-Path $PortableRoot "_internal"
if (-not (Test-Path -LiteralPath (Join-Path $PortableRoot "FilmFoundry.exe") -PathType Leaf) -or
    -not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) {
    throw "Expected an existing FilmFoundry portable application."
}

# Resolve build-runtime notices before modifying the portable folder.
$PythonLicense = (& $Python -c "import sys; from pathlib import Path; print(Path(sys.base_prefix) / 'LICENSE_PYTHON.txt')").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PythonLicense -PathType Leaf)) {
    throw "The build Python license could not be located."
}
$BootloaderLicense = (& $Python -c "from importlib.metadata import distribution; d = distribution('pyinstaller'); print(next(str(d.locate_file(f)) for f in d.files if str(f).endswith('/COPYING.txt')))").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $BootloaderLicense -PathType Leaf)) {
    throw "The build PyInstaller bootloader license could not be located."
}

$LicenseRoot = Join-Path $PortableRoot "licenses"
New-Item -ItemType Directory -Force -Path $LicenseRoot | Out-Null
foreach ($Notice in @("LICENSE", "COPYING", "THIRD_PARTY_NOTICES.md")) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Notice) -Destination $LicenseRoot -Force
}
Copy-Item -LiteralPath $PythonLicense -Destination (Join-Path $LicenseRoot "LICENSE_PYTHON.txt") -Force
$AlgorithmRoot = Join-Path $LicenseRoot "third_party"
New-Item -ItemType Directory -Force -Path $AlgorithmRoot | Out-Null
# Development dependency snapshots are separate; use the portable metadata below.
foreach ($AlgorithmNotice in (Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "third_party_licenses") -File -Filter "*.txt")) {
    Copy-Item -LiteralPath $AlgorithmNotice.FullName -Destination $AlgorithmRoot -Force
}
$InputCodecNotices = Join-Path $ProjectRoot "third_party_licenses/input-codecs"
if (Test-Path -LiteralPath $InputCodecNotices -PathType Container) {
    Copy-Item -LiteralPath $InputCodecNotices -Destination $AlgorithmRoot -Recurse -Force
}
Copy-Item -LiteralPath $BootloaderLicense -Destination (Join-Path $LicenseRoot "PYINSTALLER_COPYING.txt") -Force

$NoticesPath = Join-Path $LicenseRoot "THIRD_PARTY_NOTICES.md"
$Notices = [System.IO.File]::ReadAllText($NoticesPath)
$Notices = $Notices.Replace("](docs/public/", "](https://github.com/soundsec/Film-Foundry/blob/main/docs/")
$Notices = $Notices.Replace("](docs/", "](https://github.com/soundsec/Film-Foundry/blob/main/docs/")
$Notices = $Notices.Replace("third_party_licenses/", "third_party/")
$Notices = $Notices.Replace("[third_party/runtime](third_party/runtime/)", "[bundled package notices](dependencies/)")
$Notices = $Notices.Replace("[third_party/README.md](third_party/README.md)", "[license index](README.txt)")
$Notices = $Notices.Replace("(third_party/android/README.md)", "(https://github.com/soundsec/Film-Foundry/blob/main/third_party_licenses/android/README.md)")
$Notices = $Notices.Replace("(third_party/windows-native/)", "(windows-native/)")
[System.IO.File]::WriteAllText($NoticesPath, $Notices, (New-Object System.Text.UTF8Encoding($false)))

# Use metadata from the actual portable folder, not unrelated installed packages.
$DependencyRoot = Join-Path $LicenseRoot "dependencies"
New-Item -ItemType Directory -Force -Path $DependencyRoot | Out-Null
$Inventory = @()
foreach ($MetadataDir in (Get-ChildItem -LiteralPath $RuntimeRoot -Directory -Filter "*.dist-info" | Sort-Object Name)) {
    $Destination = Join-Path $DependencyRoot $MetadataDir.Name
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($Item in (Get-ChildItem -LiteralPath $MetadataDir.FullName)) {
        if ($Item.Name -eq "METADATA" -or $Item.Name -match '^(LICEN[CS]E|COPYING|NOTICE|AUTHORS|COPYRIGHT)') {
            Copy-Item -LiteralPath $Item.FullName -Destination $Destination -Recurse -Force
        }
    }
    $Inventory += "- dependencies/$($MetadataDir.Name)/"
}
if ($Inventory.Count -eq 0) {
    throw "The portable folder has no dependency metadata; notices collection is incomplete."
}

$Index = @"
Film Foundry - license materials

LICENSE / COPYING: Film Foundry, GPL-3.0-or-later.
LICENSE_PYTHON.txt: bundled Python interpreter license.
THIRD_PARTY_NOTICES.md / third_party/: algorithm sources and upstream notices.
PYINSTALLER_COPYING.txt: executable bootloader terms, including the bootloader exception.
dependencies/: original metadata and license texts copied from the bundled packages.

Qt / PySide6 / Shiboken are used under the GPLv3 option for this GPL release;
the GPLv3 text is in COPYING. Their metadata records the upstream alternatives.
An included commercial-license text does not grant a commercial Qt license.
python-lcms2's GPL license is separate from the LittleCMS library's license.

Keep the original notices under _internal as well. These texts do not replace
applicable corresponding-source and redistribution requirements.

Bundled package metadata:
$($Inventory -join "`n")
"@
[System.IO.File]::WriteAllText((Join-Path $LicenseRoot "README.txt"), $Index, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "License materials collected for $($Inventory.Count) bundled package records: $LicenseRoot"
