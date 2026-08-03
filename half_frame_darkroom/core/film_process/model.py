"""Material, latent-state, and final-medium objects for reduced film processes."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from half_frame_darkroom.core.execution_topology import reference_execution_enabled


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


def compose_optical_density_rgb(
    *,
    dye_density_layers: np.ndarray | None = None,
    dye_absorption_matrix: np.ndarray | tuple[tuple[float, ...], ...] | None = None,
    silver_density_rgb: np.ndarray | None = None,
    residual_halide_density_rgb: np.ndarray | None = None,
    bleached_halide_density_rgb: np.ndarray | None = None,
    base_density_rgb: np.ndarray | tuple[float, float, float] = (0.0, 0.0, 0.0),
    auxiliary_density_rgb: np.ndarray | None = None,
    consume_base_density: bool = False,
) -> np.ndarray:
    """Compose the derived scanner master from component optical densities.

    Dye remains layer-authored and is observed through its absorption matrix.
    Silver, silver salts, support/mask, and auxiliary material enter directly
    as RGB optical-density contributions. This function performs no scanner
    capture or interpretation. Inputs remain read-only by default;
    ``consume_base_density=True`` may reuse an exactly shaped, writable,
    caller-owned float32 base buffer.
    """
    base = np.asarray(base_density_rgb, dtype=np.float32)
    if base.shape != (3,) and (base.ndim < 1 or base.shape[-1] != 3):
        raise ValueError("base_density_rgb must end with three RGB channels")

    components = (
        silver_density_rgb,
        residual_halide_density_rgb,
        bleached_halide_density_rgb,
        auxiliary_density_rgb,
    )
    dye_density_rgb: np.ndarray | None = None
    if dye_density_layers is not None:
        dye_layers = np.asarray(dye_density_layers, dtype=np.float32)
        if dye_layers.ndim < 1:
            raise ValueError("dye_density_layers must have a layer axis")
        if dye_absorption_matrix is None:
            raise ValueError("dye density requires dye_absorption_matrix")
        absorption = _matrix(
            dye_absorption_matrix,
            3,
            dye_layers.shape[-1],
            "dye_absorption_matrix",
        )
        dye_density_rgb = np.einsum(
            "...l,rl->...r", dye_layers, absorption
        ).astype(np.float32, copy=False)

    values: list[tuple[str, np.ndarray]] = []
    if dye_density_rgb is not None:
        values.append(("dye_density_rgb", dye_density_rgb))
    for name, component in zip(
        (
            "silver_density_rgb",
            "residual_halide_density_rgb",
            "bleached_halide_density_rgb",
            "auxiliary_density_rgb",
        ),
        components,
        strict=True,
    ):
        if component is None:
            continue
        value = np.asarray(component, dtype=np.float32)
        if value.ndim < 1 or value.shape[-1] != 3:
            raise ValueError(f"{name} must end with three RGB channels")
        values.append((name, value))

    try:
        target_shape = np.broadcast_shapes(base.shape, *(value.shape for _, value in values))
    except ValueError as exc:
        shapes = {name: value.shape for name, value in values}
        raise ValueError(
            f"optical-density component shapes are not broadcast-compatible: base={base.shape}, {shapes}"
        ) from exc
    can_consume_base = bool(
        consume_base_density
        and base.dtype == np.float32
        and base.flags.writeable
        and base.shape == target_shape
    )
    density = base if can_consume_base else np.broadcast_to(base, target_shape).copy()
    for name, value in values:
        try:
            density += value
        except ValueError as exc:
            raise ValueError(
                f"{name} shape {value.shape} is not broadcastable to optical master {density.shape}"
            ) from exc
    # ``density`` is a newly composed derived master, never an input view.
    # Scalar reductions retain complete finite validation without allocating
    # a full boolean image, then the lower bound can be applied in place.
    minimum = float(np.min(density))
    maximum = float(np.max(density))
    if not np.isfinite(minimum) or not np.isfinite(maximum):
        raise ValueError("optical-density components must be finite")
    np.maximum(density, 0.0, out=density)
    return density.astype(np.float32, copy=False)


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
    clear_support_density_rgb: tuple[float, float, float] | None = None
    masking_coupler_density_rgb: tuple[float, float, float] | None = None
    auxiliary_density_rgb: tuple[float, float, float] = (0.0, 0.0, 0.0)
    medium_family: str = "film"
    color_system: str = "silver_halide"
    # Retained silver halide is not treated as metallic silver. This fallback
    # approximates a blue-density rise; normal construction supplies the
    # material-specific value from FilmStockConfig.
    retained_halide_density_rgb: tuple[float, float, float] = (0.62, 0.82, 1.0)
    # Bleached silver salts are a distinct pool.  Defaults intentionally reuse
    # the residual-halide optics for backward-compatible materials, while a
    # material may supply separate coefficients when that distinction matters.
    bleached_halide_density_per_layer: tuple[float, ...] | None = None
    bleached_halide_density_rgb: tuple[float, float, float] | None = None
    # Material-only latent-response coefficients.  They deliberately exclude
    # developer time, temperature, concentration, exhaustion, and push/pull;
    # those belong to process-operator kinetics after exposure has frozen the
    # latent state.  ``None`` keeps the generic half-saturation fallback used
    # by small standalone model tests and third-party callers.
    latent_gamma: tuple[float, ...] | None = None
    latent_density_range: tuple[float, ...] | None = None
    latent_log_exposure_toe: tuple[float, ...] | None = None
    latent_log_exposure_shoulder: tuple[float, ...] | None = None
    latent_toe_width: float = 0.18
    latent_shoulder_width: float = 0.22
    latent_extreme_reversal_strength: float = 0.0
    latent_extreme_reversal_start_loge: tuple[float, ...] | None = None
    latent_extreme_reversal_width: float = 0.18

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
        if self.clear_support_density_rgb is not None:
            _vector(self.clear_support_density_rgb, 3, "clear_support_density_rgb")
        if self.masking_coupler_density_rgb is not None:
            _vector(self.masking_coupler_density_rgb, 3, "masking_coupler_density_rgb")
        _vector(self.auxiliary_density_rgb, 3, "auxiliary_density_rgb")
        _vector(self.retained_halide_density_rgb, 3, "retained_halide_density_rgb")
        if self.bleached_halide_density_per_layer is not None:
            _vector(
                self.bleached_halide_density_per_layer,
                layers,
                "bleached_halide_density_per_layer",
            )
        if self.bleached_halide_density_rgb is not None:
            _vector(self.bleached_halide_density_rgb, 3, "bleached_halide_density_rgb")
        latent_vectors = (
            self.latent_gamma,
            self.latent_density_range,
            self.latent_log_exposure_toe,
            self.latent_log_exposure_shoulder,
        )
        if any(value is not None for value in latent_vectors):
            if not all(value is not None for value in latent_vectors):
                raise ValueError("material latent-response coefficients must be supplied together")
            _vector(self.latent_gamma, layers, "latent_gamma")
            density_range = _vector(
                self.latent_density_range,
                layers,
                "latent_density_range",
            )
            if np.any(density_range <= 0.0):
                raise ValueError("latent_density_range values must be positive")
            _vector(self.latent_log_exposure_toe, layers, "latent_log_exposure_toe")
            _vector(
                self.latent_log_exposure_shoulder,
                layers,
                "latent_log_exposure_shoulder",
            )
            if float(self.latent_toe_width) <= 0.0 or float(self.latent_shoulder_width) <= 0.0:
                raise ValueError("latent curve widths must be positive")
        reversal_strength = float(self.latent_extreme_reversal_strength)
        if not 0.0 <= reversal_strength <= 1.0:
            raise ValueError("latent extreme-reversal strength must be between zero and one")
        if float(self.latent_extreme_reversal_width) <= 0.0:
            raise ValueError("latent extreme-reversal width must be positive")
        if reversal_strength > 0.0:
            if self.latent_extreme_reversal_start_loge is None:
                raise ValueError(
                    "enabled latent extreme reversal requires a start log exposure"
                )
            _vector(
                self.latent_extreme_reversal_start_loge,
                layers,
                "latent_extreme_reversal_start_loge",
            )

    def expose(
        self,
        layer_exposure: np.ndarray,
        half_saturation: float = 0.18,
        *,
        preserve_original_developability: bool = True,
        consume_layer_exposure: bool = False,
    ) -> "FilmProcessState":
        """Create a latent state using one continuous developability field.

        ``developability`` is the reduced-order equivalent of the exposed
        halide fraction.  Unexposed halide remains implicit as
        ``halide * (1 - developability)`` until an activation operator changes
        it, avoiding a duplicate full-resolution pool.
        """
        source_exposure = np.asarray(layer_exposure, dtype=np.float32)
        can_consume = bool(
            consume_layer_exposure
            and not reference_execution_enabled()
            and source_exposure.dtype == np.float32
            and source_exposure.flags.writeable
        )
        if can_consume:
            exposure = source_exposure
            np.maximum(exposure, 0.0, out=exposure)
        else:
            exposure = np.clip(source_exposure, 0.0, None)
        if exposure.ndim < 1 or exposure.shape[-1] != self.layer_count:
            raise ValueError(
                f"layer_exposure must end with {self.layer_count} layers, got {exposure.shape}"
            )
        state_shape = exposure.shape
        if self.latent_gamma is None:
            half = max(float(half_saturation), 1e-6)
            denominator = exposure.copy()
            denominator += half
            np.divide(exposure, denominator, out=exposure)
            developability = exposure
            # ``developability`` owns the consumed exposure buffer now.  The
            # denominator has no pool-stage consumer.
            del denominator, exposure
        else:
            # ``exposure`` is now always a private or explicitly consumed
            # work field. Reuse it for log exposure rather than keeping clip,
            # logarithm, and source arrays alive together.
            np.maximum(exposure, 1e-6, out=exposure)
            np.log10(exposure, out=exposure)
            log_e = exposure
            toe = _vector(
                self.latent_log_exposure_toe,
                self.layer_count,
                "latent_log_exposure_toe",
            )
            shoulder = _vector(
                self.latent_log_exposure_shoulder,
                self.layer_count,
                "latent_log_exposure_shoulder",
            )
            gamma = _vector(self.latent_gamma, self.layer_count, "latent_gamma")
            density_range = _vector(
                self.latent_density_range,
                self.layer_count,
                "latent_density_range",
            )

            def softplus(value: np.ndarray, width: float) -> np.ndarray:
                width = max(float(width), 1e-6)
                value /= width
                np.clip(value, -60.0, 60.0, out=value)
                np.exp(value, out=value)
                np.log1p(value, out=value)
                value *= width
                return value

            toe_term = np.subtract(log_e, toe, dtype=np.float32)
            toe_term = softplus(toe_term, self.latent_toe_width)
            reversal_strength = float(self.latent_extreme_reversal_strength)
            if reversal_strength > 0.0 or reference_execution_enabled():
                # The research tail still needs the original log-exposure
                # field after the ordinary curve has formed. Developer
                # reference topology also retains the historical independent
                # shoulder field for same-version allocation A/B.
                shoulder_term = np.subtract(log_e, shoulder, dtype=np.float32)
            else:
                # Ordinary materials have no later logE consumer. Reuse that
                # private field for the shoulder softplus rather than keeping
                # two full layer arrays with identical lifetimes.
                shoulder_term = log_e
                np.subtract(log_e, shoulder, out=shoulder_term)
            shoulder_term = softplus(shoulder_term, self.latent_shoulder_width)
            toe_term -= shoulder_term
            toe_term *= gamma
            toe_term /= density_range
            np.clip(toe_term, 0.0, 1.0, out=toe_term)
            developability = toe_term
            if reversal_strength > 0.0:
                # The research reversal tail reuses both softplus buffers;
                # keep the already completed ordinary curve in its own field.
                developability = developability.copy()
                reversal_start = _vector(
                    self.latent_extreme_reversal_start_loge,
                    self.layer_count,
                    "latent_extreme_reversal_start_loge",
                )
                reversal_width = max(float(self.latent_extreme_reversal_width), 1e-6)
                # C1-continuous smoothstep: value and first derivative both
                # join the ordinary shoulder without a synthetic hard kink.
                # Reuse the completed toe/shoulder work buffers so enabling
                # this research tail does not retain another full layer field.
                np.subtract(log_e, reversal_start, out=toe_term)
                np.divide(toe_term, reversal_width, out=toe_term)
                np.clip(toe_term, 0.0, 1.0, out=toe_term)
                np.multiply(toe_term, -2.0, out=shoulder_term)
                shoulder_term += 3.0
                np.multiply(toe_term, toe_term, out=toe_term)
                np.multiply(toe_term, shoulder_term, out=toe_term)
                np.multiply(toe_term, reversal_strength, out=shoulder_term)
                np.subtract(1.0, shoulder_term, out=shoulder_term)
                np.multiply(developability, shoulder_term, out=developability)
                np.clip(developability, 0.0, 1.0, out=developability)
            # The curve result is retained through ``developability``.  Log
            # exposure and both toe/shoulder work references have completed
            # before the four material pools are allocated, so do not keep
            # them alive across that peak.
            del toe_term, shoulder_term, log_e, exposure
        capacity = _vector(self.halide_capacity, self.layer_count, "halide_capacity")
        halide = np.broadcast_to(capacity, state_shape).copy()
        silver = np.zeros_like(halide, dtype=np.float32)
        if self.coupler_capacity is None:
            coupler = None
            dye = None
        else:
            coupler_capacity = _vector(self.coupler_capacity, self.layer_count, "coupler_capacity")
            coupler = np.broadcast_to(coupler_capacity, state_shape).copy()
            dye = np.zeros_like(halide, dtype=np.float32)
        # ``developability`` is already a freshly produced float32 field on
        # the material path.  Reuse it as the live state buffer; only the
        # optional immutable exposure audit needs its own copy.  The previous
        # two unconditional ``astype`` calls copied the same full-resolution
        # field twice even when the audit was disabled.
        developability = np.asarray(developability, dtype=np.float32)
        return FilmProcessState(
            halide=halide,
            developability=developability,
            metallic_silver=silver,
            coupler=coupler,
            dye=dye,
            original_developability=(
                np.array(developability, dtype=np.float32, copy=True)
                if preserve_original_developability
                else None
            ),
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
    masking_coupler_remaining: float = 1.0

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
            masking_coupler_remaining=float(self.masking_coupler_remaining),
        )

    def validate(self) -> None:
        shape = np.asarray(self.halide).shape
        if not shape or any(int(length) <= 0 for length in shape):
            raise ValueError("film process pools must have non-empty dimensions")
        for name in ("halide", "developability", "metallic_silver"):
            value = np.asarray(getattr(self, name), dtype=np.float32)
            if value.shape != shape:
                raise ValueError(f"{name} shape {value.shape} does not match {shape}")
            # Min/max propagate NaN and expose both signed infinities. Two
            # scalar reductions therefore enforce the same full-array finite
            # and range contract without allocating and reducing a separate
            # boolean image for every pool after every process step.
            minimum = float(np.min(value))
            maximum = float(np.max(value))
            valid = (
                np.isfinite(minimum)
                and np.isfinite(maximum)
                and minimum >= -1e-6
            )
            if name == "developability":
                valid = valid and maximum <= 1.0 + 1e-6
            if not valid:
                raise ValueError(f"{name} contains invalid pool values")
        for name in ("coupler", "dye", "bleached_halide"):
            raw = getattr(self, name)
            if raw is None:
                continue
            value = np.asarray(raw, dtype=np.float32)
            if value.shape != shape:
                raise ValueError(f"{name} shape {value.shape} does not match {shape}")
            minimum = float(np.min(value))
            maximum = float(np.max(value))
            if not (
                np.isfinite(minimum)
                and np.isfinite(maximum)
                and minimum >= -1e-6
            ):
                raise ValueError(f"{name} contains invalid pool values")
        if not 0.0 <= float(self.auxiliary_remaining) <= 1.0:
            raise ValueError("auxiliary_remaining must stay within [0, 1]")
        if not 0.0 <= float(self.masking_coupler_remaining) <= 1.0:
            raise ValueError("masking_coupler_remaining must stay within [0, 1]")

    def developable_halide(self) -> np.ndarray:
        """Return the derived latent-activated halide view ``H * e``.

        This is intentionally not stored as a second mutable material pool.
        Keeping one halide pool plus a continuous activation field preserves
        halide conservation throughout development and reversal activation.
        """
        return (
            np.asarray(self.halide, dtype=np.float32)
            * np.clip(np.asarray(self.developability, dtype=np.float32), 0.0, 1.0)
        ).astype(np.float32, copy=False)

    def inactive_halide(self) -> np.ndarray:
        """Return the derived not-yet-activated halide view ``H * (1 - e)``."""
        return (
            np.asarray(self.halide, dtype=np.float32)
            * (1.0 - np.clip(np.asarray(self.developability, dtype=np.float32), 0.0, 1.0))
        ).astype(np.float32, copy=False)

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
            "masking_coupler_remaining": float(self.masking_coupler_remaining),
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
    clear_support_density_rgb: tuple[float, float, float] | None
    masking_coupler_density_rgb: tuple[float, float, float] | None
    masking_coupler_remaining: float
    auxiliary_density_rgb: tuple[float, float, float]
    auxiliary_remaining: float
    image_polarity: str
    view_mode: str
    compatible_interpreters: tuple[str, ...]
    process_key: str
    retained_halide_density_rgb: tuple[float, float, float] = (0.62, 0.82, 1.0)
    bleached_halide_density_per_layer: tuple[float, ...] | None = None
    bleached_halide_density_rgb: tuple[float, float, float] | None = None

    def __post_init__(self) -> None:
        # frozen=True does not make NumPy buffers immutable. Finalization owns
        # copied pool arrays, so mark those buffers read-only as well and keep
        # the material state safe from scanner/export side effects.
        for name in ("metallic_silver", "dye", "residual_halide", "bleached_halide"):
            value = getattr(self, name)
            if value is None:
                continue
            array = np.asarray(value, dtype=np.float32)
            array.setflags(write=False)
            object.__setattr__(self, name, array)

    def total_fixable_halide(self) -> np.ndarray:
        """Return all silver-salt material that a complete fixer would remove."""
        residual = np.asarray(self.residual_halide, dtype=np.float32)
        if self.bleached_halide is None:
            return residual
        bleached = np.asarray(self.bleached_halide, dtype=np.float32)
        if bleached.shape != residual.shape:
            raise ValueError("bleached_halide shape must match residual_halide")
        return (residual + bleached).astype(np.float32, copy=False)

    def mean_total_fixable_halide(self) -> np.ndarray:
        """Return the layer mean without materializing a combined RGB/layer pool.

        Film Foundry's reduced materials use one or three layers. For those
        shapes, the additions deliberately follow the same left-to-right
        float32 order as ``mean(residual + bleached, axis=-1)``. Unusual
        third-party layer counts retain the public combined-pool fallback.
        """

        residual = np.asarray(self.residual_halide, dtype=np.float32)
        if residual.ndim < 1 or residual.shape[-1] <= 0:
            raise ValueError("residual_halide must have a non-empty layer axis")
        if self.bleached_halide is None:
            return np.mean(residual, axis=-1, dtype=np.float32)
        bleached = np.asarray(self.bleached_halide, dtype=np.float32)
        if bleached.shape != residual.shape:
            raise ValueError("bleached_halide shape must match residual_halide")
        layer_count = int(residual.shape[-1])
        if layer_count not in {1, 3}:
            return np.mean(
                self.total_fixable_halide(),
                axis=-1,
                dtype=np.float32,
            )

        mean = np.add(
            residual[..., 0],
            bleached[..., 0],
            dtype=np.float32,
        )
        if layer_count == 1:
            return mean
        work = np.empty_like(mean, dtype=np.float32)
        for layer in range(1, layer_count):
            np.add(
                residual[..., layer],
                bleached[..., layer],
                out=work,
            )
            mean += work
        mean /= np.float32(layer_count)
        return mean

    def optical_density_rgb(self) -> np.ndarray:
        silver = np.asarray(self.metallic_silver, dtype=np.float32)
        residual = np.asarray(self.residual_halide, dtype=np.float32)
        bleached = (
            None
            if self.bleached_halide is None
            else np.asarray(self.bleached_halide, dtype=np.float32)
        )
        layers = silver.shape[-1]
        if residual.shape != silver.shape or (
            bleached is not None and bleached.shape != silver.shape
        ):
            raise ValueError("silver-salt pool shapes must match metallic_silver")
        dye_density_layers: np.ndarray | None = None
        if self.dye is not None:
            dye = np.asarray(self.dye, dtype=np.float32)
            if dye.shape != silver.shape or self.dye_absorption_matrix is None:
                raise ValueError("dye pools require a matching absorption matrix")
            _matrix(self.dye_absorption_matrix, 3, layers, "dye_absorption_matrix")
            dye_density_layers = dye
        silver_scale = _vector(self.silver_density_per_layer, layers, "silver_density_per_layer")
        silver_density = np.einsum("...l,l->...", silver, silver_scale)
        silver_density_rgb = np.broadcast_to(
            silver_density[..., None],
            (*silver_density.shape, 3),
        )
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
        residual_density_rgb = halide_density * halide_color
        bleached_density_rgb: np.ndarray | None = None
        if bleached is not None:
            bleached_scale = _vector(
                self.bleached_halide_density_per_layer
                if self.bleached_halide_density_per_layer is not None
                else self.residual_halide_density_per_layer,
                layers,
                "bleached_halide_density_per_layer",
            )
            bleached_density = np.einsum(
                "...l,l->...", bleached, bleached_scale
            )[..., None]
            bleached_color = _vector(
                self.bleached_halide_density_rgb
                if self.bleached_halide_density_rgb is not None
                else self.retained_halide_density_rgb,
                3,
                "bleached_halide_density_rgb",
            )
            bleached_density_rgb = bleached_density * bleached_color
        auxiliary_density_rgb = _vector(
            self.auxiliary_density_rgb, 3, "auxiliary_density_rgb"
        ) * float(self.auxiliary_remaining)
        return compose_optical_density_rgb(
            dye_density_layers=dye_density_layers,
            dye_absorption_matrix=(
                None if dye_density_layers is None else self.dye_absorption_matrix
            ),
            silver_density_rgb=silver_density_rgb,
            residual_halide_density_rgb=residual_density_rgb,
            bleached_halide_density_rgb=bleached_density_rgb,
            base_density_rgb=_vector(self.base_density_rgb, 3, "base_density_rgb"),
            auxiliary_density_rgb=auxiliary_density_rgb,
        )

    def transmittance_rgb(self) -> np.ndarray:
        return np.power(10.0, -self.optical_density_rgb()).astype(
            np.float32,
            copy=False,
        )

    def contract(self) -> dict[str, object]:
        return {
            "material_key": self.material_key,
            "process_key": self.process_key,
            "image_polarity": self.image_polarity,
            "view_mode": self.view_mode,
            "compatible_interpreters": list(self.compatible_interpreters),
            "components": {
                "metallic_silver": bool(float(np.max(self.metallic_silver)) > 1e-6),
                "dye": bool(
                    self.dye is not None and float(np.max(self.dye)) > 1e-6
                ),
                "residual_halide": bool(
                    float(np.max(self.residual_halide)) > 1e-6
                ),
                "bleached_halide": bool(
                    self.bleached_halide is not None
                    and float(np.max(self.bleached_halide)) > 1e-6
                ),
                "auxiliary_remaining": float(self.auxiliary_remaining),
                "masking_coupler_remaining": float(self.masking_coupler_remaining),
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
                "bleached_halide_density_per_layer": list(
                    self.bleached_halide_density_per_layer
                    if self.bleached_halide_density_per_layer is not None
                    else self.residual_halide_density_per_layer
                ),
                "bleached_halide_density_rgb": list(
                    self.bleached_halide_density_rgb
                    if self.bleached_halide_density_rgb is not None
                    else self.retained_halide_density_rgb
                ),
                "base_density_rgb": list(self.base_density_rgb),
                "clear_support_density_rgb": (
                    None
                    if self.clear_support_density_rgb is None
                    else list(self.clear_support_density_rgb)
                ),
                "masking_coupler_density_rgb": (
                    None
                    if self.masking_coupler_density_rgb is None
                    else list(self.masking_coupler_density_rgb)
                ),
                "masking_coupler_remaining": float(self.masking_coupler_remaining),
                "auxiliary_density_rgb": list(self.auxiliary_density_rgb),
                "auxiliary_remaining": float(self.auxiliary_remaining),
            },
        }
