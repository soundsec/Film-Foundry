"""Shared output sidecar builders for Film Foundry saves."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import math

from half_frame_darkroom.core.atomic_io import strict_json_load
from half_frame_darkroom.core.states import (
    DevelopedMedium,
    ScannedPositive,
    ScanOutput,
    developed_medium_metadata,
)
from half_frame_darkroom.model.config import DarkroomConfig


PROJECT_NAME = "Film Foundry / Electronic Negative Factory"


def load_scanner_raw_sidecar(scanner_raw_path: str | Path) -> dict[str, Any] | None:
    """Load a scanner/light-table raw sidecar without mutating configuration."""
    raw_path = Path(scanner_raw_path)
    sidecar_path = raw_path.with_suffix(raw_path.suffix + ".json")
    if not sidecar_path.exists():
        return None
    try:
        payload = strict_json_load(sidecar_path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid scanner raw sidecar: {sidecar_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Scanner raw sidecar root must be an object: {sidecar_path}")
    return payload


def scanner_raw_optical_observation_from_sidecar(
    payload: dict[str, Any] | None,
) -> dict[str, object] | None:
    """Extract immutable material optics from a loaded raw sidecar."""
    if not isinstance(payload, dict):
        return None
    developed = payload.get("developed_medium")
    if isinstance(developed, dict):
        process_model = developed.get("film_process_model")
        if isinstance(process_model, dict):
            observation = process_model.get("optical_observation")
            if isinstance(observation, dict):
                return observation
    config = payload.get("config")
    film = config.get("film") if isinstance(config, dict) else None
    if not isinstance(film, dict):
        return None
    return {
        "dye_absorption_matrix": film.get("dye_absorption_matrix"),
        "base_density_rgb": film.get("film_base_density_rgb"),
        "density_min": film.get("density_min"),
        "density_max": film.get("density_max"),
        "color_process": film.get("color_process"),
    }


def scanner_raw_border_contract(
    payload: dict[str, Any] | None,
) -> tuple[float, int] | None:
    """Return the recorded clear-base border geometry, if one exists."""
    if not isinstance(payload, dict) or payload.get("kind") != "ScannerRawNegative":
        return None
    border = payload.get("border")
    if not isinstance(border, dict):
        return None
    try:
        percent = float(border.get("percent", 0.0))
        min_px = int(border.get("min_px", 0))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(percent) or percent <= 0.0:
        return None
    return percent, max(min_px, 0)


def scanner_raw_border_width_from_sidecar(
    payload: dict[str, Any] | None,
    raw_shape: tuple[int, int] | tuple[int, int, int],
) -> int | None:
    """Resolve the exact exported clear-border width.

    New sidecars store ``width_px`` directly.  Older sidecars only stored a
    percentage based on the unbordered image, so reconstruct the integer width
    against the outer raw dimensions instead of applying that percentage to the
    outer image again.
    """
    if not isinstance(payload, dict) or payload.get("kind") != "ScannerRawNegative":
        return None
    border = payload.get("border")
    if not isinstance(border, dict):
        return None
    if "width_px" in border:
        try:
            width = int(border["width_px"])
        except (TypeError, ValueError):
            return None
        max_width = max(0, (min(int(raw_shape[0]), int(raw_shape[1])) - 1) // 2)
        return min(max(width, 0), max_width)

    contract = scanner_raw_border_contract(payload)
    if contract is None:
        return None
    percent, min_px = contract
    outer_min = min(int(raw_shape[0]), int(raw_shape[1]))
    max_width = max(0, (outer_min - 1) // 2)
    candidates: list[int] = []
    for width in range(max_width + 1):
        inner_min = outer_min - 2 * width
        generated = int(round(inner_min * percent))
        if generated > 0:
            generated = max(generated, min_px)
        if generated == width:
            candidates.append(width)
    if candidates:
        estimate = outer_min * percent / max(1.0 + 2.0 * percent, 1e-6)
        return min(candidates, key=lambda value: abs(value - estimate))
    estimate = int(round(outer_min * percent / max(1.0 + 2.0 * percent, 1e-6)))
    if estimate > 0:
        estimate = max(estimate, min_px)
    return min(max(estimate, 0), max_width)


def load_scanner_raw_optical_observation(
    scanner_raw_path: str | Path,
) -> dict[str, object] | None:
    """Read the immutable material-optics snapshot next to scanner raw TIFF."""
    return scanner_raw_optical_observation_from_sidecar(
        load_scanner_raw_sidecar(scanner_raw_path)
    )


def transmission_raw_source_kind(
    scanner_raw_path: str | Path,
    payload: dict[str, Any] | None = None,
) -> str:
    """Return the physical raw product kind, independent of interpretation."""
    if isinstance(payload, dict):
        if payload.get("kind") == "LightTableRawPositive":
            return "light_table_raw_tiff"
        if payload.get("kind") == "ScannerRawNegative":
            return "scanner_raw_tiff"
    stem = Path(scanner_raw_path).stem.lower()
    if ".light_table_raw" in stem:
        return "light_table_raw_tiff"
    return "scanner_raw_tiff"


def created_at() -> str:
    return datetime.now().isoformat(timespec="seconds")


def scanner_interpreter_payload(config: DarkroomConfig) -> dict[str, Any]:
    scanner = config.scanner
    return {
        "interpretation_mode": scanner.interpretation_mode,
        "remove_base_mask": bool(scanner.remove_base_mask),
        "invert_transmission": bool(scanner.invert_transmission),
        "include_clear_base_border": bool(scanner.include_clear_base_border),
        "interpreter_key": scanner.interpreter_key,
        "target_medium_process": scanner.target_medium_process,
        "input_polarity": scanner.input_polarity,
        "output_polarity": scanner.output_polarity,
        "negative_channel_compensation_enabled": bool(
            scanner.negative_channel_compensation_enabled
        ),
        "negative_channel_compensation_strength": float(
            scanner.negative_channel_compensation_strength
        ),
        "transmission_light_ev": scanner.transmission_light_ev,
        "transmission_light_temperature_k": scanner.transmission_light_temperature_k,
    }


def scanned_positive_interpreter_payload(
    scanned: ScannedPositive | ScanOutput,
) -> dict[str, Any]:
    return {
        "interpreter_key": scanned.interpreter_key,
        "input_polarity": scanned.input_polarity,
        "output_polarity": scanned.output_polarity,
        "view_mode": scanned.view_mode,
    }


def scan_metadata_payload(scanned: ScannedPositive | ScanOutput) -> dict[str, Any]:
    return {key: value for key, value in scanned.metadata.items() if key != "runtime_config"}


def developed_negative_sidecar(
    *,
    input_path: Path,
    negative_path: Path,
    config: DarkroomConfig,
    negative: DevelopedMedium,
    paths: dict[str, str | None],
    resolved_seed: int | None,
    provenance: dict[str, Any] | None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    is_positive = str(getattr(negative, "image_polarity", "")).lower() == "positive"
    payload: dict[str, Any] = {
        "kind": "DevelopedPositiveTransparency" if is_positive else "DevelopedNegative",
        "created_at": created_at(),
        "project": PROJECT_NAME,
        "input_path": str(input_path),
        "negative_path": str(negative_path),
        "positive_path": str(negative_path) if is_positive else None,
        "output_path": str(output_path) if output_path is not None else None,
        "resolved_seed": resolved_seed,
        "paths": dict(paths),
        "developed_medium": developed_medium_metadata(negative),
        "interpreter": scanner_interpreter_payload(config),
        "provenance": provenance,
        "config": asdict(config),
    }
    return payload


def scanner_raw_sidecar(
    *,
    input_path: Path,
    negative_path: Path,
    scanner_raw_path: Path,
    config: DarkroomConfig,
    negative: DevelopedMedium,
    provenance: dict[str, Any] | None,
    border_width_px: int = 0,
) -> dict[str, Any]:
    is_positive = str(getattr(negative, "image_polarity", "")).lower() == "positive"
    return {
        "kind": "LightTableRawPositive" if is_positive else "ScannerRawNegative",
        "created_at": created_at(),
        "project": PROJECT_NAME,
        "input_path": str(input_path),
        "negative_path": str(negative_path),
        "positive_path": str(negative_path) if is_positive else None,
        "scanner_raw_path": str(scanner_raw_path),
        "light_table_raw_path": str(scanner_raw_path) if is_positive else None,
        "paths": {
            "negative_path": str(negative_path),
            "positive_path": str(negative_path) if is_positive else None,
            "scanner_raw_path": str(scanner_raw_path),
            "light_table_raw_path": str(scanner_raw_path) if is_positive else None,
        },
        "developed_medium": developed_medium_metadata(negative),
        "interpreter": scanner_interpreter_payload(config),
        "provenance": provenance,
        "encoding": "16-bit linear RGB TIFF, no sRGB gamma",
        "border": {
            "present": int(border_width_px) > 0,
            "meaning": "derived clear-film-base transmission reference; observation only",
            "percent": config.output.scanner_raw_border_percent,
            "min_px": config.output.scanner_raw_border_min_px,
            "width_px": max(int(border_width_px), 0),
        },
        "config": asdict(config),
    }


def layer_pack_metadata(
    *,
    input_path: Path,
    negative_path: Path,
    config: DarkroomConfig,
    negative: DevelopedMedium,
    paths: dict[str, str | None],
    output_path: Path | None = None,
) -> dict[str, Any]:
    return {
        "input_path": str(input_path),
        "negative_path": str(negative_path),
        "output_path": str(output_path) if output_path is not None else None,
        "paths": dict(paths),
        "developed_medium": developed_medium_metadata(negative),
        "interpreter": scanner_interpreter_payload(config),
        "config": asdict(config),
    }


def final_positive_sidecar(
    *,
    output_path: Path,
    config: DarkroomConfig,
    input_path: Path | None = None,
    negative_path: Path | None = None,
    scan_source: str | None = None,
    scan_source_path: Path | None = None,
    resolved_seed: int | None = None,
    preview: bool | None = None,
    scanned: ScannedPositive | ScanOutput | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_config = config
    if scanned is not None:
        scanned_runtime = scanned.metadata.get("runtime_config")
        if isinstance(scanned_runtime, DarkroomConfig):
            effective_config = scanned_runtime
    interpreter = scanned_positive_interpreter_payload(scanned) if scanned is not None else scanner_interpreter_payload(config)
    payload: dict[str, Any] = {
        "kind": "ScannedPositive",
        "created_at": created_at(),
        "project": PROJECT_NAME,
        "input_path": str(input_path) if input_path is not None else None,
        "negative_path": str(negative_path) if negative_path is not None else None,
        "scan_source": scan_source,
        "scan_source_path": str(scan_source_path) if scan_source_path is not None else None,
        "output_path": str(output_path),
        "preview": preview,
        "resolved_seed": resolved_seed,
        "paths": {
            "input_path": str(input_path) if input_path is not None else None,
            "negative_path": str(negative_path) if negative_path is not None else None,
            "scan_source_path": str(scan_source_path) if scan_source_path is not None else None,
            "output_path": str(output_path),
        },
        "interpreter": interpreter,
        "scan_metadata": scan_metadata_payload(scanned) if scanned is not None else {},
        "provenance": provenance,
        "config": asdict(effective_config),
    }
    return payload
