"""Session save/load helpers for GUI-first workflows."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from half_frame_darkroom.core.atomic_io import atomic_write_json, strict_json_load
from half_frame_darkroom.model.config import DarkroomConfig


PROJECT_NAME = "Film Foundry / Electronic Negative Factory"
SESSION_KIND = "FilmFoundrySession"
SESSION_VERSION = 1


def preset_reference(value: str | Path | None, kind: str, user_dir: Path, builtin_dir: Path) -> dict[str, Any]:
    """Resolve a preset value and record whether it came from user or bundled presets."""
    text = "" if value is None else str(value)
    if not text:
        return {"kind": kind, "name": "", "source": "missing", "source_label": "未找到", "path": ""}

    direct_path = Path(text)
    if direct_path.exists():
        return {
            "kind": kind,
            "name": direct_path.stem,
            "source": "direct",
            "source_label": "直接文件",
            "path": str(direct_path.resolve()),
        }

    user_path = user_dir / f"{text}.json"
    if user_path.exists():
        return {
            "kind": kind,
            "name": text,
            "source": "user",
            "source_label": "用户预设",
            "path": str(user_path.resolve()),
        }

    builtin_path = builtin_dir / f"{text}.json"
    if builtin_path.exists():
        return {
            "kind": kind,
            "name": text,
            "source": "builtin",
            "source_label": "官方预设",
            "path": str(builtin_path.resolve()),
        }

    return {
        "kind": kind,
        "name": text,
        "source": "missing",
        "source_label": "未找到",
        "path": str(builtin_path),
    }


def preset_value_from_reference(reference: dict[str, Any]) -> str:
    """Return the best value to put back into a GUI/CLI preset field."""
    source = str(reference.get("source", ""))
    path = str(reference.get("path", ""))
    name = str(reference.get("name", ""))
    if source == "direct" and path:
        return path
    return name or path


def session_payload(
    *,
    config: DarkroomConfig,
    pipeline_mode: str,
    input_path: str,
    negative_path: str,
    output_path: str,
    presets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "kind": SESSION_KIND,
        "version": SESSION_VERSION,
        "project": PROJECT_NAME,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "pipeline_mode": str(pipeline_mode),
        "paths": {
            "input": str(input_path),
            "developed_medium": str(negative_path),
            "negative": str(negative_path),
            "output": str(output_path),
        },
        "presets": presets,
        "config": asdict(config),
    }


def save_session(path: str | Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def load_session(path: str | Path) -> dict[str, Any]:
    payload = strict_json_load(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Session JSON root must be an object: {path}")
    if str(payload.get("kind")) != SESSION_KIND:
        raise ValueError(f"Not a Film Foundry session file: {path}")
    if payload.get("version") != SESSION_VERSION:
        raise ValueError(
            f"Unsupported Film Foundry session version {payload.get('version')!r}; "
            f"expected {SESSION_VERSION}: {path}"
        )
    return payload


def config_from_session(path: str | Path) -> DarkroomConfig:
    payload = load_session(path)
    config_data = payload.get("config")
    if not isinstance(config_data, dict):
        raise ValueError(f"Session file has no config object: {path}")
    return DarkroomConfig.from_dict(config_data)
