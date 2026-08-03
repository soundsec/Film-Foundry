"""Gradual pixel-path integrations for the reduced film-process framework."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from half_frame_darkroom.core.development import EffectiveDevelopmentState, build_effective_development
from half_frame_darkroom.core.derived_cache import pseudoinverse_3x3
from half_frame_darkroom.core.execution_topology import reference_execution_enabled
from half_frame_darkroom.core.film_process.model import (
    FilmProcessState,
    ReducedFilmMaterial,
    compose_optical_density_rgb,
)
from half_frame_darkroom.core.film_process.operators import (
    CompatibilityProfile,
    FilmProcessResult,
    apply_process_program,
)
from half_frame_darkroom.core.film_process.recipe import program_from_develop_recipe
from half_frame_darkroom.core.sensitometry import rgb_exposure_to_layer_exposure
from half_frame_darkroom.core.spatial_fields import (
    LAZY_LAYER_EXPOSURE_FIELD_TYPES,
    LayerExposureAdditionField,
)
from half_frame_darkroom.core.scanner import layer_density_to_optical_density_rgb
from half_frame_darkroom.model.config import DevelopRecipeConfig, FilmStockConfig


_ZERO_SCALAR_DENSITY = np.zeros((1, 1), dtype=np.float32)
_ZERO_SCALAR_DENSITY.setflags(write=False)
_ZERO_RGB_DENSITY = np.zeros((1, 1, 3), dtype=np.float32)
_ZERO_RGB_DENSITY.setflags(write=False)


def _pool_has_material(
    values: np.ndarray | None,
    *,
    known_total: float | None = None,
) -> bool:
    """Return true only for a strictly non-zero, non-negative material pool."""
    if known_total is not None and not reference_execution_enabled():
        # Production compaction has already validated every pool and reduced
        # this exact final state in float64.  Non-negative material means its
        # sum is positive iff at least one element is positive, so a second
        # full-frame max reduction cannot add information.
        return float(known_total) > 0.0
    return values is not None and float(np.max(values)) > 0.0


def _zero_scalar_density(shape: tuple[int, int]) -> np.ndarray:
    return np.broadcast_to(_ZERO_SCALAR_DENSITY, shape)


def _zero_rgb_density(shape: tuple[int, int]) -> np.ndarray:
    return np.broadcast_to(_ZERO_RGB_DENSITY, (*shape, 3))


def _compact_consumed_process_state(
    result: FilmProcessResult,
) -> dict[str, float]:
    """Freeze totals, then release formation-only state fields."""
    totals = result.state.totals()
    layer_count = int(result.state.halide.shape[-1])
    result.state.developability = np.empty((0, 0, layer_count), dtype=np.float32)
    result.state.coupler = None
    result.state.original_developability = None
    return totals


@dataclass(frozen=True, slots=True)
class ReducedBwDevelopment:
    density_rgb: np.ndarray
    optical_density_rgb: np.ndarray
    clear_base_optical_density_rgb: np.ndarray
    process_result: FilmProcessResult
    effective_development: EffectiveDevelopmentState
    latent_fraction: np.ndarray
    compatibility: CompatibilityProfile
    process_totals: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class ReducedColorDevelopment:
    density_cmy: np.ndarray
    optical_density_rgb: np.ndarray
    clear_base_optical_density_rgb: np.ndarray
    process_result: FilmProcessResult
    effective_development: EffectiveDevelopmentState
    latent_fraction: np.ndarray
    # Metallic image silver is neutral broad-band density.  Store its one
    # independent scalar degree of freedom; the compatibility RGB attribute
    # below is a zero-copy broadcast view rather than three duplicate planes.
    silver_density: np.ndarray
    residual_halide_density_rgb: np.ndarray
    bleached_halide_density_rgb: np.ndarray
    compatibility: CompatibilityProfile
    process_totals: dict[str, float] | None = None

    @property
    def silver_density_rgb(self) -> np.ndarray:
        values = np.asarray(self.silver_density, dtype=np.float32)
        return np.broadcast_to(values[..., None], (*values.shape, 3))


def _base_and_mask_density(
    film: FilmStockConfig,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Split the legacy total base density into support and coloured mask.

    Only native colour-negative material carries an integral orange mask in
    this reduced model.  Reversal and monochrome stocks keep their complete
    base density in the clear-support component.
    """
    total = np.clip(np.asarray(film.film_base_density_rgb, dtype=np.float32), 0.0, None)
    native_negative = str(film.image_polarity).strip().lower() == "negative"
    if _is_monochrome_material(film) or not native_negative:
        return tuple(float(value) for value in total), (0.0, 0.0, 0.0)
    clear = np.minimum(
        total,
        np.clip(np.asarray(film.clear_support_density_rgb, dtype=np.float32), 0.0, None),
    )
    mask = np.maximum(total - clear, 0.0)
    return tuple(float(value) for value in clear), tuple(float(value) for value in mask)


def _is_monochrome_material(film: FilmStockConfig) -> bool:
    color_process = str(film.color_process).strip().lower().replace("-", "_").replace(" ", "_")
    return color_process in {"bw", "black_white", "monochrome"}


def _material_degradation_state(
    film: FilmStockConfig,
) -> tuple[float, float, np.ndarray, np.ndarray]:
    """Return bounded ageing severity, speed, layer balance and fog RGB.

    The model intentionally collapses storage history, age and mishandling into
    one severity while leaving the stock-specific response editable. It follows
    the well-established reduced tendencies: speed loss, higher D-min/fog,
    colour-layer imbalance and greater granularity.
    """
    severity = float(np.clip(getattr(film, "material_degradation", 0.0), 0.0, 1.0))
    speed_loss_stops = float(
        np.clip(getattr(film, "degradation_speed_loss_stops", 0.65), 0.0, 4.0)
    )
    speed_factor = float(np.exp2(-severity * speed_loss_stops))
    target_balance = np.asarray(
        getattr(film, "degradation_layer_balance", (0.90, 0.95, 1.0)),
        dtype=np.float32,
    ).reshape(3)
    target_balance = np.clip(target_balance, 0.0, 2.0)
    layer_balance = 1.0 + severity * (target_balance - 1.0)
    fog_rgb = severity * np.clip(
        np.asarray(
            getattr(film, "degradation_fog_density_rgb", (0.10, 0.12, 0.14)),
            dtype=np.float32,
        ).reshape(3),
        0.0,
        1.0,
    )
    return (
        severity,
        speed_factor,
        layer_balance.astype(np.float32, copy=False),
        fog_rgb.astype(np.float32, copy=False),
    )


