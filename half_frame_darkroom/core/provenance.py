"""Provenance markers for generated Film Foundry materials.

These are not anti-tamper or forensic watermarks. They are explicit provenance
markers for outputs and electronic negative materials.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from half_frame_darkroom import __version__

PROJECT_NAME = "Film Foundry / Electronic Negative Factory"
PROVENANCE_KIND = "FilmFoundryProvenance"
PROVENANCE_VERSION = "1"


def provenance_payload(
    *,
    stage: str,
    input_path: str | Path | None = None,
    output_path: str | Path | None = None,
    negative_path: str | Path | None = None,
    resolved_seed: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": PROVENANCE_KIND,
        "version": PROVENANCE_VERSION,
        "project": PROJECT_NAME,
        "project_version": __version__,
        "stage": str(stage),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if input_path is not None:
        payload["input_path"] = str(input_path)
    if output_path is not None:
        payload["output_path"] = str(output_path)
    if negative_path is not None:
        payload["negative_path"] = str(negative_path)
    if resolved_seed is not None:
        payload["resolved_seed"] = int(resolved_seed)
    payload["id"] = provenance_id(payload)
    return payload


def provenance_id(payload: dict[str, Any]) -> str:
    stable = {key: value for key, value in payload.items() if key not in {"id", "created_at"}}
    data = json.dumps(stable, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.blake2b(data, digest_size=5).hexdigest().upper()


def provenance_npz_array(payload: dict[str, Any]) -> np.ndarray:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return np.asarray(text)


def payload_with_config(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["config"] = config
    return result


def apply_scanner_raw_border_watermark(
    image: np.ndarray,
    payload: dict[str, Any],
    *,
    border_width: int,
) -> np.ndarray:
    """Add a subtle readable mark inside the scanner raw clear border."""
    result = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0).copy()
    border = int(border_width)
    if border <= 8 or result.ndim != 3 or result.shape[-1] != 3:
        return result

    height, width = result.shape[:2]
    text = f"Film Foundry {payload.get('id', '')}".strip()
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.35, min(0.75, border / 58.0))
    thickness = 1
    (text_width, text_height), _baseline = cv2.getTextSize(text, font, scale, thickness)
    x = max(4, min(width - text_width - 4, border + 8))
    y = max(text_height + 3, min(border - 6, text_height + 12))

    raw16 = np.round(result * 65535.0).astype(np.uint16)
    base = raw16[:border, :, :].mean(axis=(0, 1))
    ink = np.clip(base.astype(np.float32) * 0.82, 0.0, 65535.0).astype(np.uint16)
    color = tuple(int(v) for v in ink[::-1])
    bgr = raw16[..., ::-1].copy()
    cv2.putText(bgr, text, (x, y), font, scale, color, thickness, cv2.LINE_AA)
    return np.clip(bgr[..., ::-1].astype(np.float32) / 65535.0, 0.0, 1.0)
