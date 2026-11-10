# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from importlib.metadata import PackageNotFoundError
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata, collect_data_files


project_root = Path(SPECPATH).resolve().parent
if not (project_root / "film_foundry").is_dir():
    project_root = project_root.parent
hiddenimports = collect_submodules("film_foundry", filter=lambda name: not name.startswith("film_foundry.tests"))
hiddenimports += ["rawpy._rawpy", "imagecodecs._ultrahdr"]
dependency_metadata = collect_data_files("imagecodecs", includes=["licenses/*"])
for package in ("numpy", "opencv-python", "Pillow", "tifffile", "python-lcms2", "PySide6-Essentials", "shiboken6", "rawpy", "imagecodecs"):
    dependency_metadata += copy_metadata(package)
try:
    dependency_metadata += copy_metadata("PySide6")
except PackageNotFoundError:
    pass  # The declared Essentials installation does not require the umbrella package.

a = Analysis(
    [str(project_root / "run_film_foundry_launcher.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (
            str(project_root / "film_foundry" / "presets"),
            "film_foundry/presets",
        ),
        (str(project_root / "film_foundry" / "assets"), "film_foundry/assets"),
        *dependency_metadata,
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "_tkinter"],
    noarchive=False,
    optimize=0,
)
# Qt's Windows build uses the OS ICU ABI (unversioned symbols). A foreign
# Poppler/Conda ICU found on PATH can share its name but exports another ABI.
# Leave this Windows system dependency to the OS, as in the source runtime.
if sys.platform == "win32":
    a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() != "icuuc.dll"]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FilmFoundry",
    icon=str(project_root / "film_foundry/assets/film_foundry.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FilmFoundry",
)
