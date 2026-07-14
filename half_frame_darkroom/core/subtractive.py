"""减色输出兼容入口。

真正的扫描逻辑在 core/scanner.py 里显式拆成：
CMY density -> total negative density -> transmittance -> scanner raw -> positive render。
这个模块保留旧函数名，避免旧调用方失效。
"""

from __future__ import annotations

import numpy as np

from half_frame_darkroom.core.scanner import scan_negative_raw, scanner_raw_to_positive_rgb
from half_frame_darkroom.model.config import FilmStockConfig, ScannerConfig


def density_to_positive_rgb(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig | None = None,
    print_contrast: float = 1.0,
    print_exposure_ev: float = 0.0,
    paper_black: float = 0.0,
    paper_white: float = 1.0,
    base_samples: np.ndarray | None = None,
) -> np.ndarray:
    """兼容旧入口：CMY density -> scanner raw -> positive RGB。"""
    scanner_raw = scan_negative_raw(density_cmy, film, scanner)
    return scanner_raw_to_positive_rgb(
        scanner_raw,
        scanner,
        print_contrast=print_contrast,
        print_exposure_ev=print_exposure_ev,
        paper_black=paper_black,
        paper_white=paper_white,
        base_samples=base_samples,
        dye_absorption_matrix=film.dye_absorption_matrix,
    )
