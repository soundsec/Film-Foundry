"""处理管线中的显式状态对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(slots=True)
class DevelopedNegative:
    """冲洗完成的负片或反转正片状态；核心母版数据是 density_grain。"""

    linear_input: np.ndarray
    after_mtf: np.ndarray
    after_halation: np.ndarray
    density_cmy: np.ndarray
    density_grain: np.ndarray
    medium_family: str = "film"
    medium_process: str = "negative"
    image_polarity: str = "negative"
    view_mode: str = "transmissive"
    base_type: str = "orange_mask"
    color_system: str = "color_negative_dye"
    compatible_interpreters: tuple[str, ...] = ("negative_scan",)
    metadata: dict[str, Any] = field(default_factory=dict)


# Generic aliases for future media such as slides, instant film, direct-positive
# paper, daguerreotype-style plates, and other image-bearing materials.
DevelopedMedium = DevelopedNegative


@dataclass(slots=True)
class ScannedPositive:
    """扫描/打印解释后的正像状态。"""

    negative_linear: np.ndarray
    negative_base_balanced: np.ndarray
    positive_raw: np.ndarray
    negative_channel_reconstructed: np.ndarray
    scanner_raw: np.ndarray
    negative_total_density: np.ndarray
    positive_linear: np.ndarray
    output_srgb: np.ndarray
    positive_no_grain: np.ndarray
    interpreter_key: str = "negative_scan"
    input_polarity: str = "negative"
    output_polarity: str = "positive"
    view_mode: str = "display"
    metadata: dict[str, Any] = field(default_factory=dict)


RenderedPositive = ScannedPositive


def developed_medium_metadata(medium: DevelopedMedium) -> dict[str, Any]:
    """Return portable metadata that identifies how a developed medium is viewed."""
    payload = {
        "medium_family": getattr(medium, "medium_family", "film"),
        "medium_process": getattr(medium, "medium_process", "negative"),
        "image_polarity": getattr(medium, "image_polarity", "negative"),
        "view_mode": getattr(medium, "view_mode", "transmissive"),
        "base_type": getattr(medium, "base_type", "orange_mask"),
        "color_system": getattr(medium, "color_system", "color_negative_dye"),
        "compatible_interpreters": list(getattr(medium, "compatible_interpreters", ("negative_scan",))),
    }
    metadata = getattr(medium, "metadata", {})
    if isinstance(metadata, dict):
        process_model = metadata.get("film_process_model")
        if process_model is None and isinstance(metadata.get("developed_medium"), dict):
            process_model = metadata["developed_medium"].get("film_process_model")
        if isinstance(process_model, dict):
            payload["film_process_model"] = process_model
    return payload
