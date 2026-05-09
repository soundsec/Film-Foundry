"""处理管线中的显式状态对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(slots=True)
class DevelopedNegative:
    """冲洗完成的底片状态；核心母版数据是 density_grain。"""

    linear_input: np.ndarray
    after_mtf: np.ndarray
    after_halation: np.ndarray
    density_cmy: np.ndarray
    density_grain: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScannedPositive:
    """扫描/打印解释后的正像状态。"""

    negative_linear: np.ndarray
    negative_base_balanced: np.ndarray
    positive_raw: np.ndarray
    scanner_raw: np.ndarray
    negative_total_density: np.ndarray
    positive_linear: np.ndarray
    output_srgb: np.ndarray
    positive_no_grain: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)
