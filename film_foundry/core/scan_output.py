"""Frozen scan-output parameters and the CPU reference for future compute backends.

This stage starts after scene/light/color interpretation and full-frame statistics.
It neither observes nor mutates the developed medium, and never estimates a tile's
own calibration. No graphics context, global backend, or hardware dependency lives
here. Keep the CPU implementation available when adding an optional backend.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from film_foundry.core.color import linear_to_srgb
from film_foundry.core.scanner import (
    PreparedScannerToneCurve,
    apply_scan_normalization_range,
)
from film_foundry.model.config import ScannerConfig


@dataclass(frozen=True, slots=True)
class ScanOutputPlan:
    """One immutable calibration shared by all output blocks of a single scan."""

    black: tuple[float, float, float] | None
    white: tuple[float, float, float] | None
    strength: float
    mode: str
    tone: PreparedScannerToneCurve

    @classmethod
    def prepare(
        cls, scanner: ScannerConfig, *,
        black: np.ndarray | None = None, white: np.ndarray | None = None,
    ) -> ScanOutputPlan:
        normalize = scanner.scan_normalize and float(scanner.scan_normalize_strength) > 0.0

        def triplet(value: np.ndarray | None) -> tuple[float, float, float]:
            if value is None:
                raise ValueError("Output normalization requires full-frame black and white points")
            array = np.asarray(value, dtype=np.float32)
            if array.shape != (3,) or not np.isfinite(array).all():
                raise ValueError("Output calibration must contain three finite values")
            return tuple(float(v) for v in array)

        return cls(
            triplet(black) if normalize else None,
            triplet(white) if normalize else None,
            float(scanner.scan_normalize_strength) if normalize else 0.0,
            str(scanner.scan_normalize_mode),
            PreparedScannerToneCurve.prepare(scanner.tone_curve),
        )

    def apply_cpu(self, image: np.ndarray, *, consume_input: bool = False) -> np.ndarray:
        """Encode one RGB block, preserving borrowed/read-only inputs by default.

        ``consume_input`` transfers only a private writable working block. The
        same NumPy formulas/order/dtypes as the existing scanner remain authoritative.
        A future backend must return to this path when unsupported or unqualified.
        """
        image = np.asarray(image, dtype=np.float32)
        if image.ndim != 3 or image.shape[-1] != 3:
            raise ValueError("Scan output needs an HxWx3 RGB block")
        if self.black is not None:
            image = apply_scan_normalization_range(
                image, self.black, self.white, strength=self.strength,
                mode=self.mode, consume_input=consume_input,
            )
            # Normalization either consumed the private block or created one.
            consume_input = True
        encoded = linear_to_srgb(image, consume_input=consume_input)
        return self.tone.apply(encoded, consume_input=True)