def _material_curve_vector(values: tuple[float, float, float], layer_count: int) -> tuple[float, ...]:
    vector = np.asarray(values, dtype=np.float32).reshape(3)
    if int(layer_count) == 1:
        return (float(np.mean(vector)),)
    if int(layer_count) != 3:
        raise ValueError(f"silver-halide material curve requires 1 or 3 layers, got {layer_count}")
    return tuple(float(value) for value in vector)


def _material_latent_curve_kwargs(
    film: FilmStockConfig,
    layer_count: int,
) -> dict[str, object]:
    density_range = np.maximum(
        np.asarray(film.density_max, dtype=np.float32)
        - np.asarray(film.density_min, dtype=np.float32),
        1e-6,
    )
    return {
        "latent_gamma": _material_curve_vector(film.hd_gamma, layer_count),
        "latent_density_range": _material_curve_vector(
            tuple(float(value) for value in density_range),
            layer_count,
        ),
        "latent_log_exposure_toe": _material_curve_vector(
            film.log_exposure_toe,
            layer_count,
        ),
        "latent_log_exposure_shoulder": _material_curve_vector(
            film.log_exposure_shoulder,
            layer_count,
        ),
        "latent_toe_width": float(film.hd_toe_width),
        "latent_shoulder_width": float(film.hd_shoulder_width),
        "latent_extreme_reversal_strength": float(
            film.extreme_exposure_reversal_strength
        ),
        "latent_extreme_reversal_start_loge": _material_curve_vector(
            film.extreme_exposure_reversal_start_loge,
            layer_count,
        ),
        "latent_extreme_reversal_width": float(
            film.extreme_exposure_reversal_width
        ),
    }


def _silver_material(film: FilmStockConfig, layer_count: int) -> ReducedFilmMaterial:
    layers = max(int(layer_count), 1)
    clear_support, mask_density = _base_and_mask_density(film)
    return ReducedFilmMaterial(
        key=str(film.name),
        layer_count=layers,
        halide_capacity=tuple([1.0] * layers),
        silver_density_per_layer=tuple([1.0 / layers] * layers),
        residual_halide_density_per_layer=tuple([0.12 / layers] * layers),
        base_density_rgb=tuple(float(value) for value in film.film_base_density_rgb),
        clear_support_density_rgb=clear_support,
        masking_coupler_density_rgb=mask_density,
        auxiliary_density_rgb=tuple(
            float(value) for value in film.auxiliary_layer_density_rgb
        ),
        medium_family=str(film.medium_family),
        color_system=("silver_bw" if _is_monochrome_material(film) else "silver_on_color_material"),
        retained_halide_density_rgb=tuple(
            float(value) for value in film.retained_halide_density_rgb
        ),
        **_material_latent_curve_kwargs(film, layers),
    )


def _compatibility_for_program(
    film: FilmStockConfig,
    program_key: str,
    layer_count: int,
) -> CompatibilityProfile:
    native_positive = str(film.image_polarity).strip().lower() == "positive"
    program_positive = "reversal" in str(program_key).strip().lower()
    native_mono = _is_monochrome_material(film)
    program_mono = str(program_key).strip().lower().startswith("bw_")
    mask_bleach = float(
        np.clip(getattr(film, "experimental_mask_bleach_susceptibility", 1.0), 0.0, 1.0)
    )
    mask_damage = float(
        np.clip(getattr(film, "experimental_mask_bleach_dye_damage", 0.16), 0.0, 1.0)
    )
    if native_positive == program_positive and native_mono == program_mono:
        return CompatibilityProfile(
            mask_bleach=mask_bleach,
            mask_bleach_dye_damage=mask_damage,
        )
    configured_balance = tuple(
        float(np.clip(value, 0.0, 2.0)) for value in film.cross_process_layer_balance
    )
    layer_balance = (
        (float(np.mean(configured_balance)),)
        if int(layer_count) == 1
        else configured_balance
    )
    return CompatibilityProfile(
        silver_development=float(np.clip(film.cross_process_silver_development, 0.0, 1.0)),
        dye_coupling=float(np.clip(film.cross_process_dye_coupling, 0.0, 2.0)),
        activation=float(np.clip(film.cross_process_activation, 0.0, 1.0)),
        silver_bleach=float(np.clip(film.cross_process_silver_bleach, 0.0, 1.0)),
        halide_fixing=float(np.clip(film.cross_process_halide_fixing, 0.0, 1.0)),
        silver_removal=float(np.clip(film.cross_process_silver_removal, 0.0, 1.0)),
        dye_stability=float(np.clip(film.cross_process_dye_stability, 0.0, 1.0)),
        auxiliary_removal=float(np.clip(film.cross_process_auxiliary_removal, 0.0, 1.0)),
        mask_bleach=mask_bleach,
        mask_bleach_dye_damage=mask_damage,
        layer_balance=layer_balance,
    )


