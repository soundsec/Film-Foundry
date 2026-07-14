"""Path helpers shared by source-run and frozen GUI tools."""

from __future__ import annotations

from pathlib import Path
import sys


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file() or (
        source_root / "run_film_foundry_launcher.py"
    ).is_file():
        return source_root
    # Installed console scripts use the caller's working directory for user
    # inputs, outputs and presets instead of trying to write into site-packages.
    return Path.cwd().resolve()


def resource_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root).resolve()
    # Source trees and installed wheels both keep bundled presets beside the
    # Python packages. This is intentionally independent from writable app_root.
    return Path(__file__).resolve().parents[2]
