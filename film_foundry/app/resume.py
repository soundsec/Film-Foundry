"""Replay from complete on-disk artifacts. Not a half-finished medium format.

Interrupt/resume units are loadable latent NPZ, loadable developed NPZ, and
non-empty committed output images. In-flight formation is not checkpointed.
"""

from __future__ import annotations

from pathlib import Path
import zipfile

from film_foundry.core.latent_io import load_exposed_latent
from film_foundry.core.negative_io import load_developed_medium_for_scan


def complete_latent_archive(path: Path) -> bool:
    """True when ``path`` loads as a latent, including consumed archives."""
    try:
        load_exposed_latent(path, allow_consumed=True)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile):
        return False
    return True


def complete_developed_archive(path: Path) -> bool:
    """True when ``path`` loads as a developed medium for scan ingest."""
    try:
        load_developed_medium_for_scan(path)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile):
        return False
    return True


def find_complete_latent_development(
    input_path: Path, output_root: Path, planned_path: Path,
) -> Path | None:
    """Find a committed result when frozen material resolves another polarity.

    Inspect results, never source pools: old consumed or missing sources may
    still have a complete result to reuse. Explicit NPZ targets remain exact.
    """
    from film_foundry.app.workflow import planned_developed_path

    candidates = [Path(planned_path)]
    candidates.extend(planned_developed_path(input_path, output_root, image_polarity=polarity)
                      for polarity in ('negative', 'positive'))
    seen = set()
    for candidate in candidates:
        if candidate not in seen and complete_developed_archive(candidate):
            return candidate
        seen.add(candidate)
    return None


def complete_image_file(path: Path) -> bool:
    """True when a committed output image exists. Empty files are not complete."""
    path = Path(path)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
