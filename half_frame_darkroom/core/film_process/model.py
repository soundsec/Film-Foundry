"""Material, latent-state, and final-medium objects for reduced film processes."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _vector(values, length: int, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if array.shape != (length,):
        raise ValueError(f"{name} must contain {length} values, got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} contains non-finite values")
    return array


def _matrix(values, rows: int, columns: int, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if array.shape != (rows, columns):
        raise ValueError(f"{name} must have shape {(rows, columns)}, got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} contains non-finite values")
    return array


@dataclass(frozen=True, slots=True)
class ReducedFilmMaterial:
    """Static material capacities and reduced optical coefficients.

    Pool quantities use normalized, internally consistent units rather than
    physical moles.  The optical coefficients convert final pool amounts into
    RGB optical density, which keeps the state model useful without requiring a
    full spectral simulation.
    """

    key: str
    layer_count: int
    halide_capacity: tuple[float, ...]
    coupler_capacity: tuple[float, ...] | None = None
    dye_absorption_matrix: tuple[tuple[float, ...], ...] | None = None
    silver_density_per_layer: tuple[float, ...] | None = None
    residual_halide_density_per_layer: tuple[float, ...] | None = None
    base_density_rgb: tuple[float, float, float] = (0.0, 0.0, 0.0)
    auxiliary_density_rgb: tuple[float, float, float] = (0.0, 0.0, 0.0)
    medium_family: str = "film"
    color_system: str = "silver_halide"
    # Retained silver halide is not treated as metallic silver. This fallback
    # approximates a blue-density rise; normal construction supplies the
    # material-specific value from FilmStockConfig.
    retained_halide_density_rgb: tuple[float, float, float] = (0.62, 0.82, 1.0)

    def __post_init__(self) -> None:
        if int(self.layer_count) <= 0:
            raise ValueError("layer_count must be positive")
        layers = int(self.layer_count)
        _vector(self.halide_capacity, layers, "halide_capacity")
        if self.coupler_capacity is not None:
            _vector(self.coupler_capacity, layers, "coupler_capacity")
        if self.dye_absorption_matrix is not None:
            _matrix(self.dye_absorption_matrix, 3, layers, "dye_absorption_matrix")
        if self.silver_density_per_layer is not None:
            _vector(self.silver_density_per_layer, layers, "silver_density_per_layer")
        if self.residual_halide_density_per_layer is not None:
            _vector(
                self.residual_halide_density_per_layer,
                layers,
                "residual_halide_density_per_layer",
            )
        _vector(self.base_density_rgb, 3, "base_density_rgb")
        _vector(self.auxiliary_density_rgb, 3, "auxiliary_density_rgb")
        _vector(self.retained_halide_density_rgb, 3, "retained_halide_density_rgb")

    def expose(self, layer_exposure: np.ndarray, half_saturation: float = 0.18) -> "FilmProcessState":
        """Create a latent state using one continuous developability field.

        ``developability`` is the reduced-order equivalent of the exposed
        halide fraction.  Unexposed halide remains implicit as
        ``halide * (1 - developability)`` until an activation operator changes
        it, avoiding a duplicate full-resolution pool.
        """
        exposure = np.clip(np.asarray(layer_exposure, dtype=np.float32), 0.0, None)
        if exposure.ndim < 1 or exposure.shape[-1] != self.layer_count:
            raise ValueError(
                f"layer_exposure must end with {self.layer_count} layers, got {exposure.shape}"
            )
        half = max(float(half_saturation), 1e-6)
        developability = exposure / (exposure + half)
        capacity = _vector(self.halide_capacity, self.layer_count, "halide_capacity")
        halide = np.broadcast_to(capacity, exposure.shape).copy()
        silver = np.zeros_like(halide, dtype=np.float32)
        if self.coupler_capacity is None:
            coupler = None
            dye = None
        else:
            coupler_capacity = _vector(self.coupler_capacity, self.layer_count, "coupler_capacity")
            coupler = np.broadcast_to(coupler_capacity, exposure.shape).copy()
            dye = np.zeros_like(halide, dtype=np.float32)
        return FilmProcessState(
            halide=halide,
            developability=developability.astype(np.float32),
            metallic_silver=silver,
            coupler=coupler,
            dye=dye,
            original_developability=developability.astype(np.float32),
        )


@dataclass(slots=True)
class FilmProcessState:
    """Mutable reduced material pools while a process program is running."""

    halide: np.ndarray
    developability: np.ndarray
    metallic_silver: np.ndarray
    coupler: np.ndarray | None = None
    dye: np.ndarray | None = None
    bleached_halide: np.ndarray | None = None
    original_developability: np.ndarray | None = None
    auxiliary_remaining: float = 1.0

    def copy(self) -> "FilmProcessState":
        return FilmProcessState(
            halide=np.array(self.halide, dtype=np.float32, copy=True),
            developability=np.array(self.developability, dtype=np.float32, copy=True),
            metallic_silver=np.array(self.metallic_silver, dtype=np.float32, copy=True),
            coupler=None if self.coupler is None else np.array(self.coupler, dtype=np.float32, copy=True),
            dye=None if self.dye is None else np.array(self.dye, dtype=np.float32, copy=True),
            bleached_halide=(
                None
                if self.bleached_halide is None
                else np.array(self.bleached_halide, dtype=np.float32, copy=True)
            ),
            original_developability=(
                None
                if self.original_developability is None
                else np.array(self.original_developability, dtype=np.float32, copy=True)
            ),
            auxiliary_remaining=float(self.auxiliary_remaining),
        )

    def validate(self) -> None:
        shape = np.asarray(self.halide).shape
        if not shape:
            raise ValueError("film process pools must have at least one dimension")
        for name in ("halide", "developability", "metallic_silver"):
            value = np.asarray(getattr(self, name), dtype=np.float32)
            if value.shape != shape:
                raise ValueError(f"{name} shape {value.shape} does not match {shape}")
            if not np.all(np.isfinite(value)) or np.any(value < -1e-6):
                raise ValueError(f"{name} contains invalid pool values")
        if np.any(self.developability > 1.0 + 1e-6):
            raise ValueError("developability must stay within [0, 1]")
        for name in ("coupler", "dye", "bleached_halide"):
            raw = getattr(self, name)
            if raw is None:
                continue
            value = np.asarray(raw, dtype=np.float32)
            if value.shape != shape:
                raise ValueError(f"{name} shape {value.shape} does not match {shape}")
            if not np.all(np.isfinite(value)) or np.any(value < -1e-6):
                raise ValueError(f"{name} contains invalid pool values")
        if not 0.0 <= float(self.auxiliary_remaining) <= 1.0:
            raise ValueError("auxiliary_remaining must stay within [0, 1]")

    def developable_halide(self) -> np.ndarray:
        """Return the derived latent-activated halide view ``H * e``.

        This is intentionally not stored as a second mutable material pool.
        Keeping one halide pool plus a continuous activation field preserves
        halide conservation throughout development and reversal activation.
        """
        return (
            np.asarray(self.halide, dtype=np.float32)
            * np.clip(np.asarray(self.developability, dtype=np.float32), 0.0, 1.0)
        ).astype(np.float32)

    def inactive_halide(self) -> np.ndarray:
        """Return the derived not-yet-activated halide view ``H * (1 - e)``."""
        return (
            np.asarray(self.halide, dtype=np.float32)
            * (1.0 - np.clip(np.asarray(self.developability, dtype=np.float32), 0.0, 1.0))
        ).astype(np.float32)

    def totals(self) -> dict[str, float]:
        halide_total = float(np.sum(self.halide, dtype=np.float64))
        # A scalar einsum avoids materializing both H*e and H*(1-e) merely to
        # write diagnostic totals for a potentially huge frame.
        halide_flat = np.asarray(self.halide, dtype=np.float32).reshape(-1)
        developability_flat = np.asarray(
            self.developability, dtype=np.float32
        ).reshape(-1)
        developable_total = 0.0
        chunk_elements = 1_048_576
        for start in range(0, halide_flat.size, chunk_elements):
            stop = min(start + chunk_elements, halide_flat.size)
            developable_total += float(
                np.sum(
                    halide_flat[start:stop] * developability_flat[start:stop],
                    dtype=np.float64,
                )
            )
        return {
            "halide": halide_total,
            "developable_halide": developable_total,
            "inactive_halide": halide_total - developable_total,
            "metallic_silver": float(np.sum(self.metallic_silver, dtype=np.float64)),
            "coupler": 0.0 if self.coupler is None else float(np.sum(self.coupler, dtype=np.float64)),
            "dye": 0.0 if self.dye is None else float(np.sum(self.dye, dtype=np.float64)),
            "bleached_halide": (
                0.0
                if self.bleached_halide is None
                else float(np.sum(self.bleached_halide, dtype=np.float64))
            ),
            "auxiliary_remaining": float(self.auxiliary_remaining),
        }


@dataclass(frozen=True, slots=True)
class FilmFinalMedium:
    """Immutable residual material observed by a scanner or virtual light table."""

    material_key: str
    metallic_silver: np.ndarray
    dye: np.ndarray | None
    residual_halide: np.ndarray
    bleached_halide: np.ndarray | None
    dye_absorption_matrix: tuple[tuple[float, ...], ...] | None
    silver_density_per_layer: tuple[float, ...]
    residual_halide_density_per_layer: tuple[float, ...]
    base_density_rgb: tuple[float, float, float]
    auxiliary_density_rgb: tuple[float, float, float]
    auxiliary_remaining: float
    image_polarity: str
    view_mode: str
    compatible_interpreters: tuple[str, ...]
    process_key: str
    retained_halide_density_rgb: tuple[float, float, float] = (0.62, 0.82, 1.0)

    def total_fixable_halide(self) -> np.ndarray:
        """Return all silver-salt material that a complete fixer would remove."""
        residual = np.asarray(self.residual_halide, dtype=np.float32)
        if self.bleached_halide is None:
            return residual
        bleached = np.asarray(self.bleached_halide, dtype=np.float32)
        if bleached.shape != residual.shape:
            raise ValueError("bleached_halide shape must match residual_halide")
        return (residual + bleached).astype(np.float32)

    def optical_density_rgb(self) -> np.ndarray:
        silver = np.asarray(self.metallic_silver, dtype=np.float32)
        residual = self.total_fixable_halide()
        layers = silver.shape[-1]
        if residual.shape != silver.shape:
            raise ValueError("residual_halide shape must match metallic_silver")
        prefix_shape = silver.shape[:-1]
        density = np.zeros((*prefix_shape, 3), dtype=np.float32)
        if self.dye is not None:
            dye = np.asarray(self.dye, dtype=np.float32)
            if dye.shape != silver.shape or self.dye_absorption_matrix is None:
                raise ValueError("dye pools require a matching absorption matrix")
            absorption = _matrix(self.dye_absorption_matrix, 3, layers, "dye_absorption_matrix")
            density += np.einsum("...l,rl->...r", dye, absorption)
        silver_scale = _vector(self.silver_density_per_layer, layers, "silver_density_per_layer")
        silver_density = np.einsum("...l,l->...", silver, silver_scale)[..., None]
        density += silver_density
        halide_scale = _vector(
            self.residual_halide_density_per_layer,
            layers,
            "residual_halide_density_per_layer",
        )
        halide_density = np.einsum("...l,l->...", residual, halide_scale)[..., None]
        halide_color = _vector(
            self.retained_halide_density_rgb,
            3,
            "retained_halide_density_rgb",
        )
        density += halide_density * halide_color
        density += _vector(self.base_density_rgb, 3, "base_density_rgb")
        density += _vector(self.auxiliary_density_rgb, 3, "auxiliary_density_rgb") * float(
            self.auxiliary_remaining
        )
        return np.clip(density, 0.0, None).astype(np.float32)

    def transmittance_rgb(self) -> np.ndarray:
        return np.power(10.0, -self.optical_density_rgb()).astype(np.float32)

    def contract(self) -> dict[str, object]:
        return {
            "material_key": self.material_key,
            "process_key": self.process_key,
            "image_polarity": self.image_polarity,
            "view_mode": self.view_mode,
            "compatible_interpreters": list(self.compatible_interpreters),
            "components": {
                "metallic_silver": bool(np.any(self.metallic_silver > 1e-6)),
                "dye": bool(self.dye is not None and np.any(self.dye > 1e-6)),
                "residual_halide": bool(np.any(self.residual_halide > 1e-6)),
                "bleached_halide": bool(
                    self.bleached_halide is not None
                    and np.any(self.bleached_halide > 1e-6)
                ),
                "auxiliary_remaining": float(self.auxiliary_remaining),
            },
            "optical_observation": {
                "dye_absorption_matrix": (
                    None
                    if self.dye_absorption_matrix is None
                    else [list(row) for row in self.dye_absorption_matrix]
                ),
                "silver_density_per_layer": list(self.silver_density_per_layer),
                "residual_halide_density_per_layer": list(
                    self.residual_halide_density_per_layer
                ),
                "retained_halide_density_rgb": list(self.retained_halide_density_rgb),
                "base_density_rgb": list(self.base_density_rgb),
                "auxiliary_density_rgb": list(self.auxiliary_density_rgb),
                "auxiliary_remaining": float(self.auxiliary_remaining),
            },
        }