def _material_latent_state(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    material: ReducedFilmMaterial,
    *,
    latent_layer_exposure_addition: np.ndarray | LayerExposureAdditionField | None = None,
) -> FilmProcessState:
    """Freeze material-only latent activation before any process operator.

    Exposure mapping, stock sensitivity/curve fields, and material degradation
    are allowed here. Developer recipe values are deliberately absent; time,
    temperature, concentration, exhaustion, push/pull, and compensation enter
    only through the subsequently constructed process program.
    """
    layer_exposure = rgb_exposure_to_layer_exposure(image_linear, film)
    _, speed_factor, degradation_balance, _ = _material_degradation_state(film)
    # The exposure mapper returns a private material-layer field. Apply the
    # established left-to-right stock factors in place before handing
    # ownership to ``ReducedFilmMaterial.expose``.
    layer_exposure *= speed_factor
    layer_exposure *= degradation_balance.reshape(1, 1, 3)
    if material.layer_count == 1:
        layer_exposure = layer_exposure.mean(axis=-1, keepdims=True)
    if latent_layer_exposure_addition is not None:
        expected_shape = (*layer_exposure.shape[:2], int(material.layer_count))
        material_scale = (
            np.asarray(
                (speed_factor * float(np.mean(degradation_balance)),),
                dtype=np.float32,
            )
            if material.layer_count == 1
            else (speed_factor * degradation_balance).astype(np.float32, copy=False)
        )
        if isinstance(latent_layer_exposure_addition, LAZY_LAYER_EXPOSURE_FIELD_TYPES):
            if latent_layer_exposure_addition.shape != expected_shape:
                raise ValueError(
                    "latent_layer_exposure_addition must match the formed layer "
                    f"exposure shape {expected_shape}, got "
                    f"{latent_layer_exposure_addition.shape}"
                )
            latent_layer_exposure_addition.add_scaled_to(
                layer_exposure,
                material_scale,
            )
            addition = None
        else:
            addition = np.asarray(latent_layer_exposure_addition, dtype=np.float32)
        if addition is not None:
            if addition.shape != expected_shape:
                raise ValueError(
                    "latent_layer_exposure_addition must match the formed layer "
                    f"exposure shape {expected_shape}, got {addition.shape}"
                )
            addition_minimum = float(np.min(addition))
            addition_maximum = float(np.max(addition))
            if not np.isfinite(addition_minimum) or not np.isfinite(addition_maximum):
                raise ValueError("latent_layer_exposure_addition must contain only finite values")
            if addition_minimum < 0.0:
                raise ValueError("latent_layer_exposure_addition must be non-negative")

            # The addition is already expressed in material-layer exposure
            # units, so it bypasses the scene RGB sensitivity matrix. It still
            # experiences the same stock ageing/speed response before the
            # material-only latent curve.
            layer_exposure += addition * material_scale.reshape(1, 1, -1)
    state = material.expose(
        layer_exposure,
        preserve_original_developability=False,
        consume_layer_exposure=True,
    )
    state.auxiliary_remaining = float(np.clip(film.auxiliary_layer_amount, 0.0, 1.0))
    return state


