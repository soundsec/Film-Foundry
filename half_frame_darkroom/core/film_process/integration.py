"""Gradual pixel-path integrations for the reduced film-process framework."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from half_frame_darkroom.core.development import EffectiveDevelopmentState, build_effective_development
from half_frame_darkroom.core.derived_cache import pseudoinverse_3x3
from half_frame_darkroom.core.film_process.model import FilmProcessState, ReducedFilmMaterial
from half_frame_darkroom.core.film_process.operators import (
    CompatibilityProfile,
    FilmProcessResult,
    apply_process_program,
)
from half_frame_darkroom.core.film_process.recipe import program_from_develop_recipe
from half_frame_darkroom.core.sensitometry import hd_density_curve, rgb_exposure_to_layer_exposure
from half_frame_darkroom.model.config import DevelopRecipeConfig, FilmStockConfig


@dataclass(frozen=True, slots=True)
class ReducedBwDevelopment:
    density_rgb: np.ndarray
    process_result: FilmProcessResult
    effective_development: EffectiveDevelopmentState
    latent_fraction: np.ndarray
    compatibility: CompatibilityProfile


@dataclass(frozen=True, slots=True)
class ReducedColorDevelopment:
    density_cmy: np.ndarray
    process_result: FilmProcessResult
    effective_development: EffectiveDevelopmentState
    latent_fraction: np.ndarray
    silver_density_rgb: np.ndarray
    residual_halide_density_rgb: np.ndarray
    compatibility: CompatibilityProfile


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
    return severity, speed_factor, layer_balance.astype(np.float32), fog_rgb.astype(np.float32)


def _silver_material(film: FilmStockConfig, layer_count: int) -> ReducedFilmMaterial:
    layers = max(int(layer_count), 1)
    return ReducedFilmMaterial(
        key=str(film.name),
        layer_count=layers,
        halide_capacity=tuple([1.0] * layers),
        silver_density_per_layer=tuple([1.0 / layers] * layers),
        residual_halide_density_per_layer=tuple([0.12 / layers] * layers),
        base_density_rgb=tuple(float(value) for value in film.film_base_density_rgb),
        auxiliary_density_rgb=tuple(
            float(value) for value in film.auxiliary_layer_density_rgb
        ),
        medium_family=str(film.medium_family),
        color_system=("silver_bw" if _is_monochrome_material(film) else "silver_on_color_material"),
        retained_halide_density_rgb=tuple(
            float(value) for value in film.retained_halide_density_rgb
        ),
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
    if native_positive == program_positive and native_mono == program_mono:
        return CompatibilityProfile()
    configured_balance = tuple(
        float(np.clip(value, 0.0, 2.0)) for value in film.cross_process_layer_balance
    )
    layer_balance = (
        (float(np.mean(configured_balance)),)
        if int(layer_count) == 1
        else configured_balance
    )
    return CompatibilityProfile(
        silver_development=float(np.clip(film.cross_process_silver_development, 0.0, 2.0)),
        dye_coupling=float(np.clip(film.cross_process_dye_coupling, 0.0, 2.0)),
        activation=float(np.clip(film.cross_process_activation, 0.0, 2.0)),
        silver_bleach=float(np.clip(film.cross_process_silver_bleach, 0.0, 2.0)),
        halide_fixing=float(np.clip(film.cross_process_halide_fixing, 0.0, 2.0)),
        silver_removal=float(np.clip(film.cross_process_silver_removal, 0.0, 2.0)),
        dye_stability=float(np.clip(film.cross_process_dye_stability, 0.0, 1.0)),
        auxiliary_removal=float(np.clip(film.cross_process_auxiliary_removal, 0.0, 2.0)),
        layer_balance=layer_balance,
    )


def _material_latent_layers(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    effective: EffectiveDevelopmentState,
) -> np.ndarray:
    """Map exposure to latent developability using the existing material curve.

    The current recipe supplies gamma and toe/shoulder shape. D-min and D-max
    are normalized back out here because fog and density amplitude belong to
    final-medium formation; actual completion and fixing remain state-transition
    operators. This keeps compensation and push/pull curve shape active on the
    authoritative pool path without double-applying density or fog.
    """
    layer_exposure = rgb_exposure_to_layer_exposure(image_linear, film)
    _, speed_factor, degradation_balance, _ = _material_degradation_state(film)
    layer_exposure = layer_exposure * speed_factor * degradation_balance.reshape(1, 1, 3)
    shaped_density = hd_density_curve(layer_exposure, film, recipe)
    d_min = (
        np.asarray(film.density_min, dtype=np.float32)
        + float(effective.d_min_shift)
    ).reshape(1, 1, 3)
    d_max = (
        np.asarray(film.density_max, dtype=np.float32)
        * float(effective.d_max_factor)
    ).reshape(1, 1, 3)
    latent = np.clip(
        (shaped_density - d_min) / np.maximum(d_max - d_min, 1e-6),
        0.0,
        1.0,
    )
    return latent.astype(np.float32)


def develop_bw_negative_reduced(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
) -> ReducedBwDevelopment:
    """Run a silver-only negative program without changing material identity.

    A true B&W stock uses one reduced layer. A color stock processed in B&W
    chemistry keeps its three sensitivity layers and colored base, but forms no
    dye; their retained silver is observed as neutral broadband density.
    """
    effective = build_effective_development(recipe)
    material_is_mono = _is_monochrome_material(film)
    layer_count = 1 if material_is_mono else 3
    latent_layers = _material_latent_layers(image_linear, film, recipe, effective)
    latent_fraction = (
        latent_layers.mean(axis=-1, keepdims=True) if material_is_mono else latent_layers
    )
    zeros = np.zeros_like(latent_fraction, dtype=np.float32)
    latent_state = FilmProcessState(
        halide=np.ones_like(latent_fraction, dtype=np.float32),
        developability=latent_fraction,
        metallic_silver=zeros,
        # The reduced operator model derives H^E/H^U from the live continuous
        # activation field; no operator reads a second original copy.
        original_developability=None,
        auxiliary_remaining=float(np.clip(film.auxiliary_layer_amount, 0.0, 1.0)),
    )
    program = program_from_develop_recipe(
        recipe,
        mode="bw_negative",
        material_process="negative",
        layer_count=layer_count,
    )
    compatibility = _compatibility_for_program(film, program.key, layer_count)
    result = apply_process_program(
        _silver_material(film, layer_count),
        latent_state,
        program,
        compatibility,
        consume_latent_state=True,
    )

    silver = np.mean(result.final_medium.metallic_silver, axis=-1)
    residual_halide = np.mean(result.final_medium.total_fixable_halide(), axis=-1)
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
        density_rgb = np.repeat(density[..., None], 3, axis=-1).astype(np.float32)
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
        density_rgb = np.clip(density_rgb, 0.0, upper).astype(np.float32)
    return ReducedBwDevelopment(
        density_rgb=density_rgb,
        process_result=result,
        effective_development=effective,
        latent_fraction=latent_fraction,
        compatibility=compatibility,
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
    positive = (positive - 0.5) * contrast + 0.5
    positive += float(getattr(film, "positive_density_bias", 0.0))

    latitude = float(np.clip(getattr(film, "positive_latitude_compression", 0.0), 0.0, 1.0))
    if latitude > 0.0:
        smooth = positive * positive * (3.0 - 2.0 * positive)
        positive = positive * (1.0 - latitude) + smooth * latitude

    midtone = float(np.clip(getattr(film, "positive_midtone_density", 0.0), 0.0, 1.0))
    if midtone > 0.0:
        weight = 1.0 - np.clip(np.abs(positive - 0.5) / 0.5, 0.0, 1.0)
        weight = weight * weight * (3.0 - 2.0 * weight)
        positive += midtone * 0.20 * weight

    toe = float(np.clip(getattr(film, "positive_shadow_toe", 0.0), 0.0, 1.0))
    toe_width = float(np.clip(getattr(film, "positive_shadow_toe_width", 0.22), 0.02, 0.80))
    if toe > 0.0:
        dense = np.clip((positive - (1.0 - toe_width)) / toe_width, 0.0, 1.0)
        dense = dense * dense * (3.0 - 2.0 * dense)
        positive -= toe * toe_width * 0.45 * dense

    shoulder = float(np.clip(getattr(film, "positive_highlight_shoulder", 0.0), 0.0, 1.0))
    shoulder_width = float(
        np.clip(getattr(film, "positive_highlight_shoulder_width", 0.18), 0.02, 0.80)
    )
    if shoulder > 0.0:
        thin = 1.0 - np.clip(positive / shoulder_width, 0.0, 1.0)
        thin = thin * thin * (3.0 - 2.0 * thin)
        positive += shoulder * shoulder_width * 0.55 * thin
    return np.clip(positive, 0.0, 1.0).astype(np.float32)


def develop_bw_reversal_reduced(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
) -> ReducedBwDevelopment:
    """Form a silver positive by developing the halide left by first development."""
    effective = build_effective_development(recipe)
    material_is_mono = _is_monochrome_material(film)
    layer_count = 1 if material_is_mono else 3
    latent_layers = _material_latent_layers(image_linear, film, recipe, effective)
    latent_fraction = (
        latent_layers.mean(axis=-1, keepdims=True) if material_is_mono else latent_layers
    )
    zeros = np.zeros_like(latent_fraction, dtype=np.float32)
    state = FilmProcessState(
        halide=np.ones_like(latent_fraction, dtype=np.float32),
        developability=latent_fraction,
        metallic_silver=zeros,
        original_developability=None,
        auxiliary_remaining=float(np.clip(film.auxiliary_layer_amount, 0.0, 1.0)),
    )
    program = program_from_develop_recipe(
        recipe,
        mode="bw_reversal",
        material_process="reversal",
        layer_count=layer_count,
    )
    compatibility = _compatibility_for_program(film, program.key, layer_count)
    result = apply_process_program(
        _silver_material(film, layer_count),
        state,
        program,
        compatibility,
        consume_latent_state=True,
    )
    silver_layers = shape_positive_density_fraction(result.final_medium.metallic_silver, film)
    silver = np.mean(silver_layers, axis=-1)
    residual = np.mean(result.final_medium.total_fixable_halide(), axis=-1)
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
        density_rgb = np.repeat(density[..., None], 3, axis=-1).astype(np.float32)
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
        ).astype(np.float32)
    return ReducedBwDevelopment(
        density_rgb=density_rgb,
        process_result=result,
        effective_development=effective,
        latent_fraction=latent_fraction,
        compatibility=compatibility,
    )


def _color_material(film: FilmStockConfig) -> ReducedFilmMaterial:
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
        auxiliary_density_rgb=tuple(
            float(value) for value in film.auxiliary_layer_density_rgb
        ),
        medium_family=str(film.medium_family),
        color_system="color_coupler",
        retained_halide_density_rgb=tuple(
            float(value) for value in film.retained_halide_density_rgb
        ),
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
    return np.clip(layers, 0.0, None).astype(np.float32)


def develop_color_negative_reduced(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
) -> ReducedColorDevelopment:
    """Run color coupling, bleach, and fixing before producing a density master."""
    effective = build_effective_development(recipe)
    latent_fraction = _material_latent_layers(image_linear, film, recipe, effective)
    zeros = np.zeros_like(latent_fraction, dtype=np.float32)
    latent_state = FilmProcessState(
        halide=np.ones_like(latent_fraction, dtype=np.float32),
        developability=latent_fraction,
        metallic_silver=zeros,
        coupler=np.ones_like(latent_fraction, dtype=np.float32),
        dye=zeros.copy(),
        original_developability=None,
        auxiliary_remaining=float(np.clip(film.auxiliary_layer_amount, 0.0, 1.0)),
    )
    program = program_from_develop_recipe(
        recipe,
        mode="color_negative",
        material_process="negative",
        layer_count=3,
    )
    compatibility = _compatibility_for_program(film, program.key, 3)
    result = apply_process_program(
        _color_material(film),
        latent_state,
        program,
        compatibility,
        consume_latent_state=True,
    )

    d_min = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)
    d_max = np.asarray(film.density_max, dtype=np.float32).reshape(1, 1, 3)
    density_range = np.maximum(d_max - d_min, 1e-6)
    dye = np.zeros_like(latent_fraction) if result.final_medium.dye is None else result.final_medium.dye
    _, _, _, degradation_fog_rgb = _material_degradation_state(film)
    degradation_fog_layers = _rgb_density_to_layer_proxy(
        degradation_fog_rgb.reshape(1, 1, 3), film
    )
    density_cmy = d_min + degradation_fog_layers + float(effective.d_min_shift)
    density_cmy = density_cmy + dye * density_range * float(effective.d_max_factor)
    auxiliary_density_rgb = np.asarray(
        result.final_medium.auxiliary_density_rgb,
        dtype=np.float32,
    ) * float(result.final_medium.auxiliary_remaining)
    density_cmy = density_cmy + _rgb_density_to_layer_proxy(
        auxiliary_density_rgb.reshape(1, 1, 3), film
    )

    silver_amount = np.mean(result.final_medium.metallic_silver, axis=-1)
    residual_amount = np.mean(result.final_medium.total_fixable_halide(), axis=-1)
    mean_range = float(np.mean(density_range))
    silver_density_rgb = silver_amount * mean_range * 0.72
    residual_density = residual_amount * mean_range * 0.12
    halide_color = np.asarray(
        result.final_medium.retained_halide_density_rgb,
        dtype=np.float32,
    ).reshape(1, 1, 3)
    residual_density_rgb = residual_density[..., None] * halide_color
    density_cmy = density_cmy + _rgb_density_to_layer_proxy(
        silver_density_rgb[..., None] + residual_density_rgb,
        film,
    )
    upper = d_max * 1.35 + 0.35
    density_cmy = np.clip(density_cmy, 0.0, upper).astype(np.float32)
    return ReducedColorDevelopment(
        density_cmy=density_cmy,
        process_result=result,
        effective_development=effective,
        latent_fraction=latent_fraction,
        silver_density_rgb=np.repeat(silver_density_rgb[..., None], 3, axis=-1).astype(
            np.float32
        ),
        residual_halide_density_rgb=residual_density_rgb.astype(np.float32),
        compatibility=compatibility,
    )


def develop_color_reversal_reduced(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
) -> ReducedColorDevelopment:
    """Form a dye positive from the halide remaining after first development."""
    effective = build_effective_development(recipe)
    latent_fraction = _material_latent_layers(image_linear, film, recipe, effective)
    zeros = np.zeros_like(latent_fraction, dtype=np.float32)
    state = FilmProcessState(
        halide=np.ones_like(latent_fraction, dtype=np.float32),
        developability=latent_fraction,
        metallic_silver=zeros,
        coupler=np.ones_like(latent_fraction, dtype=np.float32),
        dye=zeros.copy(),
        original_developability=None,
        auxiliary_remaining=float(np.clip(film.auxiliary_layer_amount, 0.0, 1.0)),
    )
    program = program_from_develop_recipe(
        recipe,
        mode="color_reversal",
        material_process="reversal",
        layer_count=3,
    )
    compatibility = _compatibility_for_program(film, program.key, 3)
    result = apply_process_program(
        _color_material(film),
        state,
        program,
        compatibility,
        consume_latent_state=True,
    )

    d_min = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)
    d_max = np.asarray(film.density_max, dtype=np.float32).reshape(1, 1, 3)
    density_range = np.maximum(d_max - d_min, 1e-6)
    dye = np.zeros_like(latent_fraction) if result.final_medium.dye is None else result.final_medium.dye
    dye = shape_positive_density_fraction(dye, film)
    saturation = max(float(getattr(film, "positive_dye_saturation", 1.0)), 0.0)
    if abs(saturation - 1.0) > 1e-6:
        neutral = dye.mean(axis=-1, keepdims=True)
        dye = np.clip(neutral + (dye - neutral) * saturation, 0.0, 1.0)
    _, _, _, degradation_fog_rgb = _material_degradation_state(film)
    degradation_fog_layers = _rgb_density_to_layer_proxy(
        degradation_fog_rgb.reshape(1, 1, 3), film
    )
    density_cmy = d_min + degradation_fog_layers + dye * density_range * float(effective.d_max_factor)
    auxiliary_density_rgb = np.asarray(
        result.final_medium.auxiliary_density_rgb,
        dtype=np.float32,
    ) * float(result.final_medium.auxiliary_remaining)
    density_cmy += _rgb_density_to_layer_proxy(
        auxiliary_density_rgb.reshape(1, 1, 3), film
    )

    silver_amount = np.mean(result.final_medium.metallic_silver, axis=-1)
    residual_amount = np.mean(result.final_medium.total_fixable_halide(), axis=-1)
    mean_range = float(np.mean(density_range))
    silver_density_rgb = silver_amount * mean_range * 0.72
    residual_density = residual_amount * mean_range * 0.12
    halide_color = np.asarray(
        result.final_medium.retained_halide_density_rgb,
        dtype=np.float32,
    ).reshape(1, 1, 3)
    residual_density_rgb = residual_density[..., None] * halide_color
    density_cmy += _rgb_density_to_layer_proxy(
        silver_density_rgb[..., None] + residual_density_rgb,
        film,
    )
    density_cmy = np.clip(density_cmy, 0.0, d_max * 1.35 + 0.35).astype(np.float32)
    return ReducedColorDevelopment(
        density_cmy=density_cmy,
        process_result=result,
        effective_development=effective,
        latent_fraction=latent_fraction,
        silver_density_rgb=np.repeat(silver_density_rgb[..., None], 3, axis=-1).astype(np.float32),
        residual_halide_density_rgb=residual_density_rgb.astype(np.float32),
        compatibility=compatibility,
    )
