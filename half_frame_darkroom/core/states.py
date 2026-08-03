"""处理管线中的显式状态对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(slots=True)
class DevelopedNegative:
    """冲洗完成的胶片介质，保存层母版与派生光学母版。

    ``density_cmy`` / ``density_grain`` 服务于层效果、制版、分层导出和
    旧格式兼容；存在 ``optical_density_rgb`` 时，后者是扫描器唯一的权威
    输入，但它仍是由最终介质派生的只读量，不取代材料状态。
    """

    linear_input: np.ndarray
    after_mtf: np.ndarray
    after_halation: np.ndarray
    density_cmy: np.ndarray
    density_grain: np.ndarray
    # Authoritative final optical observation when supplied by the unified
    # material-pool path.  ``density_cmy``/``density_grain`` remain portable
    # compatibility and layer-export masters, not the scanner's only truth.
    optical_density_rgb: np.ndarray | None = None
    clear_base_optical_density_rgb: tuple[float, float, float] | None = None
    medium_family: str = "film"
    medium_process: str = "negative"
    image_polarity: str = "negative"
    view_mode: str = "transmissive"
    base_type: str = "orange_mask"
    color_system: str = "color_negative_dye"
    compatible_interpreters: tuple[str, ...] = ("negative_scan",)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.optical_density_rgb is None:
            return
        optical = np.asarray(self.optical_density_rgb, dtype=np.float32)
        # 构造即转移派生母版的所有权；scanner 只能读取，不能回写。
        optical.setflags(write=False)
        self.optical_density_rgb = optical

    @property
    def layer_masters_available(self) -> bool:
        """Whether the portable CMY compatibility masters are resident.

        A scan-only NPZ load may intentionally keep only the authoritative RGB
        optical master.  Zero-sized CMY sentinels mean "not loaded", never a
        black or empty developed medium.  The ordinary/full loader continues
        to populate both layer masters.
        """
        density_cmy = np.asarray(self.density_cmy)
        density_grain = np.asarray(self.density_grain)
        return density_cmy.size > 0 and density_grain.size > 0

    @property
    def frame_shape(self) -> tuple[int, int, int]:
        """Return the authoritative observation geometry without copying."""
        if self.optical_density_rgb is not None:
            return tuple(int(value) for value in self.optical_density_rgb.shape)
        return tuple(int(value) for value in np.asarray(self.density_grain).shape)

    @property
    def legacy_formed_layer_density(self) -> np.ndarray:
        """Explicit semantic alias for the historical ``density_cmy`` field."""
        return self.density_cmy

    @property
    def legacy_composite_layer_density(self) -> np.ndarray:
        """Explicit semantic alias for the historical ``density_grain`` field.

        The composite includes emulsion grain and layer-space proxies for some
        post-process accidents, so it is not an isolated grain-delta map.
        """
        return self.density_grain


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


@dataclass(slots=True)
class ScanOutput:
    """Output-only scan result without Inspector stage retention."""

    output_srgb: np.ndarray
    interpreter_key: str = "negative_scan"
    input_polarity: str = "negative"
    output_polarity: str = "positive"
    view_mode: str = "display"
    metadata: dict[str, Any] = field(default_factory=dict)


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
        "authoritative_optical_density_rgb": getattr(medium, "optical_density_rgb", None) is not None,
        "array_semantics_version": 1,
        "array_semantics": {
            "density_cmy": {
                "role": "legacy_formed_layer_density",
                "legacy_field": True,
                "authoritative_for_scan": False,
                "description": (
                    "Three-channel compatibility layer proxy before emulsion grain "
                    "and post-process deposit proxies; not guaranteed to be pure dye CMY."
                ),
            },
            "density_grain": {
                "role": "legacy_composite_layer_density",
                "legacy_field": True,
                "authoritative_for_scan": False,
                "is_pure_grain_delta": False,
                "description": (
                    "Final compatibility layer proxy including emulsion grain and "
                    "post-process layer-proxy accidents; not an isolated grain field."
                ),
            },
            "optical_density_rgb": {
                "role": "derived_total_optical_density_rgb",
                "read_only": True,
                "authoritative_for_scan": getattr(medium, "optical_density_rgb", None) is not None,
                "description": (
                    "Derived total RGB optical-density master observed by the scanner; "
                    "it does not replace the final material state."
                ),
            },
        },
    }
    clear_base = getattr(medium, "clear_base_optical_density_rgb", None)
    if clear_base is not None:
        payload["clear_base_optical_density_rgb"] = [float(value) for value in clear_base]
    metadata = getattr(medium, "metadata", {})
    if isinstance(metadata, dict):
        process_model = metadata.get("film_process_model")
        if process_model is None and isinstance(metadata.get("developed_medium"), dict):
            process_model = metadata["developed_medium"].get("film_process_model")
        if isinstance(process_model, dict):
            payload["film_process_model"] = process_model
    return payload