def _bw_optical_masters(
    film: FilmStockConfig,
    result: FilmProcessResult,
    effective: EffectiveDevelopmentState,
    silver_fraction: np.ndarray,
    *,
    include_d_min_shift: bool,
    process_totals: dict[str, float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Observe silver-only media without routing silver/salts through CMY."""
    material_is_mono = _is_monochrome_material(film)
    d_min = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)
    d_max = np.asarray(film.density_max, dtype=np.float32).reshape(1, 1, 3)
    mean_range = max(float(np.mean(d_max - d_min)), 1e-6)
    _, _, _, degradation_fog_rgb = _material_degradation_state(film)
    shift = float(effective.d_min_shift) if include_d_min_shift else 0.0
    if material_is_mono:
        emulsion_base_rgb = np.full(
            (1, 1, 3),
            float(np.mean(d_min + degradation_fog_rgb.reshape(1, 1, 3))) + shift,
            dtype=np.float32,
        )
        dye_and_base_rgb = emulsion_base_rgb + np.asarray(
            result.final_medium.base_density_rgb,
            dtype=np.float32,
        ).reshape(1, 1, 3)
    else:
        emulsion_layers = d_min + _rgb_density_to_layer_proxy(
            degradation_fog_rgb.reshape(1, 1, 3),
            film,
        ) + shift
        dye_and_base_rgb = layer_density_to_optical_density_rgb(
            emulsion_layers.astype(np.float32, copy=False),
            film,
            base_density_rgb=result.final_medium.base_density_rgb,
        )

    auxiliary = np.asarray(
        result.final_medium.auxiliary_density_rgb,
        dtype=np.float32,
    ) * float(result.final_medium.auxiliary_remaining)
    auxiliary_component = (
        auxiliary.reshape(1, 1, 3)
        if bool(np.any(auxiliary != 0.0))
        else None
    )
    clear_base = (dye_and_base_rgb.reshape(3) + auxiliary).astype(
        np.float32,
        copy=False,
    )
    silver_density = np.asarray(silver_fraction, dtype=np.float32) * (
        mean_range * float(effective.d_max_factor)
    )
    silver_rgb = np.broadcast_to(
        silver_density[..., None],
        (*silver_density.shape, 3),
    )
    has_residual = _pool_has_material(
        result.final_medium.residual_halide,
        known_total=(None if process_totals is None else process_totals["halide"]),
    )
    has_bleached = _pool_has_material(
        result.final_medium.bleached_halide,
        known_total=(
            None
            if process_totals is None
            else process_totals["bleached_halide"]
        ),
    )
    salt_rgb: np.ndarray | None = None
    if has_residual or has_bleached:
        residual_amount = np.mean(result.final_medium.residual_halide, axis=-1)
        bleached_amount = (
            np.zeros_like(residual_amount)
            if result.final_medium.bleached_halide is None
            else np.mean(result.final_medium.bleached_halide, axis=-1)
        )
        halide_color = np.asarray(
            result.final_medium.retained_halide_density_rgb,
            dtype=np.float32,
        ).reshape(1, 1, 3)
        salt_rgb = (
            (residual_amount + bleached_amount)[..., None]
            * (mean_range * 0.12)
            * halide_color
        )
    optical = compose_optical_density_rgb(
        silver_density_rgb=silver_rgb,
        residual_halide_density_rgb=salt_rgb,
        base_density_rgb=dye_and_base_rgb,
        auxiliary_density_rgb=auxiliary_component,
    )
    return optical, clear_base


def develop_bw_negative_reduced(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    *,
    local_development_rate: object | None = None,
    latent_layer_exposure_addition: np.ndarray | LayerExposureAdditionField | None = None,
    retain_latent_fraction: bool = True,
    retain_process_state: bool = True,
) -> ReducedBwDevelopment:
    """Run a silver-only negative program without changing material identity.

    A true B&W stock uses one reduced layer. A color stock processed in B&W
    chemistry keeps its three sensitivity layers and colored base, but forms no
    dye; their retained silver is observed as neutral broadband density.
    """
    effective = build_effective_development(recipe)
    material_is_mono = _is_monochrome_material(film)
    layer_count = 1 if material_is_mono else 3
    material = _silver_material(film, layer_count)
    latent_state = _material_latent_state(
        image_linear,
        film,
        material,
        latent_layer_exposure_addition=latent_layer_exposure_addition,
    )
    latent_fraction = (
        # The mutable process state may reuse its activation buffer after the
        # first development step.  A requested public diagnostic therefore
        # owns an explicit snapshot; the production-only discard path avoids
        # both this snapshot and the later replacement buffer.
        np.array(latent_state.developability, dtype=np.float32, copy=True)
        if retain_latent_fraction
        else None
    )
    program = program_from_develop_recipe(
        recipe,
        mode="bw_negative",
        material_process="negative",
        layer_count=layer_count,
    )
    compatibility = _compatibility_for_program(film, program.key, layer_count)
    result = apply_process_program(
        material,
        latent_state,
        program,
        compatibility,
        consume_latent_state=True,
        transfer_final_pool_ownership=True,
        local_development_rate=local_development_rate,
        validate_each_step=False,
    )
    process_totals = (
        None if retain_process_state else _compact_consumed_process_state(result)
    )

    silver = np.mean(result.final_medium.metallic_silver, axis=-1)
    residual_halide = result.final_medium.mean_total_fixable_halide()
    d_min_layers = np.asarray(film.density_min, dtype=np.float32)
    d_max_layers = np.asarray(film.density_max, dtype=np.float32)
    density_range = max(float(np.mean(d_max_layers - d_min_layers)), 1e-6)
    neutral_density = silver * density_range * float(effective.d_max_factor)
    # Incomplete fixing leaves a bounded veil/clouding term. It is material
    # density, not a scan-time effect, and therefore remains in the master.
    neutral_density += residual_halide * density_range * 0.12
    auxiliary_density_rgb = np.asarray(
        result.final_medium.auxiliary_density_rgb,
        dtype=np.float32,
    ) * float(result.final_medium.auxiliary_remaining)
    if material_is_mono:
        _, _, _, degradation_fog_rgb = _material_degradation_state(film)
        d_min = float(
            np.mean(d_min_layers + degradation_fog_rgb + auxiliary_density_rgb)
        ) + float(effective.d_min_shift)
        d_max = float(np.mean(d_max_layers))
        density = np.clip(neutral_density + d_min, 0.0, d_max * 1.35 + 0.25)
        density_rgb = np.repeat(density[..., None], 3, axis=-1).astype(
            np.float32,
            copy=False,
        )
    else:
        _, _, _, degradation_fog_rgb = _material_degradation_state(film)
        degradation_fog_layers = _rgb_density_to_layer_proxy(
            degradation_fog_rgb.reshape(1, 1, 3), film
        )
        auxiliary_layers = _rgb_density_to_layer_proxy(
            auxiliary_density_rgb.reshape(1, 1, 3), film
        )
        base_layers = d_min_layers.reshape(1, 1, 3) + degradation_fog_layers + auxiliary_layers + float(effective.d_min_shift)
        density_rgb = base_layers + _neutral_rgb_density_to_layer_proxy(neutral_density, film)
        upper = d_max_layers.reshape(1, 1, 3) * 1.35 + 0.35
        density_rgb = np.clip(density_rgb, 0.0, upper).astype(np.float32, copy=False)
    optical_density_rgb, clear_base_optical_density_rgb = _bw_optical_masters(
        film,
        result,
        effective,
        silver,
        include_d_min_shift=True,
        process_totals=process_totals,
    )
    return ReducedBwDevelopment(
        density_rgb=density_rgb,
        optical_density_rgb=optical_density_rgb,
        clear_base_optical_density_rgb=clear_base_optical_density_rgb,
        process_result=result,
        effective_development=effective,
        latent_fraction=(
            np.empty((0, 0, layer_count), dtype=np.float32)
            if latent_fraction is None
            else latent_fraction
        ),
        compatibility=compatibility,
        process_totals=process_totals,
    )


def shape_positive_density_fraction(
    fraction: np.ndarray,
    film: FilmStockConfig,
) -> np.ndarray:
    """Apply reduced material-side transparency characteristics.

    These controls describe the formed positive medium.  They are deliberately
    applied before scanning, so changing a scanner can never rewrite them.
    """
    positive = np.asarray(fraction, dtype=np.float32)
    contrast = max(float(getattr(film, "positive_density_contrast", 1.0)), 0.01)
    bias = float(getattr(film, "positive_density_bias", 0.0))

    # Contrast belongs to a bounded material fraction. A linear stretch here
    # used to push a broad interval above 1.0 and the final clip converted it
    # into a large, perfectly flat D-max plateau. Logit-domain shaping keeps
    # finite tonal differences finite while retaining true 0/1 material-pool
    # endpoints. ``bias * 4`` preserves the old control's approximate response
    # around the midpoint, where the logistic derivative is 0.25.
    bounded = np.clip(positive, 1e-6, 1.0 - 1e-6)
    logit = np.log(bounded)
    bounded *= -1.0
    np.log1p(bounded, out=bounded)
    logit -= bounded
    logit *= contrast
    logit += bias * 4.0
    np.clip(logit, -30.0, 30.0, out=logit)
    logit *= -1.0
    np.exp(logit, out=logit)
    logit += 1.0
    np.reciprocal(logit, out=logit)
    positive = logit

    def smoothstep_from_positive_work(work: np.ndarray) -> np.ndarray:
        curve = np.multiply(work, work, dtype=np.float32)
        work *= -2.0
        work += 3.0
        curve *= work
        return curve

    latitude = float(np.clip(getattr(film, "positive_latitude_compression", 0.0), 0.0, 1.0))
    if latitude > 0.0:
        np.copyto(bounded, positive)
        smooth = smoothstep_from_positive_work(bounded)
        positive *= 1.0 - latitude
        smooth *= latitude
        positive += smooth

    midtone = float(np.clip(getattr(film, "positive_midtone_density", 0.0), 0.0, 1.0))
    if midtone > 0.0:
        np.subtract(positive, 0.5, out=bounded)
        np.abs(bounded, out=bounded)
        bounded /= 0.5
        np.clip(bounded, 0.0, 1.0, out=bounded)
        np.subtract(1.0, bounded, out=bounded)
        weight = smoothstep_from_positive_work(bounded)
        weight *= midtone * 0.20
        positive += weight

    toe = float(np.clip(getattr(film, "positive_shadow_toe", 0.0), 0.0, 1.0))
    toe_width = float(np.clip(getattr(film, "positive_shadow_toe_width", 0.22), 0.02, 0.80))
    if toe > 0.0:
        np.subtract(positive, 1.0 - toe_width, out=bounded)
        bounded /= toe_width
        np.clip(bounded, 0.0, 1.0, out=bounded)
        dense = smoothstep_from_positive_work(bounded)
        dense *= toe * toe_width * 0.45
        positive -= dense

    shoulder = float(np.clip(getattr(film, "positive_highlight_shoulder", 0.0), 0.0, 1.0))
    shoulder_width = float(
        np.clip(getattr(film, "positive_highlight_shoulder_width", 0.18), 0.02, 0.80)
    )
    if shoulder > 0.0:
        np.divide(positive, shoulder_width, out=bounded)
        np.clip(bounded, 0.0, 1.0, out=bounded)
        np.subtract(1.0, bounded, out=bounded)
        thin = smoothstep_from_positive_work(bounded)
        thin *= shoulder * shoulder_width * 0.55
        positive += thin
    np.clip(positive, 0.0, 1.0, out=positive)
    return positive.astype(np.float32, copy=False)


def shape_positive_dye_chroma(
    dye_fraction: np.ndarray,
    film: FilmStockConfig,
) -> np.ndarray:
    """Apply material-side midtone saturation and endpoint chroma loss.

    A formed positive's thin highlight approaches the support/D-min and its
    dense shadow approaches finite D-max.  Chroma is smoothly reduced before
    either endpoint, avoiding a scan-time hard white/black colour clip.
    """
    dye = np.asarray(dye_fraction, dtype=np.float32)
    neutral = dye.mean(axis=-1, keepdims=True)
    level = np.clip(neutral, 0.0, 1.0)
    saturation = max(float(getattr(film, "positive_dye_saturation", 1.0)), 0.0)
    highlight_retention = float(
        np.clip(getattr(film, "positive_highlight_chroma_retention", 0.22), 0.0, 1.0)
    )
    shadow_retention = float(
        np.clip(getattr(film, "positive_shadow_chroma_retention", 0.28), 0.0, 1.0)
    )
    highlight_width = float(
        np.clip(getattr(film, "positive_highlight_shoulder_width", 0.18), 0.02, 0.80)
    )
    shadow_width = float(
        np.clip(getattr(film, "positive_shadow_toe_width", 0.22), 0.02, 0.80)
    )
    highlight_gate = np.divide(level, highlight_width, dtype=np.float32)
    np.clip(highlight_gate, 0.0, 1.0, out=highlight_gate)
    highlight_curve = np.multiply(highlight_gate, highlight_gate, dtype=np.float32)
    highlight_gate *= -2.0
    highlight_gate += 3.0
    highlight_curve *= highlight_gate

    shadow_gate = np.subtract(1.0, level, dtype=np.float32)
    shadow_gate /= shadow_width
    np.clip(shadow_gate, 0.0, 1.0, out=shadow_gate)
    shadow_curve = np.multiply(shadow_gate, shadow_gate, dtype=np.float32)
    shadow_gate *= -2.0
    shadow_gate += 3.0
    shadow_curve *= shadow_gate

    highlight_curve *= 1.0 - highlight_retention
    highlight_curve += highlight_retention
    shadow_curve *= 1.0 - shadow_retention
    shadow_curve += shadow_retention
    highlight_curve *= shadow_curve
    highlight_curve *= saturation
    shaped = np.subtract(dye, neutral, dtype=np.float32)
    shaped *= highlight_curve
    shaped += neutral
    np.clip(shaped, 0.0, 1.0, out=shaped)
    return shaped.astype(np.float32, copy=False)


def develop_bw_reversal_reduced(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    *,
    local_development_rate: object | None = None,
    latent_layer_exposure_addition: np.ndarray | LayerExposureAdditionField | None = None,
    retain_latent_fraction: bool = True,
    retain_process_state: bool = True,
) -> ReducedBwDevelopment:
    """Form a silver positive by developing the halide left by first development."""
    effective = build_effective_development(recipe)
    material_is_mono = _is_monochrome_material(film)
    layer_count = 1 if material_is_mono else 3
    material = _silver_material(film, layer_count)
    state = _material_latent_state(
        image_linear,
        film,
        material,
        latent_layer_exposure_addition=latent_layer_exposure_addition,
    )
    latent_fraction = (
        np.array(state.developability, dtype=np.float32, copy=True)
        if retain_latent_fraction
        else None
    )
    program = program_from_develop_recipe(
        recipe,
        mode="bw_reversal",
        material_process="reversal",
        layer_count=layer_count,
    )
    compatibility = _compatibility_for_program(film, program.key, layer_count)
    result = apply_process_program(
        material,
        state,
        program,
        compatibility,
        consume_latent_state=True,
        transfer_final_pool_ownership=True,
        local_development_rate=local_development_rate,
        validate_each_step=False,
    )
    process_totals = (
        None if retain_process_state else _compact_consumed_process_state(result)
    )
    silver_layers = shape_positive_density_fraction(result.final_medium.metallic_silver, film)
    silver = np.mean(silver_layers, axis=-1)
    residual = result.final_medium.mean_total_fixable_halide()
    d_min_layers = np.asarray(film.density_min, dtype=np.float32)
    d_max_layers = np.asarray(film.density_max, dtype=np.float32)
    density_range = max(float(np.mean(d_max_layers - d_min_layers)), 1e-6)
    neutral_density = silver * density_range * float(effective.d_max_factor)
    neutral_density += residual * density_range * 0.12
    auxiliary_density_rgb = np.asarray(
        result.final_medium.auxiliary_density_rgb,
        dtype=np.float32,
    ) * float(result.final_medium.auxiliary_remaining)
    if material_is_mono:
        _, _, _, degradation_fog_rgb = _material_degradation_state(film)
        density = float(
            np.mean(d_min_layers + degradation_fog_rgb + auxiliary_density_rgb)
        ) + neutral_density
        density = np.clip(density, 0.0, float(np.mean(d_max_layers)) * 1.35 + 0.25)
        density_rgb = np.repeat(density[..., None], 3, axis=-1).astype(
            np.float32,
            copy=False,
        )
    else:
        _, _, _, degradation_fog_rgb = _material_degradation_state(film)
        density_rgb = d_min_layers.reshape(1, 1, 3) + _rgb_density_to_layer_proxy(
            degradation_fog_rgb.reshape(1, 1, 3), film
        )
        density_rgb += _rgb_density_to_layer_proxy(
            auxiliary_density_rgb.reshape(1, 1, 3), film
        )
        density_rgb = density_rgb + _neutral_rgb_density_to_layer_proxy(neutral_density, film)
        density_rgb = np.clip(
            density_rgb,
            0.0,
            d_max_layers.reshape(1, 1, 3) * 1.35 + 0.35,
        ).astype(np.float32, copy=False)
    optical_density_rgb, clear_base_optical_density_rgb = _bw_optical_masters(
        film,
        result,
        effective,
        silver,
        include_d_min_shift=False,
        process_totals=process_totals,
    )
    return ReducedBwDevelopment(
        density_rgb=density_rgb,
        optical_density_rgb=optical_density_rgb,
        clear_base_optical_density_rgb=clear_base_optical_density_rgb,
        process_result=result,
        effective_development=effective,
        latent_fraction=(
            np.empty((0, 0, layer_count), dtype=np.float32)
            if latent_fraction is None
            else latent_fraction
        ),
        compatibility=compatibility,
        process_totals=process_totals,
    )


def _color_material(film: FilmStockConfig) -> ReducedFilmMaterial:
    clear_support, mask_density = _base_and_mask_density(film)
    return ReducedFilmMaterial(
        key=str(film.name),
        layer_count=3,
        halide_capacity=(1.0, 1.0, 1.0),
        coupler_capacity=(1.0, 1.0, 1.0),
        dye_absorption_matrix=tuple(
            tuple(float(value) for value in row) for row in film.dye_absorption_matrix
        ),
        silver_density_per_layer=(1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0),
        residual_halide_density_per_layer=(0.04, 0.04, 0.04),
        base_density_rgb=tuple(float(value) for value in film.film_base_density_rgb),
        clear_support_density_rgb=clear_support,
        masking_coupler_density_rgb=mask_density,
        auxiliary_density_rgb=tuple(
            float(value) for value in film.auxiliary_layer_density_rgb
        ),
        medium_family=str(film.medium_family),
        color_system="color_coupler",
        retained_halide_density_rgb=tuple(
            float(value) for value in film.retained_halide_density_rgb
        ),
        **_material_latent_curve_kwargs(film, 3),
    )


def _neutral_rgb_density_to_layer_proxy(
    neutral_density: np.ndarray,
    film: FilmStockConfig,
) -> np.ndarray:
    """Represent neutral silver/halide density in the legacy three-layer master."""
    rgb = np.repeat(np.asarray(neutral_density, dtype=np.float32)[..., None], 3, axis=-1)
    return _rgb_density_to_layer_proxy(rgb, film)


def _rgb_density_to_layer_proxy(
    rgb_density: np.ndarray,
    film: FilmStockConfig,
) -> np.ndarray:
    """Map an RGB optical-density contribution into the three-layer master."""
    inverse = pseudoinverse_3x3(film.dye_absorption_matrix)
    rgb = np.asarray(rgb_density, dtype=np.float32)
    if rgb.shape[-1] != 3:
        raise ValueError(f"rgb_density must end with three channels, got {rgb.shape}")
    layers = np.einsum("...r,lr->...l", rgb, inverse)
    np.maximum(layers, 0.0, out=layers)
    return layers.astype(np.float32, copy=False)


def develop_color_negative_reduced(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    *,
    local_development_rate: object | None = None,
    latent_layer_exposure_addition: np.ndarray | LayerExposureAdditionField | None = None,
    retain_latent_fraction: bool = True,
    retain_process_state: bool = True,
) -> ReducedColorDevelopment:
    """Run color coupling, bleach, and fixing before producing a density master."""
    effective = build_effective_development(recipe)
    material = _color_material(film)
    latent_state = _material_latent_state(
        image_linear,
        film,
        material,
        latent_layer_exposure_addition=latent_layer_exposure_addition,
    )
    latent_fraction = (
        np.array(latent_state.developability, dtype=np.float32, copy=True)
        if retain_latent_fraction
        else None
    )
    program = program_from_develop_recipe(
        recipe,
        mode="color_negative",
        material_process="negative",
        layer_count=3,
    )
    compatibility = _compatibility_for_program(film, program.key, 3)
    result = apply_process_program(
        material,
        latent_state,
        program,
        compatibility,
        consume_latent_state=True,
        transfer_final_pool_ownership=True,
        local_development_rate=local_development_rate,
        validate_each_step=False,
    )
    process_totals = (
        None if retain_process_state else _compact_consumed_process_state(result)
    )

    d_min = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)
    d_max = np.asarray(film.density_max, dtype=np.float32).reshape(1, 1, 3)
    density_range = np.maximum(d_max - d_min, 1e-6)
    dye = (
        np.zeros_like(result.final_medium.metallic_silver, dtype=np.float32)
        if result.final_medium.dye is None
        else result.final_medium.dye
    )
    _, _, _, degradation_fog_rgb = _material_degradation_state(film)
    degradation_fog_layers = _rgb_density_to_layer_proxy(
        degradation_fog_rgb.reshape(1, 1, 3), film
    )
    image_dye_base = d_min + degradation_fog_layers + float(effective.d_min_shift)
    image_dye_layers = np.multiply(dye, density_range, dtype=np.float32)
    del dye
    image_dye_layers *= float(effective.d_max_factor)
    image_dye_layers += image_dye_base
    # Both the compatibility CMY master and the authoritative RGB observation
    # begin from exactly the same formed dye/D-min layers. Preserve that field
    # once and copy it for the compatibility-only silver/auxiliary additions,
    # instead of evaluating the complete full-frame dye expression twice.
    density_cmy = image_dye_layers.copy()
    auxiliary_density_rgb = np.asarray(
        result.final_medium.auxiliary_density_rgb,
        dtype=np.float32,
    ) * float(result.final_medium.auxiliary_remaining)
    has_auxiliary_density = bool(np.any(auxiliary_density_rgb != 0.0))
    if has_auxiliary_density:
        density_cmy += _rgb_density_to_layer_proxy(
            auxiliary_density_rgb.reshape(1, 1, 3), film
        )

    field_shape = result.final_medium.metallic_silver.shape[:2]
    has_silver = _pool_has_material(
        result.final_medium.metallic_silver,
        known_total=(
            None
            if process_totals is None
            else process_totals["metallic_silver"]
        ),
    )
    has_residual = _pool_has_material(
        result.final_medium.residual_halide,
        known_total=(None if process_totals is None else process_totals["halide"]),
    )
    has_bleached = _pool_has_material(
        result.final_medium.bleached_halide,
        known_total=(
            None
            if process_totals is None
            else process_totals["bleached_halide"]
        ),
    )
    silver_amount = (
        np.mean(result.final_medium.metallic_silver, axis=-1)
        if has_silver
        else _zero_scalar_density(field_shape)
    )
    mean_range = float(np.mean(density_range))
    if has_silver:
        silver_amount *= mean_range
        silver_amount *= 0.72
    silver_density_rgb = silver_amount
    halide_color = np.asarray(
        result.final_medium.retained_halide_density_rgb,
        dtype=np.float32,
    ).reshape(1, 1, 3)
    if has_silver or has_residual or has_bleached:
        residual_amount = result.final_medium.mean_total_fixable_halide()
        residual_amount *= mean_range
        residual_amount *= 0.12
        residual_density_rgb = residual_amount[..., None] * halide_color
        residual_density_rgb += silver_density_rgb[..., None]
        density_cmy += _rgb_density_to_layer_proxy(residual_density_rgb, film)
        del residual_density_rgb, residual_amount
    upper = d_max * 1.35 + 0.35
    np.clip(density_cmy, 0.0, upper, out=density_cmy)
    # Authoritative observation keeps unlike materials separate.  Only image
    # dye/D-min layers pass through the dye absorption and base interaction;
    # neutral silver, retained salts and auxiliary layers are added in RGB
    # optical-density space instead of being forced through a CMY pseudoinverse.
    dye_and_base_rgb = layer_density_to_optical_density_rgb(
        image_dye_layers.astype(np.float32, copy=False),
        film,
        base_density_rgb=result.final_medium.base_density_rgb,
        consume_input=True,
    )
    del image_dye_layers
    if has_residual:
        residual_halide_amount = np.mean(
            result.final_medium.residual_halide,
            axis=-1,
        )
        residual_halide_amount *= mean_range
        residual_halide_amount *= 0.12
        residual_only_rgb = residual_halide_amount[..., None] * halide_color
    else:
        residual_only_rgb = _zero_rgb_density(field_shape)
    if has_bleached:
        bleached_halide_amount = np.mean(
            result.final_medium.bleached_halide,
            axis=-1,
        )
        bleached_halide_amount *= mean_range
        bleached_halide_amount *= 0.12
        bleached_only_rgb = bleached_halide_amount[..., None] * halide_color
    else:
        bleached_only_rgb = _zero_rgb_density(field_shape)
    silver_density_rgb_view = np.broadcast_to(
        silver_density_rgb[..., None],
        (*silver_density_rgb.shape, 3),
    )
    optical_density_rgb = compose_optical_density_rgb(
        silver_density_rgb=silver_density_rgb_view,
        residual_halide_density_rgb=(residual_only_rgb if has_residual else None),
        bleached_halide_density_rgb=(bleached_only_rgb if has_bleached else None),
        base_density_rgb=dye_and_base_rgb,
        auxiliary_density_rgb=(
            auxiliary_density_rgb.reshape(1, 1, 3)
            if has_auxiliary_density
            else None
        ),
        consume_base_density=True,
    )
    clear_base_optical_density_rgb = (
        layer_density_to_optical_density_rgb(
            (
                d_min
                + degradation_fog_layers
                + float(effective.d_min_shift)
            ).astype(np.float32, copy=False),
            film,
            base_density_rgb=result.final_medium.base_density_rgb,
        ).reshape(3)
        + auxiliary_density_rgb
    ).astype(np.float32, copy=False)
    return ReducedColorDevelopment(
        density_cmy=density_cmy,
        optical_density_rgb=optical_density_rgb,
        clear_base_optical_density_rgb=clear_base_optical_density_rgb,
        process_result=result,
        effective_development=effective,
        latent_fraction=(
            np.empty((0, 0, 3), dtype=np.float32)
            if latent_fraction is None
            else latent_fraction
        ),
        silver_density=silver_density_rgb,
        residual_halide_density_rgb=residual_only_rgb.astype(np.float32, copy=False),
        bleached_halide_density_rgb=bleached_only_rgb.astype(np.float32, copy=False),
        compatibility=compatibility,
        process_totals=process_totals,
    )


def develop_color_reversal_reduced(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    *,
    local_development_rate: object | None = None,
    latent_layer_exposure_addition: np.ndarray | LayerExposureAdditionField | None = None,
    retain_latent_fraction: bool = True,
    retain_process_state: bool = True,
) -> ReducedColorDevelopment:
    """Form a dye positive from the halide remaining after first development."""
    effective = build_effective_development(recipe)
    material = _color_material(film)
    state = _material_latent_state(
        image_linear,
        film,
        material,
        latent_layer_exposure_addition=latent_layer_exposure_addition,
    )
    latent_fraction = (
        np.array(state.developability, dtype=np.float32, copy=True)
        if retain_latent_fraction
        else None
    )
    program = program_from_develop_recipe(
        recipe,
        mode="color_reversal",
        material_process="reversal",
        layer_count=3,
    )
    compatibility = _compatibility_for_program(film, program.key, 3)
    result = apply_process_program(
        material,
        state,
        program,
        compatibility,
        consume_latent_state=True,
        transfer_final_pool_ownership=True,
        local_development_rate=local_development_rate,
        validate_each_step=False,
    )
    process_totals = (
        None if retain_process_state else _compact_consumed_process_state(result)
    )

    d_min = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)
    d_max = np.asarray(film.density_max, dtype=np.float32).reshape(1, 1, 3)
    density_range = np.maximum(d_max - d_min, 1e-6)
    dye = (
        np.zeros_like(result.final_medium.metallic_silver, dtype=np.float32)
        if result.final_medium.dye is None
        else result.final_medium.dye
    )
    dye = shape_positive_density_fraction(dye, film)
    dye = shape_positive_dye_chroma(dye, film)
    _, _, _, degradation_fog_rgb = _material_degradation_state(film)
    degradation_fog_layers = _rgb_density_to_layer_proxy(
        degradation_fog_rgb.reshape(1, 1, 3), film
    )
    image_dye_base = d_min + degradation_fog_layers
    image_dye_layers = np.multiply(dye, density_range, dtype=np.float32)
    del dye
    image_dye_layers *= float(effective.d_max_factor)
    image_dye_layers += image_dye_base
    density_cmy = image_dye_layers.copy()
    auxiliary_density_rgb = np.asarray(
        result.final_medium.auxiliary_density_rgb,
        dtype=np.float32,
    ) * float(result.final_medium.auxiliary_remaining)
    has_auxiliary_density = bool(np.any(auxiliary_density_rgb != 0.0))
    if has_auxiliary_density:
        density_cmy += _rgb_density_to_layer_proxy(
            auxiliary_density_rgb.reshape(1, 1, 3), film
        )

    field_shape = result.final_medium.metallic_silver.shape[:2]
    has_silver = _pool_has_material(
        result.final_medium.metallic_silver,
        known_total=(
            None
            if process_totals is None
            else process_totals["metallic_silver"]
        ),
    )
    has_residual = _pool_has_material(
        result.final_medium.residual_halide,
        known_total=(None if process_totals is None else process_totals["halide"]),
    )
    has_bleached = _pool_has_material(
        result.final_medium.bleached_halide,
        known_total=(
            None
            if process_totals is None
            else process_totals["bleached_halide"]
        ),
    )
    silver_amount = (
        np.mean(result.final_medium.metallic_silver, axis=-1)
        if has_silver
        else _zero_scalar_density(field_shape)
    )
    mean_range = float(np.mean(density_range))
    if has_silver:
        silver_amount *= mean_range
        silver_amount *= 0.72
    silver_density_rgb = silver_amount
    halide_color = np.asarray(
        result.final_medium.retained_halide_density_rgb,
        dtype=np.float32,
    ).reshape(1, 1, 3)
    if has_silver or has_residual or has_bleached:
        residual_amount = result.final_medium.mean_total_fixable_halide()
        residual_amount *= mean_range
        residual_amount *= 0.12
        residual_density_rgb = residual_amount[..., None] * halide_color
        residual_density_rgb += silver_density_rgb[..., None]
        density_cmy += _rgb_density_to_layer_proxy(residual_density_rgb, film)
        del residual_density_rgb, residual_amount
    np.clip(density_cmy, 0.0, d_max * 1.35 + 0.35, out=density_cmy)
    dye_and_base_rgb = layer_density_to_optical_density_rgb(
        image_dye_layers.astype(np.float32, copy=False),
        film,
        base_density_rgb=result.final_medium.base_density_rgb,
        consume_input=True,
    )
    del image_dye_layers
    if has_residual:
        residual_halide_amount = np.mean(
            result.final_medium.residual_halide,
            axis=-1,
        )
        residual_halide_amount *= mean_range
        residual_halide_amount *= 0.12
        residual_only_rgb = residual_halide_amount[..., None] * halide_color
    else:
        residual_only_rgb = _zero_rgb_density(field_shape)
    if has_bleached:
        bleached_halide_amount = np.mean(
            result.final_medium.bleached_halide,
            axis=-1,
        )
        bleached_halide_amount *= mean_range
        bleached_halide_amount *= 0.12
        bleached_only_rgb = bleached_halide_amount[..., None] * halide_color
    else:
        bleached_only_rgb = _zero_rgb_density(field_shape)
    silver_density_rgb_view = np.broadcast_to(
        silver_density_rgb[..., None],
        (*silver_density_rgb.shape, 3),
    )
    optical_density_rgb = compose_optical_density_rgb(
        silver_density_rgb=silver_density_rgb_view,
        residual_halide_density_rgb=(residual_only_rgb if has_residual else None),
        bleached_halide_density_rgb=(bleached_only_rgb if has_bleached else None),
        base_density_rgb=dye_and_base_rgb,
        auxiliary_density_rgb=(
            auxiliary_density_rgb.reshape(1, 1, 3)
            if has_auxiliary_density
            else None
        ),
        consume_base_density=True,
    )
    clear_base_optical_density_rgb = (
        layer_density_to_optical_density_rgb(
            (d_min + degradation_fog_layers).astype(np.float32, copy=False),
            film,
            base_density_rgb=result.final_medium.base_density_rgb,
        ).reshape(3)
        + auxiliary_density_rgb
    ).astype(np.float32, copy=False)
    return ReducedColorDevelopment(
        density_cmy=density_cmy,
        optical_density_rgb=optical_density_rgb,
        clear_base_optical_density_rgb=clear_base_optical_density_rgb,
        process_result=result,
        effective_development=effective,
        latent_fraction=(
            np.empty((0, 0, 3), dtype=np.float32)
            if latent_fraction is None
            else latent_fraction
        ),
        silver_density=silver_density_rgb,
        residual_halide_density_rgb=residual_only_rgb.astype(np.float32, copy=False),
        bleached_halide_density_rgb=bleached_only_rgb.astype(np.float32, copy=False),
        compatibility=compatibility,
        process_totals=process_totals,
    )
