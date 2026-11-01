"""Exposed, undeveloped latent-state interchange.

This archive is the frozen material state after ``expose()`` and before any
process program. It is shared-kernel interchange, not a mobile-only format.
Scene JPEG / CameraX frames are not latent. Developed NPZ files are not latent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import zipfile

import numpy as np

from film_foundry.core.atomic_io import atomic_savez, strict_json_loads
from film_foundry.core.film_process.model import FilmProcessState
from film_foundry.core.material_geometry import MaterialGeometry
from film_foundry.contracts.spectral_exposure import SpectralExposureProfile
from film_foundry.contracts.spectral_optics import SpectralMaterialProfile
from film_foundry.contracts.material_snapshot import MaterialSnapshot
from film_foundry.contracts.data_kernel import MATERIAL_SNAPSHOT_KEY
from film_foundry.core.frame_identity import (
    FRAME_IDENTITY_KEY,
    attach_frame_identity,
    normalize_frame_identity,
)
from film_foundry.model.config import FilmStockConfig


LATENT_SUFFIX = ".darkroom_latent.npz"
LATENT_KIND = "exposed_undeveloped_latent_v1"
LATENT_STAGE = "exposed_latent"
LATENT_JSON_KEY = "latent_medium_metadata"
LATENT_REQUIRED_ARRAY_KEYS = ("halide", "developability", "metallic_silver")
LATENT_OPTIONAL_ARRAY_KEYS = (
    "coupler",
    "dye",
    "bleached_halide",
    "original_developability",
)


def latent_identity_from_film(film: FilmStockConfig) -> dict[str, Any]:
    """Portable identity copied from an existing film stock, not a new schema."""
    polarity = str(film.image_polarity).strip().lower() or "negative"
    if polarity not in {"negative", "positive"}:
        raise ValueError(f"image_polarity must be negative or positive, got {polarity!r}")
    mono = str(film.color_process).strip().lower() in {"bw", "black_white", "monochrome"}
    if polarity == "positive":
        return {
            "medium_family": str(film.medium_family or "film"),
            "medium_process": str(film.medium_process or "slide"),
            "image_polarity": "positive",
            "view_mode": "transmissive",
            "base_type": "clear_base",
            "color_system": "bw_silver" if mono else "positive_dye",
            "compatible_interpreters": ["positive_transparency_scan"],
        }
    return {
        "medium_family": str(film.medium_family or "film"),
        "medium_process": str(film.medium_process or "negative"),
        "image_polarity": "negative",
        "view_mode": "transmissive",
        "base_type": "clear_base" if mono else "orange_mask",
        "color_system": "bw_silver" if mono else "color_negative_dye",
        "compatible_interpreters": ["negative_scan"],
    }


def _as_layer_pool(value: Any, key: str, path: Path, *, layer_count: int) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype == object:
        raise ValueError(f"{path} 中的 {key} 仍是 object 数据，不能作为潜影池读取。")
    array = np.asarray(array, dtype=np.float32)
    if (
        array.ndim != 3
        or array.shape[0] <= 0
        or array.shape[1] <= 0
        or int(array.shape[2]) != int(layer_count)
    ):
        raise ValueError(
            f"{path} 中的 {key} 形状应为 HxWx{layer_count}，实际为 {array.shape}。"
        )
    minimum = float(np.min(array))
    maximum = float(np.max(array))
    if not np.isfinite(minimum) or not np.isfinite(maximum):
        raise ValueError(f"{path} 中的 {key} 包含 NaN 或 Infinity。")
    return array


def latent_medium_metadata(
    state: FilmProcessState,
    identity: dict[str, Any],
) -> dict[str, Any]:
    """JSON object stored beside the pool arrays."""
    state.validate()
    payload = {
        "kind": LATENT_KIND,
        "stage": LATENT_STAGE,
        "layer_count": int(np.asarray(state.developability).shape[-1]),
        "auxiliary_remaining": float(state.auxiliary_remaining),
        "masking_coupler_remaining": float(state.masking_coupler_remaining),
        "consumable": True,
        "consumed": False,
    }
    payload.update(identity)
    if state.material_snapshot is not None:
        source = state.material_snapshot.parameters()
        native = FilmStockConfig(**{key: source[key] for key in (
            "medium_family", "medium_process", "image_polarity", "color_process") if key in source})
        payload.update(latent_identity_from_film(native))
        payload[MATERIAL_SNAPSHOT_KEY] = state.material_snapshot.as_dict()
    elif MATERIAL_SNAPSHOT_KEY in payload:
        raise ValueError("material_snapshot belongs to the latent state, not its identity")
    if state.material_geometry is not None:
        payload["material_geometry"] = state.material_geometry.as_dict()
    elif "material_geometry" in payload:
        raise ValueError("material_geometry belongs to the latent state, not its identity")
    if state.spectral_exposure_profile is not None:
        payload["spectral_exposure_profile"] = state.spectral_exposure_profile.as_dict()
    elif "spectral_exposure_profile" in payload:
        raise ValueError("spectral_exposure_profile belongs to the latent state, not its identity")
    if state.spectral_material_profile is not None:
        payload["spectral_material_profile"] = state.spectral_material_profile.as_dict()
    elif "spectral_material_profile" in payload:
        raise ValueError("spectral_material_profile belongs to the latent state, not its identity")
    attach_frame_identity(payload, identity.get(FRAME_IDENTITY_KEY))
    return payload


def save_exposed_latent(
    path: Path,
    state: FilmProcessState,
    *,
    identity: dict[str, Any],
    consumed: bool = False,
) -> Path:
    """Write an undeveloped latent archive. Does not consume or mutate ``state``."""
    path = Path(path)
    if not path.name.lower().endswith(LATENT_SUFFIX):
        raise ValueError(f"Latent archive must use suffix {LATENT_SUFFIX}, got {path.name!r}.")
    state.validate()
    metadata = latent_medium_metadata(state, identity)
    metadata["consumed"] = bool(consumed)
    arrays: dict[str, np.ndarray] = {
        "halide": np.asarray(state.halide, dtype=np.float32),
        "developability": np.asarray(state.developability, dtype=np.float32),
        "metallic_silver": np.asarray(state.metallic_silver, dtype=np.float32),
        LATENT_JSON_KEY: np.asarray(
            json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        ),
    }
    for key in LATENT_OPTIONAL_ARRAY_KEYS:
        value = getattr(state, key)
        if value is not None:
            arrays[key] = np.asarray(value, dtype=np.float32)
    return atomic_savez(path, compressed=True, compression_level=1, **arrays)


def load_exposed_latent(
    path: Path,
    *,
    allow_consumed: bool = False,
) -> tuple[FilmProcessState, dict[str, Any]]:
    """Load a frozen latent state. The file is not marked consumed."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Latent NPZ does not exist or is empty: {path}")
    if not zipfile.is_zipfile(path):
        raise ValueError(f"Latent NPZ is truncated or not a valid ZIP/NPZ archive: {path}")
    try:
        with np.load(path, allow_pickle=False) as data:
            files = set(data.files)
            if "density_cmy" in files and "developability" not in files:
                raise ValueError(
                    f"{path} 是已形成介质，不是未冲洗潜影；请使用 developed-medium 加载入口。"
                )
            if LATENT_JSON_KEY not in files:
                raise ValueError(f"{path} 缺少 {LATENT_JSON_KEY}。")
            raw = data[LATENT_JSON_KEY]
            if raw.shape != ():
                raise ValueError(f"{path} 中的 {LATENT_JSON_KEY} 必须是单个 JSON 字符串。")
            metadata = strict_json_loads(str(raw.item()))
            if not isinstance(metadata, dict):
                raise ValueError(f"{path} 中的 {LATENT_JSON_KEY} 根节点必须是对象。")
            if str(metadata.get("kind") or "") != LATENT_KIND:
                raise ValueError(
                    f"{path} kind 必须是 {LATENT_KIND!r}，实际为 {metadata.get('kind')!r}。"
                )
            if str(metadata.get("stage") or "") != LATENT_STAGE:
                raise ValueError(
                    f"{path} stage 必须是 {LATENT_STAGE!r}，实际为 {metadata.get('stage')!r}。"
                )
            if FRAME_IDENTITY_KEY in metadata:
                normalized = normalize_frame_identity(
                    metadata.get(FRAME_IDENTITY_KEY),
                    path=path,
                )
                if normalized is None:
                    metadata.pop(FRAME_IDENTITY_KEY, None)
                else:
                    metadata[FRAME_IDENTITY_KEY] = normalized
            layer_count = int(metadata.get("layer_count") or 0)
            if layer_count <= 0:
                raise ValueError(f"{path} layer_count 必须是正整数。")
            pools: dict[str, np.ndarray] = {}
            for key in LATENT_REQUIRED_ARRAY_KEYS:
                if key not in files:
                    raise ValueError(f"{path} 缺少潜影池 {key}。")
                pools[key] = _as_layer_pool(data[key], key, path, layer_count=layer_count)
            optional: dict[str, np.ndarray | None] = {}
            for key in LATENT_OPTIONAL_ARRAY_KEYS:
                if key in files:
                    optional[key] = _as_layer_pool(
                        data[key], key, path, layer_count=layer_count
                    )
                else:
                    optional[key] = None
    except (OSError, EOFError, zipfile.BadZipFile) as exc:
        raise ValueError(f"无法读取潜影归档；NPZ 可能已截断或损坏：{path}") from exc

    state = FilmProcessState(
        halide=pools["halide"],
        developability=pools["developability"],
        metallic_silver=pools["metallic_silver"],
        coupler=optional["coupler"],
        dye=optional["dye"],
        bleached_halide=optional["bleached_halide"],
        original_developability=optional["original_developability"],
        auxiliary_remaining=float(metadata.get("auxiliary_remaining", 1.0)),
        masking_coupler_remaining=float(metadata.get("masking_coupler_remaining", 1.0)),
        material_geometry=(
            MaterialGeometry.from_dict(metadata["material_geometry"])
            if "material_geometry" in metadata else None
        ),
        spectral_exposure_profile=(
            SpectralExposureProfile.from_value(metadata["spectral_exposure_profile"])
            if "spectral_exposure_profile" in metadata else None
        ),
        spectral_material_profile=(
            SpectralMaterialProfile.from_value(metadata["spectral_material_profile"])
            if "spectral_material_profile" in metadata else None
        ),
        material_snapshot=(MaterialSnapshot.from_value(metadata[MATERIAL_SNAPSHOT_KEY])
                           if MATERIAL_SNAPSHOT_KEY in metadata else None),
    )
    state.validate()
    if bool(metadata.get("consumed")) and not allow_consumed:
        raise ValueError(
            f"{path} 中的潜影已消耗，不能再作为未冲洗原件使用。请使用保留的未消耗原档。"
        )
    return state, metadata


def mark_exposed_latent_consumed(path: Path) -> Path:
    """Rewrite metadata so the same file is no longer a fresh undeveloped original."""
    path = Path(path)
    state, metadata = load_exposed_latent(path, allow_consumed=True)
    if bool(metadata.get("consumed")):
        return path
    identity = {
        key: metadata[key]
        for key in (
            "medium_family",
            "medium_process",
            "image_polarity",
            "view_mode",
            "base_type",
            "color_system",
            "compatible_interpreters",
            FRAME_IDENTITY_KEY,
        )
        if key in metadata
    }
    return save_exposed_latent(path, state, identity=identity, consumed=True)
