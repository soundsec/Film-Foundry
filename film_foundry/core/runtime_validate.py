"""Runtime validation for develop, medium, and scan configs.

Numerical core owns these checks. Engine re-exports the same names so
existing callers and monkeypatches on film_foundry.core.engine
continue to intercept engine-site lookups.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import numpy as np

from film_foundry.contracts.spectral_exposure import SpectralExposureProfile
from film_foundry.contracts.spectral_optics import SpectralMaterialProfile, SpectralObservationProfile
from film_foundry.core.spectral_exposure import compile_spectral_exposure

from film_foundry.core.accidents import (
    EXPOSURE_OCCLUSION_AMOUNT_FIELDS,
    LIGHT_LEAK_CONFIG_TEMPLATE_KEYS,
    LIGHT_LEAK_ENTRY_SIDE_KEYS,
    LIGHT_LEAK_MIX_KEYS,
    LIGHT_LEAK_PLACEMENT_KEYS,
    LIGHT_LEAK_SECONDARY_TEMPLATE_KEYS,
)
from film_foundry.core.execution import resolve_execution_mode
from film_foundry.core.film_border import validate_film_border_config
from film_foundry.core.film_process.recipe import resolve_program_key
from film_foundry.core.light_piping import LIGHT_PIPING_EDGE_MODES
from film_foundry.core.media import config_medium_process, is_monochrome_material
from film_foundry.core.states import DevelopedNegative
from film_foundry.model.config import (
    DarkroomConfig,
    SCANNER_COLOR_RESPONSE_MODELS,
    normalize_scanner_color_curve_rgb,
)


def _validate_rgb_image_array(image: np.ndarray, label: str = "input image") -> np.ndarray:
    array = np.asarray(image)
    if array.ndim != 3 or array.shape[-1] != 3:
        raise ValueError(f"{label} must have shape HxWx3, got {array.shape}.")
    if array.shape[0] <= 0 or array.shape[1] <= 0:
        raise ValueError(f"{label} must have non-zero width and height, got {array.shape}.")
    # Both reductions propagate NaN/Inf but do not allocate an HxWx3 boolean
    # image.  This boundary is crossed repeatedly by 30--40 MP media, so the
    # validation working set must stay scalar without weakening the check.
    minimum = np.min(array)
    maximum = np.max(array)
    if not bool(np.isfinite(minimum)) or not bool(np.isfinite(maximum)):
        raise ValueError(f"{label} must contain only finite numeric values.")
    return array


def _validate_developed_medium_arrays(medium: DevelopedNegative) -> None:
    density_cmy = np.asarray(medium.density_cmy)
    density_grain = np.asarray(medium.density_grain)
    layer_sizes = (density_cmy.size, density_grain.size)
    layer_shape: tuple[int, int, int] | None = None
    if all(size > 0 for size in layer_sizes):
        density_cmy = _validate_rgb_image_array(
            density_cmy,
            "developed medium density_cmy",
        )
        density_grain = _validate_rgb_image_array(
            density_grain,
            "developed medium density_grain",
        )
        if density_cmy.shape != density_grain.shape:
            raise ValueError(
                "developed medium density_cmy and density_grain must have the same shape; "
                f"got {density_cmy.shape} and {density_grain.shape}"
            )
        layer_shape = density_grain.shape
    elif all(size == 0 for size in layer_sizes):
        if density_cmy.shape != (0, 0, 3) or density_grain.shape != (0, 0, 3):
            raise ValueError(
                "Unavailable developed-medium layer masters must use (0, 0, 3) sentinels."
            )
        storage = medium.metadata.get("stage_storage", {})
        if (
            medium.optical_density_rgb is None
            or not isinstance(storage, dict)
            or storage.get("profile") != "scan_optical_only_v1"
        ):
            raise ValueError(
                "Developed-medium layer masters cannot be absent without an "
                "authoritative scan-only optical master."
            )
    else:
        raise ValueError(
            "developed medium density_cmy and density_grain must either both be "
            "resident or both be unavailable."
        )
    if medium.optical_density_rgb is not None:
        optical = _validate_rgb_image_array(
            medium.optical_density_rgb,
            "developed medium optical_density_rgb",
        )
        if layer_shape is not None and optical.shape != layer_shape:
            raise ValueError(
                "developed medium optical_density_rgb must match density_grain; "
                f"got {optical.shape} and {layer_shape}"
            )

def _validate_numeric_config_group(group: str, values: dict[str, object]) -> None:
    for key, value in values.items():
        if group == "develop" and key == "process_steps":
            from film_foundry.contracts.process_steps import validate_process_steps
            validate_process_steps(value)
            continue
        if isinstance(value, bool) or isinstance(value, str) or value is None:
            continue
        if not isinstance(value, (int, float, tuple, list, np.ndarray, np.number)):
            continue
        try:
            array = np.asarray(value, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric {group} setting '{key}'.") from exc
        if not np.isfinite(array).all():
            raise ValueError(f"Non-finite {group} setting '{key}' is not allowed.")


def _validate_develop_runtime(
    config: DarkroomConfig,
    *,
    allow_discarded_history: bool = False,
) -> None:
    """Reject malformed material/process settings before forming latent state."""
    from film_foundry.core.incident_identity import validate_incident_seed
    validate_incident_seed(config.chemistry.incident_identity_seed)
    if config.film.spectral_exposure_profile is not None:
        profile = SpectralExposureProfile.from_value(config.film.spectral_exposure_profile)
        expected_layers = 1 if is_monochrome_material(config) else 3
        if profile.layer_count != expected_layers:
            raise ValueError("spectral_exposure_profile layer count does not match the material")
        compile_spectral_exposure(profile)
    if config.film.spectral_material_profile is not None:
        SpectralMaterialProfile.from_value(config.film.spectral_material_profile)
    for group, values in (
        ("film", asdict(config.film)),
        ("develop", asdict(config.chemistry)),
        ("develop look", {
            "exposure_ev": config.look.exposure_ev,
            "negative_contrast": config.look.negative_contrast,
            "saturation_multiplier": config.look.saturation_multiplier,
            "halation_multiplier": config.look.halation_multiplier,
            "halation_sensitivity": config.look.halation_sensitivity,
            "grain_multiplier": config.look.grain_multiplier,
            "grain_size_multiplier": config.look.grain_size_multiplier,
            "look_strength": config.look.look_strength,
            "emulsion_mtf_strength": config.look.emulsion_mtf_strength,
            "digital_artifact_suppression": config.look.digital_artifact_suppression,
            "halation_edge_compensation": config.look.halation_edge_compensation,
        }),
        ("processing", asdict(config.processing)),
    ):
        _validate_numeric_config_group(group, values)

    shaped_values = {
        "film.halation_color": (config.film.halation_color, (3,)),
        "film.halation_layer_return_weights": (
            config.film.halation_layer_return_weights,
            (3,),
        ),
        "film.halation_spread_scale_weights": (
            config.film.halation_spread_scale_weights,
            (3,),
        ),
        "film.emulsion_highlight_spread_layer_radius_scales": (
            config.film.emulsion_highlight_spread_layer_radius_scales,
            (3,),
        ),
        "film.emulsion_highlight_spread_layer_strengths": (
            config.film.emulsion_highlight_spread_layer_strengths,
            (3,),
        ),
        "film.light_piping_layer_weights": (
            config.film.light_piping_layer_weights,
            (3,),
        ),
        "film.color_matrix": (config.film.color_matrix, (3, 3)),
        "film.hd_gamma": (config.film.hd_gamma, (3,)),
        "film.density_min": (config.film.density_min, (3,)),
        "film.density_max": (config.film.density_max, (3,)),
        "film.log_exposure_toe": (config.film.log_exposure_toe, (3,)),
        "film.log_exposure_shoulder": (config.film.log_exposure_shoulder, (3,)),
        "film.extreme_exposure_reversal_start_loge": (
            config.film.extreme_exposure_reversal_start_loge,
            (3,),
        ),
        "film.layer_sensitivity_matrix": (config.film.layer_sensitivity_matrix, (3, 3)),
        "film.dye_absorption_matrix": (config.film.dye_absorption_matrix, (3, 3)),
        "film.base_dye_interaction_matrix": (config.film.base_dye_interaction_matrix, (3, 3)),
        "film.film_base_density_rgb": (config.film.film_base_density_rgb, (3,)),
        "film.clear_support_density_rgb": (config.film.clear_support_density_rgb, (3,)),
        "film.retained_halide_density_rgb": (config.film.retained_halide_density_rgb, (3,)),
        "film.auxiliary_layer_density_rgb": (config.film.auxiliary_layer_density_rgb, (3,)),
        "film.degradation_fog_density_rgb": (config.film.degradation_fog_density_rgb, (3,)),
        "film.degradation_layer_balance": (config.film.degradation_layer_balance, (3,)),
        "film.granularity_sigma": (config.film.granularity_sigma, (3,)),
        "film.cross_process_layer_balance": (config.film.cross_process_layer_balance, (3,)),
        "develop.process_layer_balance": (config.chemistry.process_layer_balance, (3,)),
    }
    for name, (value, expected_shape) in shaped_values.items():
        try:
            array = np.asarray(value, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric develop setting '{name}'.") from exc
        if array.shape != expected_shape:
            raise ValueError(
                f"Develop setting '{name}' must have shape {expected_shape}, got {array.shape}."
            )
        if not np.isfinite(array).all():
            raise ValueError(f"Non-finite develop setting '{name}' is not allowed.")

    light_leak_template = str(config.chemistry.light_leak_template).strip().lower()
    if light_leak_template not in LIGHT_LEAK_CONFIG_TEMPLATE_KEYS:
        raise ValueError(
            "develop.light_leak_template must be one of: "
            + ", ".join(LIGHT_LEAK_CONFIG_TEMPLATE_KEYS)
        )
    light_leak_secondary = str(
        config.chemistry.light_leak_secondary_template
    ).strip().lower()
    if light_leak_secondary not in LIGHT_LEAK_SECONDARY_TEMPLATE_KEYS:
        raise ValueError(
            "develop.light_leak_secondary_template must be one of: "
            + ", ".join(LIGHT_LEAK_SECONDARY_TEMPLATE_KEYS)
        )
    light_leak_mix = str(config.chemistry.light_leak_mix).strip().lower()
    if light_leak_mix not in LIGHT_LEAK_MIX_KEYS:
        raise ValueError(
            "develop.light_leak_mix must be one of: "
            + ", ".join(LIGHT_LEAK_MIX_KEYS)
        )
    light_leak_placement = str(config.chemistry.light_leak_placement).strip().lower()
    if light_leak_placement not in LIGHT_LEAK_PLACEMENT_KEYS:
        raise ValueError(
            "develop.light_leak_placement must be one of: "
            + ", ".join(LIGHT_LEAK_PLACEMENT_KEYS)
        )
    light_leak_entry_side = str(config.chemistry.light_leak_entry_side).strip().lower()
    if light_leak_entry_side not in LIGHT_LEAK_ENTRY_SIDE_KEYS:
        raise ValueError(
            "develop.light_leak_entry_side must be one of: "
            + ", ".join(LIGHT_LEAK_ENTRY_SIDE_KEYS)
        )
    if not np.isfinite(float(config.chemistry.light_leak_center_x)) or not (
        0.0 <= float(config.chemistry.light_leak_center_x) <= 1.0
    ):
        raise ValueError("develop.light_leak_center_x must be between zero and one.")
    if not np.isfinite(float(config.chemistry.light_leak_center_y)) or not (
        0.0 <= float(config.chemistry.light_leak_center_y) <= 1.0
    ):
        raise ValueError("develop.light_leak_center_y must be between zero and one.")
    if not np.isfinite(float(config.chemistry.light_leak_rotation_degrees)):
        raise ValueError("develop.light_leak_rotation_degrees must be finite.")
    identity_seed = config.chemistry.light_leak_identity_seed
    if identity_seed is not None:
        try:
            identity_seed = int(identity_seed)
        except (TypeError, ValueError) as exc:
            raise ValueError("develop.light_leak_identity_seed must be an integer or None.") from exc
        if identity_seed < 0:
            raise ValueError("develop.light_leak_identity_seed must be non-negative.")
    occlusion_identity = config.chemistry.exposure_occlusion_identity_seed
    if occlusion_identity is not None:
        try:
            occlusion_identity = int(occlusion_identity)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "develop.exposure_occlusion_identity_seed must be an integer or None."
            ) from exc
        if occlusion_identity < 0:
            raise ValueError("develop.exposure_occlusion_identity_seed must be non-negative.")
    mask_path = str(config.chemistry.light_leak_mask_path).strip()
    if mask_path and not Path(mask_path).is_file():
        raise ValueError(f"develop.light_leak_mask_path does not exist: {mask_path}")
    if (
        light_leak_mix == "replace"
        and not mask_path
        and float(config.chemistry.light_leak_strength) > 1e-6
    ):
        raise ValueError("develop.light_leak_mix='replace' requires light_leak_mask_path.")

    halation_return_model = str(config.film.halation_return_model).strip().lower()
    if halation_return_model not in {"compatibility_rgb", "layer_selective"}:
        raise ValueError(
            "film.halation_return_model must be 'compatibility_rgb' or "
            "'layer_selective'."
        )
    layer_return_weights = np.asarray(
        config.film.halation_layer_return_weights,
        dtype=np.float64,
    )
    if np.any(layer_return_weights < 0.0):
        raise ValueError("film.halation_layer_return_weights must be non-negative.")
    if float(config.film.halation_strength) < 0.0:
        raise ValueError("film.halation_strength must be non-negative.")
    spread_scale_weights = np.asarray(
        config.film.halation_spread_scale_weights,
        dtype=np.float64,
    )
    if np.any(spread_scale_weights < 0.0) or float(spread_scale_weights.sum()) <= 0.0:
        raise ValueError(
            "film.halation_spread_scale_weights must be non-negative and "
            "contain a positive value."
        )
    if (
        halation_return_model == "layer_selective"
        and float(spread_scale_weights[2]) > 0.0
        and float(config.film.halation_outer_radius)
        < float(config.film.halation_exponential_radius)
    ):
        raise ValueError(
            "layer-selective wide halation requires halation_outer_radius to "
            "be at least halation_exponential_radius."
        )
    if halation_return_model == "layer_selective" and not any(
        (
            _uses_reduced_bw_negative(config),
            _uses_reduced_color_negative(config),
            _uses_reduced_bw_reversal(config),
            _uses_reduced_color_reversal(config),
        )
    ):
        raise ValueError(
            "layer-selective halation requires a unified silver-halide "
            "material-pool process program"
        )

    emulsion_spread_strength = float(config.film.emulsion_highlight_spread_strength)
    emulsion_spread_radius = float(config.film.emulsion_highlight_spread_radius)
    emulsion_spread_threshold = float(config.film.emulsion_highlight_spread_threshold)
    emulsion_spread_softness = float(config.film.emulsion_highlight_spread_softness)
    if emulsion_spread_strength < 0.0:
        raise ValueError(
            "film.emulsion_highlight_spread_strength must be non-negative."
        )
    if emulsion_spread_radius <= 0.0:
        raise ValueError("film.emulsion_highlight_spread_radius must be positive.")
    if emulsion_spread_threshold < 0.0:
        raise ValueError(
            "film.emulsion_highlight_spread_threshold must be non-negative."
        )
    if emulsion_spread_softness <= 0.0:
        raise ValueError("film.emulsion_highlight_spread_softness must be positive.")
    if np.any(
        np.asarray(
            config.film.emulsion_highlight_spread_layer_radius_scales,
            dtype=np.float64,
        )
        <= 0.0
    ):
        raise ValueError(
            "film.emulsion_highlight_spread_layer_radius_scales must be positive."
        )
    if np.any(
        np.asarray(
            config.film.emulsion_highlight_spread_layer_strengths,
            dtype=np.float64,
        )
        < 0.0
    ):
        raise ValueError(
            "film.emulsion_highlight_spread_layer_strengths must be non-negative."
        )
    if emulsion_spread_strength > 0.0 and not any(
        (
            _uses_reduced_bw_negative(config),
            _uses_reduced_color_negative(config),
            _uses_reduced_bw_reversal(config),
            _uses_reduced_color_reversal(config),
        )
    ):
        raise ValueError(
            "emulsion highlight spread requires a unified silver-halide "
            "material-pool process program"
        )

    light_piping_mode = str(config.film.light_piping_edge_mode).strip().lower()
    if light_piping_mode not in LIGHT_PIPING_EDGE_MODES:
        raise ValueError(
            "film.light_piping_edge_mode must declare a supported frame edge mode."
        )
    if float(config.film.light_piping_strength) < 0.0:
        raise ValueError("film.light_piping_strength must be non-negative.")
    if float(config.film.light_piping_depth) <= 0.0:
        raise ValueError("film.light_piping_depth must be positive.")
    light_piping_weights = np.asarray(
        config.film.light_piping_layer_weights,
        dtype=np.float64,
    )
    if np.any(light_piping_weights < 0.0):
        raise ValueError("film.light_piping_layer_weights must be non-negative.")
    if (
        float(config.film.light_piping_strength) > 0.0
        and light_piping_mode != "none"
        and not any(
            (
                _uses_reduced_bw_negative(config),
                _uses_reduced_color_negative(config),
                _uses_reduced_bw_reversal(config),
                _uses_reduced_color_reversal(config),
            )
        )
    ):
        raise ValueError(
            "material light piping requires a unified silver-halide material-pool program"
        )
    if (
        float(config.chemistry.development_adjacency_strength) > 0.0
        and not any(
            (
                _uses_reduced_bw_negative(config),
                _uses_reduced_color_negative(config),
                _uses_reduced_bw_reversal(config),
                _uses_reduced_color_reversal(config),
            )
        )
    ):
        raise ValueError(
            "development adjacency requires a unified silver-halide material-pool program"
        )
    dir_matrix = np.asarray(
        config.film.dir_material_response_matrix,
        dtype=np.float64,
    )
    if dir_matrix.shape != (3, 3) or not np.all(np.isfinite(dir_matrix)):
        raise ValueError("film.dir_material_response_matrix must be a finite 3x3 matrix.")
    if np.any(dir_matrix < 0.0) or np.any(np.sum(dir_matrix, axis=0) > 0.95 + 1e-6):
        raise ValueError(
            "film.dir_material_response_matrix must be non-negative with target columns summing to at most 0.95."
        )
    dir_radius = float(config.film.dir_diffusion_radius)
    if not np.isfinite(dir_radius) or dir_radius <= 0.0:
        raise ValueError("film.dir_diffusion_radius must be finite and positive.")
    dir_process_effectiveness = config.chemistry.dir_process_effectiveness
    if dir_process_effectiveness is not None and (
        not np.isfinite(float(dir_process_effectiveness))
        or not 0.0 <= float(dir_process_effectiveness) <= 1.0
    ):
        raise ValueError(
            "chemistry.dir_process_effectiveness must be between zero and one or None."
        )
    if np.any(dir_matrix > 0.0):
        if is_monochrome_material(config):
            raise ValueError(
                "DIR material response requires a color coupler material"
            )
        if not any(
            (
                _uses_reduced_color_negative(config),
                _uses_reduced_color_reversal(config),
            )
        ):
            raise ValueError(
                "DIR material response requires a unified color material-pool program"
            )

    d_min = np.asarray(config.film.density_min, dtype=np.float64)
    d_max = np.asarray(config.film.density_max, dtype=np.float64)
    if np.any(d_max <= d_min):
        raise ValueError("film.density_max must be greater than film.density_min in every layer.")
    if not 0.0 <= float(config.film.extreme_exposure_reversal_strength) <= 1.0:
        raise ValueError(
            "film.extreme_exposure_reversal_strength must be between zero and one."
        )
    if float(config.film.extreme_exposure_reversal_width) <= 0.0:
        raise ValueError("film.extreme_exposure_reversal_width must be positive.")
    if not 0.0 <= float(config.chemistry.development_adjacency_strength) <= 1.0:
        raise ValueError(
            "develop.development_adjacency_strength must be between zero and one."
        )
    if float(config.chemistry.development_adjacency_radius) <= 0.0:
        raise ValueError("develop.development_adjacency_radius must be positive.")
    if not 0.0 <= float(config.chemistry.dye_damage_strength) <= 1.0:
        raise ValueError("develop.dye_damage_strength must be between zero and one.")
    for occlusion_key in EXPOSURE_OCCLUSION_AMOUNT_FIELDS:
        value = float(getattr(config.chemistry, occlusion_key))
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"develop.{occlusion_key} must be between zero and one.")
    validate_film_border_config(config.film_border)
    if (
        float(config.chemistry.dye_damage_strength) > 0.0
        and not any(
            (
                _uses_reduced_color_negative(config),
                _uses_reduced_color_reversal(config),
            )
        )
    ):
        raise ValueError(
            "explicit dye damage requires a unified color material-pool program"
        )
    if len(config.film.grain_scales) == 0 or len(config.film.grain_scales) != len(config.film.grain_scale_weights):
        raise ValueError("film.grain_scales and film.grain_scale_weights must be non-empty and have equal length.")
    if float(config.film.silver_grain_strength) < 0.0:
        raise ValueError("film.silver_grain_strength must be non-negative.")
    if float(config.film.silver_grain_radius) <= 0.0:
        raise ValueError("film.silver_grain_radius must be positive.")
    if not 0.0 <= float(config.film.silver_grain_clump_mix) <= 1.0:
        raise ValueError("film.silver_grain_clump_mix must be between 0 and 1.")
    if config.random_seed is not None:
        if isinstance(config.random_seed, bool) or not isinstance(config.random_seed, (int, np.integer)):
            raise ValueError("random_seed must be an integer or None.")
    if str(config.seed_strategy).strip().lower() not in {"random", "fixed", "path"}:
        raise ValueError("seed_strategy must be 'random', 'fixed', or 'path'.")
    if str(config.processing.quality_mode).strip().lower() not in {
        "draft", "preview", "standard", "high", "full", "native"
    }:
        raise ValueError("processing.quality_mode is not recognized.")
    resolve_execution_mode(config)
    comfort_zone = float(config.processing.comfort_zone_megapixels)
    if not np.isfinite(comfort_zone) or comfort_zone <= 0.0:
        raise ValueError(
            "processing.comfort_zone_megapixels must be finite and positive."
        )
    tile_rows = int(config.processing.material_tile_rows)
    if tile_rows < 0:
        raise ValueError("processing.material_tile_rows must be zero or positive.")
    tile_threshold = float(config.processing.material_tile_threshold_megapixels)
    if not np.isfinite(tile_threshold) or tile_threshold <= 0.0:
        raise ValueError(
            "processing.material_tile_threshold_megapixels must be finite and positive."
        )
    scan_tile_rows = int(config.processing.scan_tile_rows)
    if scan_tile_rows < 0:
        raise ValueError("processing.scan_tile_rows must be zero or positive.")
    scan_tile_threshold = float(config.processing.scan_tile_threshold_megapixels)
    if not np.isfinite(scan_tile_threshold) or scan_tile_threshold <= 0.0:
        raise ValueError(
            "processing.scan_tile_threshold_megapixels must be finite and positive."
        )
    native_thread_limit = int(config.processing.native_thread_limit)
    if native_thread_limit < 0:
        raise ValueError(
            "processing.native_thread_limit must be zero or positive."
        )
    adjacency_edge = config.processing.adjacency_work_long_edge
    if adjacency_edge is not None and int(adjacency_edge) <= 0:
        raise ValueError(
            "processing.adjacency_work_long_edge must be positive or None."
        )
    dir_edge = config.processing.dir_work_long_edge
    if dir_edge is not None and int(dir_edge) <= 0:
        raise ValueError("processing.dir_work_long_edge must be positive or None.")
    emulsion_edge = config.processing.emulsion_spread_work_long_edge
    if emulsion_edge is not None and int(emulsion_edge) <= 0:
        raise ValueError(
            "processing.emulsion_spread_work_long_edge must be positive or None."
        )
    history_policies = {"full", "cold_fp16"}
    if allow_discarded_history:
        history_policies.add("discard")
    if str(config.processing.history_storage_policy).strip().lower() not in history_policies:
        raise ValueError(
            "processing.history_storage_policy must be 'full' or 'cold_fp16'."
        )


def _validate_output_runtime(config: DarkroomConfig) -> None:
    _validate_numeric_config_group("output", asdict(config.output))
    if int(config.output.bit_depth) not in {8, 16}:
        raise ValueError(f"Unsupported output bit depth {config.output.bit_depth}; expected 8 or 16.")
    if str(config.output.format).strip().lower() not in {"png", "jpg", "jpeg", "tif", "tiff", "webp"}:
        raise ValueError(f"Unsupported output format: {config.output.format!r}.")
    if str(config.output.medium_npz_compression).strip().lower() not in {
        "compressed", "store"
    }:
        raise ValueError(
            "output.medium_npz_compression must be 'compressed' or 'store'."
        )
    for key in ("render_long_edge", "preview_long_edge"):
        edge = getattr(config.output, key)
        if edge is not None and int(edge) <= 0:
            raise ValueError(f"output.{key} must be positive or None.")
    if int(config.output.encode_tile_rows) < 0:
        raise ValueError("output.encode_tile_rows must be zero or positive.")
    encode_threshold = float(config.output.encode_tile_threshold_megapixels)
    if not np.isfinite(encode_threshold) or encode_threshold <= 0.0:
        raise ValueError(
            "output.encode_tile_threshold_megapixels must be finite and positive."
        )
    budget = config.processing.memory_budget_mb
    if budget is not None and (not np.isfinite(float(budget)) or float(budget) <= 0.0):
        raise ValueError("processing.memory_budget_mb must be finite and positive or None.")
    if str(config.processing.memory_budget_policy).strip().lower() not in {
        "allow", "warn", "error"
    }:
        raise ValueError(
            "processing.memory_budget_policy must be 'allow', 'warn', or 'error'."
        )


def _validate_developed_medium_state(medium: DevelopedNegative) -> None:
    """Validate every persisted full-resolution stage and shared geometry."""
    history_arrays = {
        "linear_input": medium.linear_input,
        "after_mtf": medium.after_mtf,
        "after_halation": medium.after_halation,
    }
    runtime_config = medium.metadata.get("runtime_config")
    history_policy = (
        str(runtime_config.processing.history_storage_policy).strip().lower()
        if isinstance(runtime_config, DarkroomConfig)
        else "full"
    )
    stage_storage = medium.metadata.get("stage_storage", {})
    history_declared_unavailable = (
        isinstance(stage_storage, dict)
        and stage_storage.get("history") == "unavailable"
    )
    history_is_discarded = all(np.asarray(value).size == 0 for value in history_arrays.values())
    if history_is_discarded and (
        history_policy == "discard" or history_declared_unavailable
    ):
        for key, value in history_arrays.items():
            array = np.asarray(value)
            if array.shape != (0, 0, 3):
                raise ValueError(
                    f"Discarded developed-medium history {key} must have shape "
                    f"(0, 0, 3), got {array.shape}."
                )
            if not np.isfinite(array).all():
                raise ValueError(
                    f"Discarded developed-medium history {key} must be finite."
                )
        arrays = {}
    else:
        arrays = dict(history_arrays)
    _validate_developed_medium_arrays(medium)
    if medium.layer_masters_available:
        arrays.update({
            "density_cmy": medium.density_cmy,
            "density_grain": medium.density_grain,
        })
    if medium.optical_density_rgb is not None:
        arrays["optical_density_rgb"] = medium.optical_density_rgb
    validated = {
        key: _validate_rgb_image_array(value, f"developed medium {key}")
        for key, value in arrays.items()
    }
    shapes = {array.shape for array in validated.values()}
    if len(shapes) != 1:
        details = ", ".join(f"{key}={value.shape}" for key, value in validated.items())
        raise ValueError(f"Developed medium arrays must share one HxWx3 shape; got {details}.")


def _validate_scan_runtime(config: DarkroomConfig) -> None:
    """Reject malformed scan settings before they can create silent NaN output."""
    if config.scanner.spectral_observation_profile is not None:
        SpectralObservationProfile.from_value(config.scanner.spectral_observation_profile)
    scalar_groups = {
        "scanner": asdict(config.scanner),
        "scan look": {
            "print_contrast": config.look.print_contrast,
            "print_exposure_ev": config.look.print_exposure_ev,
        },
    }
    for group, values in scalar_groups.items():
        _validate_numeric_config_group(group, values)

    shaped_values = {
        "scanner.scanner_response_matrix": (config.scanner.scanner_response_matrix, (3, 3)),
        "scanner.negative_channel_matrix": (config.scanner.negative_channel_matrix, (3, 3)),
        "scanner.negative_channel_gamma": (config.scanner.negative_channel_gamma, (3,)),
        "film.dye_absorption_matrix": (config.film.dye_absorption_matrix, (3, 3)),
        "film.base_dye_interaction_matrix": (config.film.base_dye_interaction_matrix, (3, 3)),
        "film.film_base_density_rgb": (config.film.film_base_density_rgb, (3,)),
        "film.clear_support_density_rgb": (config.film.clear_support_density_rgb, (3,)),
        "film.density_min": (config.film.density_min, (3,)),
        "film.density_max": (config.film.density_max, (3,)),
    }
    for name, (value, expected_shape) in shaped_values.items():
        try:
            array = np.asarray(value, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric scan setting '{name}'.") from exc
        if array.shape != expected_shape:
            raise ValueError(
                f"Scan setting '{name}' must have shape {expected_shape}, got {array.shape}."
            )
        if not np.isfinite(array).all():
            raise ValueError(f"Non-finite scan setting '{name}' is not allowed.")

    enum_values = {
        "scanner.scan_method": (
            config.scanner.scan_method,
            {
                "negative_inversion",
                "positive_transparency",
                "direct_transmission",
                "legacy_density_mapping",
            },
        ),
        "scanner.print_mapping_mode": (
            config.scanner.print_mapping_mode,
            {"printlike", "sigmoid"},
        ),
        "scanner.scan_normalize_mode": (
            config.scanner.scan_normalize_mode,
            {"luma", "rgb"},
        ),
        "scanner.color_response_model": (
            config.scanner.color_response_model,
            set(SCANNER_COLOR_RESPONSE_MODELS),
        ),
    }
    for name, (value, accepted) in enum_values.items():
        normalized = str(value).strip().lower()
        if normalized not in accepted:
            expected = ", ".join(sorted(accepted))
            raise ValueError(f"Scan setting '{name}' must be one of: {expected}.")

    base_percentile = float(config.scanner.scan_base_percentile)
    black_percentile = float(config.scanner.scan_black_percentile)
    white_percentile = float(config.scanner.scan_white_percentile)
    if not 0.0 <= base_percentile <= 100.0:
        raise ValueError("scanner.scan_base_percentile must be between 0 and 100.")
    if not 0.0 <= black_percentile < white_percentile <= 100.0:
        raise ValueError(
            "scanner scan percentiles must satisfy "
            "0 <= scan_black_percentile < scan_white_percentile <= 100."
        )
    if float(config.scanner.negative_backlight_temperature_k) <= 0.0:
        raise ValueError("scanner.negative_backlight_temperature_k must be positive.")
    if float(config.scanner.light_table_temperature_k) <= 0.0:
        raise ValueError("scanner.light_table_temperature_k must be positive.")
    if float(config.scanner.print_gamma) <= 0.0:
        raise ValueError("scanner.print_gamma must be positive.")
    normalize_scanner_color_curve_rgb(config.scanner.color_curve_rgb)
    normalize_scanner_color_curve_rgb(
        config.scanner.device_response_rgb,
        field_name="scanner.device_response_rgb",
    )


def _resolved_process_program_key(config: DarkroomConfig) -> str:
    return resolve_program_key(
        config.chemistry,
        mode=config.mode,
        material_process=config_medium_process(config),
    )


def _uses_reduced_bw_negative(config: DarkroomConfig) -> bool:
    return _resolved_process_program_key(config) == "bw_negative"


def _uses_reduced_color_negative(config: DarkroomConfig) -> bool:
    key = _resolved_process_program_key(config)
    return key in {
        "color_negative",
        "color_negative_bleach_bypass",
    }


def _uses_reduced_bw_reversal(config: DarkroomConfig) -> bool:
    key = _resolved_process_program_key(config)
    return key == "bw_reversal"


def _uses_reduced_color_reversal(config: DarkroomConfig) -> bool:
    key = _resolved_process_program_key(config)
    return key == "color_reversal"
