"""Small same-volume atomic file writers used by core export paths."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
from typing import Any, Iterable, Iterator
from uuid import uuid4

import numpy as np


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON numeric constant '{value}' is not allowed.")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"Duplicate JSON key '{key}' is not allowed.")
        payload[key] = value
    return payload


def strict_json_loads(text: str) -> Any:
    """Parse standards-compliant JSON and reject duplicate object keys."""
    return json.loads(
        text,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_json_keys,
    )


def strict_json_load(path: str | Path) -> Any:
    target = Path(path)
    with target.open("r", encoding="utf-8") as handle:
        return strict_json_loads(handle.read())


def _temporary_sibling(path: Path) -> Path:
    """Return a hidden temporary sibling while preserving the target suffix."""
    suffix = "".join(path.suffixes) or ".tmp"
    # Keep the name short for Windows MAX_PATH compatibility; the temporary
    # file is already isolated by its directory and random token.
    return path.with_name(f".ff-{uuid4().hex[:10]}{suffix}")


@contextmanager
def atomic_output_path(path: str | Path) -> Iterator[Path]:
    """Yield a same-directory temporary path and atomically replace on success."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(target)
    try:
        yield temporary
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def atomic_output_directory(path: str | Path) -> Iterator[Path]:
    """Build a directory beside its target and switch it in only when complete."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".ff-stage-{uuid4().hex[:10]}")
    backup = target.with_name(f".ff-backup-{uuid4().hex[:10]}")
    staging.mkdir()
    moved_existing = False
    try:
        yield staging
        if target.exists():
            os.replace(target, backup)
            moved_existing = True
        try:
            os.replace(staging, target)
        except Exception:
            if moved_existing and backup.exists() and not target.exists():
                os.replace(backup, target)
                moved_existing = False
            raise
        if moved_existing and backup.exists():
            if backup.is_dir():
                shutil.rmtree(backup, ignore_errors=True)
            else:
                try:
                    backup.unlink()
                except OSError:
                    pass
            moved_existing = False
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if moved_existing and backup.exists() and not target.exists():
            try:
                os.replace(backup, target)
                moved_existing = False
            except OSError:
                pass


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


@contextmanager
def atomic_path_set(paths: Iterable[str | Path]) -> Iterator[tuple[Path, ...]]:
    """Rollback a related set of output files/directories as one operation.

    Each previous target is moved aside before any new output is written.  If
    the caller fails, every partially-created target is removed and the whole
    previous set is restored.  This is intended for one logical export whose
    files are individually written atomically but must not end up mixing old
    and new generations.
    """
    targets: list[Path] = []
    seen: set[str] = set()
    for value in paths:
        target = Path(value).resolve()
        key = os.path.normcase(str(target))
        if key in seen:
            raise ValueError(f"Atomic output set contains a duplicate target: {target}")
        seen.add(key)
        targets.append(target)

    # A directory target must represent the complete subtree.  Listing one of
    # its children separately would make rollback order ambiguous.
    for index, target in enumerate(targets):
        for other in targets[index + 1 :]:
            if target in other.parents or other in target.parents:
                raise ValueError(
                    "Atomic output set cannot contain nested targets: "
                    f"{target} and {other}"
                )

    backups: dict[Path, Path] = {}
    try:
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() and not target.is_symlink():
                continue
            backup = target.with_name(f".ff-backup-{uuid4().hex[:10]}")
            os.replace(target, backup)
            backups[target] = backup
    except Exception:
        for target, backup in reversed(tuple(backups.items())):
            if backup.exists() or backup.is_symlink():
                os.replace(backup, target)
        raise

    try:
        yield tuple(targets)
    except BaseException:
        restore_errors: list[OSError] = []
        for target in reversed(targets):
            try:
                if target.exists() or target.is_symlink():
                    _remove_path(target)
            except OSError as exc:
                restore_errors.append(exc)
        for target, backup in reversed(tuple(backups.items())):
            try:
                if backup.exists() or backup.is_symlink():
                    os.replace(backup, target)
            except OSError as exc:
                restore_errors.append(exc)
        if restore_errors:
            raise RuntimeError(
                "Failed to restore one or more outputs after an export error."
            ) from restore_errors[0]
        raise
    else:
        for backup in backups.values():
            try:
                if backup.exists() or backup.is_symlink():
                    _remove_path(backup)
            except OSError:
                # The published output is already complete.  A stale hidden
                # backup is safer than removing or invalidating that output.
                pass


def atomic_write_bytes(path: str | Path, payload: bytes | bytearray | memoryview | np.ndarray) -> Path:
    target = Path(path)
    with atomic_output_path(target) as temporary:
        view = memoryview(payload)
        if not view.contiguous:
            raise ValueError("Atomic binary payload must be contiguous.")
        with temporary.open("wb") as handle:
            handle.write(view.cast("B"))
    return target


def atomic_write_json(path: str | Path, payload: Any) -> Path:
    target = Path(path)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    with atomic_output_path(target) as temporary:
        temporary.write_text(text, encoding="utf-8")
    return target


def atomic_savez(
    path: str | Path,
    *,
    compressed: bool = True,
    **arrays: np.ndarray,
) -> Path:
    target = Path(path)
    with atomic_output_path(target) as temporary:
        writer = np.savez_compressed if compressed else np.savez
        writer(temporary, **arrays)
    return target


def atomic_savez_compressed(path: str | Path, **arrays: np.ndarray) -> Path:
    """Compatibility wrapper for the default compressed NPZ contract."""
    return atomic_savez(path, compressed=True, **arrays)


def atomic_copy2(source: str | Path, destination: str | Path) -> Path:
    source = Path(source)
    target = Path(destination)
    with atomic_output_path(target) as temporary:
        shutil.copy2(source, temporary)
    return target
