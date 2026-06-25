"""电子负片母版的安全/兼容读取工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os

import numpy as np

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
    if array.ndim != 3 or array.shape[-1] != 3:
        raise ValueError(f"{path} 中的 {key} 形状应为 HxWx3，实际为 {array.shape}。")
    return array


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
    legacy_reason: str | None = None
    try:
        with np.load(path, allow_pickle=False) as data:
            return (
                _as_density_array(data["density_cmy"], "density_cmy", path),
                _as_density_array(data["density_grain"], "density_grain", path),
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
            return (
                _as_density_array(data["density_cmy"], "density_cmy", path),
                _as_density_array(data["density_grain"], "density_grain", path),
            )
        for key in data.files:
            negative = _negative_from_object(data[key], path)
            if negative is not None:
                return negative
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
    empty = np.zeros_like(density_grain, dtype=np.float32)
    metadata: dict[str, Any] = {"negative_path": str(path)}
    sidecar_path = path.with_suffix(path.suffix + ".json")
    if sidecar_path.exists():
        try:
            with sidecar_path.open("r", encoding="utf-8") as handle:
                sidecar = json.load(handle)
            stored_config = sidecar.get("config")
            if isinstance(stored_config, dict):
                metadata["runtime_config"] = DarkroomConfig.from_dict(stored_config)
                metadata["sidecar_path"] = str(sidecar_path)
        except Exception as exc:
            metadata["sidecar_load_error"] = str(exc)
    return DevelopedNegative(
        linear_input=empty,
        after_mtf=empty,
        after_halation=empty,
        density_cmy=density_cmy,
        density_grain=density_grain,
        metadata=metadata,
    )
