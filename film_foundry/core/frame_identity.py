"""Optional roll/frame identity shared by latent and developed media.

This is interchange metadata, not a second schema and not a material field.
Absence remains legal for files written before the identity existed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


FRAME_IDENTITY_KEY = "frame_identity"
FRAME_IDENTITY_FIELDS = ("roll_id", "frame_id", "frame_index")


def normalize_frame_identity(
    value: Any,
    *,
    path: Path | str | None = None,
) -> dict[str, str | int] | None:
    """Return a canonical identity object, or None when the writer omitted it."""
    if value is None:
        return None
    where = f"{path} 中的 " if path is not None else ""
    if not isinstance(value, Mapping) or isinstance(value, (str, bytes)):
        raise ValueError(f"{where}{FRAME_IDENTITY_KEY} 必须是对象。")
    unknown = [str(key) for key in value if str(key) not in FRAME_IDENTITY_FIELDS]
    if unknown:
        raise ValueError(
            f"{where}{FRAME_IDENTITY_KEY} 含有未编目字段：{', '.join(sorted(unknown))}。"
        )
    payload: dict[str, str | int] = {}
    if "roll_id" in value:
        roll_id = value["roll_id"]
        if not isinstance(roll_id, str) or not roll_id.strip():
            raise ValueError(f"{where}{FRAME_IDENTITY_KEY}.roll_id 必须是非空字符串。")
        payload["roll_id"] = roll_id.strip()
    if "frame_id" in value:
        frame_id = value["frame_id"]
        if not isinstance(frame_id, str) or not frame_id.strip():
            raise ValueError(f"{where}{FRAME_IDENTITY_KEY}.frame_id 必须是非空字符串。")
        payload["frame_id"] = frame_id.strip()
    if "frame_index" in value:
        frame_index = value["frame_index"]
        if isinstance(frame_index, bool) or not isinstance(frame_index, int):
            raise ValueError(f"{where}{FRAME_IDENTITY_KEY}.frame_index 必须是整数。")
        if frame_index < 1:
            raise ValueError(f"{where}{FRAME_IDENTITY_KEY}.frame_index 必须是从 1 起的正整数。")
        payload["frame_index"] = int(frame_index)
    if not payload:
        return None
    if "roll_id" not in payload:
        raise ValueError(f"{where}{FRAME_IDENTITY_KEY} 必须包含 roll_id。")
    if "frame_id" not in payload and "frame_index" not in payload:
        raise ValueError(
            f"{where}{FRAME_IDENTITY_KEY} 必须包含 frame_id 或 frame_index。"
        )
    return payload


def frame_identity_from_mapping(mapping: Any) -> dict[str, str | int] | None:
    """Read identity from in-memory metadata or a JSON payload."""
    if not isinstance(mapping, Mapping):
        return None
    if FRAME_IDENTITY_KEY in mapping:
        return normalize_frame_identity(mapping.get(FRAME_IDENTITY_KEY))
    nested = mapping.get("developed_medium")
    if isinstance(nested, Mapping) and FRAME_IDENTITY_KEY in nested:
        return normalize_frame_identity(nested.get(FRAME_IDENTITY_KEY))
    return None


def attach_frame_identity(
    payload: dict[str, Any],
    identity: Any,
    *,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Copy a validated identity onto a metadata object. Omits the key when absent."""
    normalized = normalize_frame_identity(identity, path=path)
    if normalized is None:
        payload.pop(FRAME_IDENTITY_KEY, None)
        return payload
    payload[FRAME_IDENTITY_KEY] = dict(normalized)
    return payload
