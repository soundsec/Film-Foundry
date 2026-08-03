"""电子负片母版的安全/兼容读取工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import zipfile

import numpy as np

from half_frame_darkroom.core.atomic_io import strict_json_load, strict_json_loads
from half_frame_darkroom.core.states import DevelopedNegative
from half_frame_darkroom.model.config import DarkroomConfig


LEGACY_PICKLE_ENV = "FILM_FOUNDRY_ALLOW_LEGACY_PICKLE"


def _as_density_array(value: Any, key: str, path: Path) -> np.ndarray:
    """把读取到的密度数据整理成 float32 HxWx3 数组。"""
    array = np.asarray(value)
    if array.dtype == object:
        if array.shape == ():
            array = np.asarray(array.item())
        if array.dtype == object:
            raise ValueError(f"{path} 中的 {key} 仍是 object 数据，无法安全解释为密度数组。")
    array = np.asarray(array, dtype=np.float32)
    if array.ndim != 3 or array.shape[-1] != 3 or array.shape[0] <= 0 or array.shape[1] <= 0:
        raise ValueError(f"{path} 中的 {key} 形状应为 HxWx3，实际为 {array.shape}。")
    minimum = float(np.min(array))
    maximum = float(np.max(array))
    if not np.isfinite(minimum) or not np.isfinite(maximum):
        raise ValueError(f"{path} 中的 {key} 包含 NaN 或 Infinity，不能作为介质密度读取。")
    return array


def _validate_density_pair(
    density_cmy: np.ndarray,
    density_grain: np.ndarray,
    path: Path,
) -> tuple[np.ndarray, np.ndarray]:
    if density_cmy.shape != density_grain.shape:
        raise ValueError(
            f"{path} 中的 density_cmy 与 density_grain 形状不一致："
            f"{density_cmy.shape} != {density_grain.shape}。"
        )
    return density_cmy, density_grain


def _negative_from_object(value: Any, path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """兼容极早期可能把 DevelopedNegative/dict 直接塞进 npz 的实验文件。"""
    obj = np.asarray(value, dtype=object)
    if obj.shape == ():
        obj = obj.item()
    if isinstance(obj, DevelopedNegative):
        return (
            _as_density_array(obj.density_cmy, "density_cmy", path),
            _as_density_array(obj.density_grain, "density_grain", path),
        )
    if isinstance(obj, dict) and "density_cmy" in obj and "density_grain" in obj:
        return (
            _as_density_array(obj["density_cmy"], "density_cmy", path),
            _as_density_array(obj["density_grain"], "density_grain", path),
        )
    if hasattr(obj, "density_cmy") and hasattr(obj, "density_grain"):
        return (
            _as_density_array(getattr(obj, "density_cmy"), "density_cmy", path),
            _as_density_array(getattr(obj, "density_grain"), "density_grain", path),
        )
    return None


def _allow_legacy_pickle(value: bool | None) -> bool:
    if value is not None:
        return bool(value)
    return os.environ.get(LEGACY_PICKLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _read_developed_medium_metadata_from_npz(path: Path) -> dict[str, Any]:
    try:
        with np.load(path, allow_pickle=False) as data:
            if "developed_medium_metadata" not in data.files:
                return {}
            raw = data["developed_medium_metadata"]
            if raw.shape != ():
                raise ValueError(
                    f"{path} 中的 developed_medium_metadata 必须是单个 JSON 字符串。"
                )
            payload = strict_json_loads(str(raw.item()))
            if not isinstance(payload, dict):
                raise ValueError(f"{path} 中的 developed_medium_metadata 根节点必须是对象。")
            return payload
    except (OSError, EOFError, zipfile.BadZipFile) as exc:
        raise ValueError(f"无法读取介质 metadata；NPZ 可能已截断或损坏：{path}") from exc
    except (json.JSONDecodeError, UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, ValueError) and str(path) in str(exc):
            raise
        raise ValueError(f"{path} 中的 developed_medium_metadata 不是有效 JSON：{exc}") from exc


def _metadata_kwargs(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    keys = {
        "medium_family",
        "medium_process",
        "image_polarity",
        "view_mode",
        "base_type",
        "color_system",
        "compatible_interpreters",
    }
    kwargs = {key: payload[key] for key in keys if key in payload}
    if "compatible_interpreters" in kwargs:
        interpreters = kwargs["compatible_interpreters"]
        if not isinstance(interpreters, (list, tuple)) or isinstance(interpreters, str):
            raise ValueError(f"{path} 中的 compatible_interpreters 必须是字符串列表。")
        normalized = tuple(str(value).strip() for value in interpreters)
        if not normalized or any(not value for value in normalized):
            raise ValueError(f"{path} 中的 compatible_interpreters 不能为空。")
        kwargs["compatible_interpreters"] = normalized
    for key, value in list(kwargs.items()):
        if key != "compatible_interpreters":
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{path} 中的介质 metadata 字段 '{key}' 必须是非空字符串。")
            kwargs[key] = str(value)
    polarity = str(kwargs.get("image_polarity", "")).lower()
    compatible = tuple(kwargs.get("compatible_interpreters", ()))
    if not polarity:
        if "positive_transparency_scan" in compatible:
            polarity = "positive"
            kwargs["image_polarity"] = polarity
        elif "negative_scan" in compatible:
            polarity = "negative"
            kwargs["image_polarity"] = polarity
    if polarity and polarity not in {"negative", "positive"}:
        raise ValueError(f"{path} 中的 image_polarity 必须是 negative 或 positive。")
    if polarity == "positive":
        kwargs.setdefault("medium_family", "film")
        kwargs.setdefault("medium_process", "slide")
        kwargs.setdefault("view_mode", "transmissive")
        kwargs.setdefault("base_type", "clear_base")
        kwargs.setdefault("color_system", "positive_dye")
        kwargs.setdefault("compatible_interpreters", ("positive_transparency_scan",))
        compatible = tuple(kwargs["compatible_interpreters"])
    elif polarity == "negative":
        kwargs.setdefault("medium_family", "film")
        kwargs.setdefault("medium_process", "negative")
        kwargs.setdefault("view_mode", "transmissive")
        kwargs.setdefault("base_type", "orange_mask")
        kwargs.setdefault("color_system", "color_negative_dye")
        kwargs.setdefault("compatible_interpreters", ("negative_scan",))
        compatible = tuple(kwargs["compatible_interpreters"])
    if polarity == "positive" and compatible and "positive_transparency_scan" not in compatible:
        raise ValueError(f"{path} 的正片 metadata 没有兼容的 positive_transparency_scan 解释器。")
    if polarity == "negative" and compatible and "negative_scan" not in compatible:
        raise ValueError(f"{path} 的负片 metadata 没有兼容的 negative_scan 解释器。")
    return kwargs


def _unavailable_rgb_field() -> np.ndarray:
    """Return an explicit zero-allocation sentinel for an unavailable RGB field."""
    return np.empty((0, 0, 3), dtype=np.float32)


def _load_medium_metadata(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load portable identity/runtime metadata shared by full and scan-only loads."""
    metadata: dict[str, Any] = {"negative_path": str(path)}
    medium_payload = _read_developed_medium_metadata_from_npz(path)
    sidecar_path = path.with_suffix(path.suffix + ".json")
    if sidecar_path.exists():
        try:
            sidecar = strict_json_load(sidecar_path)
            if not isinstance(sidecar, dict):
                raise ValueError("sidecar root must be a JSON object")
            if "config" in sidecar:
                stored_config = sidecar["config"]
                if not isinstance(stored_config, dict):
                    raise ValueError("sidecar config must be a JSON object")
                metadata["runtime_config"] = DarkroomConfig.from_dict(stored_config)
                metadata["sidecar_path"] = str(sidecar_path)
            if "developed_medium" in sidecar:
                sidecar_medium = sidecar["developed_medium"]
                if not isinstance(sidecar_medium, dict):
                    raise ValueError("sidecar developed_medium must be a JSON object")
                if not medium_payload:
                    medium_payload = sidecar_medium
        except Exception as exc:
            raise ValueError(f"Invalid developed-medium sidecar '{sidecar_path}': {exc}") from exc
    if not medium_payload and ".darkroom_positive" in path.stem.lower():
        medium_payload = {
            "medium_family": "film",
            "medium_process": "slide",
            "image_polarity": "positive",
            "view_mode": "transmissive",
            "base_type": "clear_base",
            "color_system": "positive_dye",
            "compatible_interpreters": ["positive_transparency_scan"],
        }
        metadata["medium_identity_source"] = "filename_fallback"
    if medium_payload:
        metadata["developed_medium"] = medium_payload
        if isinstance(medium_payload.get("film_process_model"), dict):
            metadata["film_process_model"] = medium_payload["film_process_model"]
    return metadata, medium_payload, _metadata_kwargs(medium_payload, path)


def load_negative_density_arrays(
    path: str | Path,
    *,
    allow_legacy_pickle: bool | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """读取 .npz 电子负片，只返回 density_cmy 和 density_grain。

    默认只用 allow_pickle=False 的安全路径读取。旧实验文件如果混入 object/pickle
    数据，必须显式启用 allow_legacy_pickle 或设置 FILM_FOUNDRY_ALLOW_LEGACY_PICKLE=1。
    即便启用兼容读取，也只接受可转换为数值数组的密度数据。
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Developed-medium NPZ does not exist or is empty: {path}")
    if not zipfile.is_zipfile(path):
        raise ValueError(f"Developed-medium NPZ is truncated or not a valid ZIP/NPZ archive: {path}")
    legacy_reason: str | None = None
    try:
        with np.load(path, allow_pickle=False) as data:
            density_cmy = _as_density_array(data["density_cmy"], "density_cmy", path)
            density_grain = _as_density_array(data["density_grain"], "density_grain", path)
            return _validate_density_pair(
                density_cmy,
                density_grain,
                path,
            )
    except KeyError as exc:
        # 旧实验文件可能把整个 DevelopedNegative/dict 存在单个 object key 里。
        legacy_reason = f"missing density key: {exc}"
    except ValueError as exc:
        message = str(exc)
        object_pickle_error = "Object arrays cannot be loaded" in message or "pickled" in message
        if not object_pickle_error:
            raise
        legacy_reason = message

    if not _allow_legacy_pickle(allow_legacy_pickle):
        raise ValueError(
            f"{path} looks like a legacy object/pickled negative file ({legacy_reason}). "
            "For safety, Film Foundry does not load pickled .npz data by default. "
            f"If this file was created by your own old experiment and you trust it, set "
            f"{LEGACY_PICKLE_ENV}=1 or call load_negative_density_arrays(..., allow_legacy_pickle=True)."
        )

    with np.load(path, allow_pickle=True) as data:
        files = set(data.files)
        if "density_cmy" in files and "density_grain" in files:
            return _validate_density_pair(
                _as_density_array(data["density_cmy"], "density_cmy", path),
                _as_density_array(data["density_grain"], "density_grain", path),
                path,
            )
        for key in data.files:
            negative = _negative_from_object(data[key], path)
            if negative is not None:
                return _validate_density_pair(*negative, path)
    raise ValueError(f"{path} 不是可识别的电子负片 .npz，缺少 density_cmy / density_grain。")


def load_developed_negative_npz(
    path: str | Path,
    *,
    allow_legacy_pickle: bool | None = None,
) -> DevelopedNegative:
    """从 .npz 母版读取 DevelopedNegative 状态对象。"""
    path = Path(path)
    density_cmy, density_grain = load_negative_density_arrays(
        path,
        allow_legacy_pickle=allow_legacy_pickle,
    )
    optical_density_rgb: np.ndarray | None = None
    clear_base_optical_density_rgb: tuple[float, float, float] | None = None
    with np.load(path, allow_pickle=False) as data:
        if "optical_density_rgb" in data.files:
            optical_density_rgb = _as_density_array(
                data["optical_density_rgb"],
                "optical_density_rgb",
                path,
            )
            if optical_density_rgb.shape != density_grain.shape:
                raise ValueError(
                    f"{path} optical_density_rgb shape does not match density_grain: "
                    f"{optical_density_rgb.shape} != {density_grain.shape}"
                )
            if float(np.min(optical_density_rgb)) < 0.0:
                raise ValueError(f"{path} optical_density_rgb contains negative density")
        if "clear_base_optical_density_rgb" in data.files:
            clear = np.asarray(data["clear_base_optical_density_rgb"], dtype=np.float32)
            if clear.shape != (3,) or not np.all(np.isfinite(clear)) or np.any(clear < 0.0):
                raise ValueError(
                    f"{path} clear_base_optical_density_rgb must contain three finite nonnegative values"
                )
            clear_base_optical_density_rgb = tuple(float(value) for value in clear)
    metadata, medium_payload, identity_kwargs = _load_medium_metadata(path)
    metadata["stage_storage"] = {
        "profile": "portable_layers_without_history_v1",
        "history": "unavailable",
        "layer_masters": "resident",
        "optical_master": "resident" if optical_density_rgb is not None else "derived_on_scan",
    }
    return DevelopedNegative(
        linear_input=_unavailable_rgb_field(),
        after_mtf=_unavailable_rgb_field(),
        after_halation=_unavailable_rgb_field(),
        density_cmy=density_cmy,
        density_grain=density_grain,
        optical_density_rgb=optical_density_rgb,
        clear_base_optical_density_rgb=clear_base_optical_density_rgb,
        **identity_kwargs,
        metadata=metadata,
    )


def load_developed_medium_for_scan(
    path: str | Path,
    *,
    allow_legacy_pickle: bool | None = None,
) -> DevelopedNegative:
    """Load the smallest exact medium representation required by a scanner.

    Modern archives carry an authoritative ``optical_density_rgb`` master. In
    that case the layer compatibility masters remain safely stored in the NPZ
    but are not decompressed into RAM. Older archives transparently fall back
    to :func:`load_developed_negative_npz`, preserving the complete legacy
    reconstruction path. No saved data or scan formula is changed.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Developed-medium NPZ does not exist or is empty: {path}")
    if not zipfile.is_zipfile(path):
        raise ValueError(
            f"Developed-medium NPZ is truncated or not a valid ZIP/NPZ archive: {path}"
        )

    optical_density_rgb: np.ndarray | None = None
    clear_base_optical_density_rgb: tuple[float, float, float] | None = None
    try:
        with np.load(path, allow_pickle=False) as data:
            if "optical_density_rgb" in data.files:
                optical_density_rgb = _as_density_array(
                    data["optical_density_rgb"],
                    "optical_density_rgb",
                    path,
                )
                if float(np.min(optical_density_rgb)) < 0.0:
                    raise ValueError(f"{path} optical_density_rgb contains negative density")
                if "clear_base_optical_density_rgb" in data.files:
                    clear = np.asarray(
                        data["clear_base_optical_density_rgb"],
                        dtype=np.float32,
                    )
                    if (
                        clear.shape != (3,)
                        or not np.all(np.isfinite(clear))
                        or np.any(clear < 0.0)
                    ):
                        raise ValueError(
                            f"{path} clear_base_optical_density_rgb must contain three "
                            "finite nonnegative values"
                        )
                    clear_base_optical_density_rgb = tuple(float(value) for value in clear)
    except (OSError, EOFError, zipfile.BadZipFile) as exc:
        raise ValueError(
            f"Unable to read developed-medium optical master; NPZ may be truncated or corrupt: {path}"
        ) from exc

    if optical_density_rgb is None:
        return load_developed_negative_npz(
            path,
            allow_legacy_pickle=allow_legacy_pickle,
        )

    metadata, _medium_payload, identity_kwargs = _load_medium_metadata(path)
    metadata["stage_storage"] = {
        "profile": "scan_optical_only_v1",
        "history": "unavailable",
        "layer_masters": "stored_not_loaded",
        "optical_master": "resident_authoritative",
        "reversible_full_loader": "load_developed_negative_npz",
    }
    return DevelopedNegative(
        linear_input=_unavailable_rgb_field(),
        after_mtf=_unavailable_rgb_field(),
        after_halation=_unavailable_rgb_field(),
        density_cmy=_unavailable_rgb_field(),
        density_grain=_unavailable_rgb_field(),
        optical_density_rgb=optical_density_rgb,
        clear_base_optical_density_rgb=clear_base_optical_density_rgb,
        **identity_kwargs,
        metadata=metadata,
    )
