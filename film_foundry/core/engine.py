"""Single-image Film Foundry processing engine."""

from __future__ import annotations

import copy
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np

from film_foundry.core.accidents import (
    EXPOSURE_OCCLUSION_AMOUNT_FIELDS,
    LIGHT_LEAK_CONFIG_TEMPLATE_KEYS,
    LIGHT_LEAK_ENTRY_SIDE_KEYS,
    LIGHT_LEAK_MIX_KEYS,
    LIGHT_LEAK_PLACEMENT_KEYS,
    LIGHT_LEAK_SECONDARY_TEMPLATE_KEYS,
    apply_density_accident_components,
    apply_exposure_occlusion_to_linear,
    apply_light_leak_to_exposure,
    compose_density_accident_master,
    resolved_light_leak_template,
    resolve_light_leak_pattern,
    sample_light_leak_pattern,
    uneven_development_rate_field,
)
from film_foundry.core.atomic_io import atomic_path_set, atomic_savez, atomic_write_json
from film_foundry.core.color import linear_to_srgb, luminance, srgb_to_linear
from film_foundry.core.density_grain import apply_density_grain
from film_foundry.core.development import build_effective_development
from film_foundry.core.development_adjacency import (
    build_development_adjacency_field,
)
from film_foundry.core.dir_interimage import build_dir_material_response_field
from film_foundry.core.emulsion_spread import (
    COMPACT_EMULSION_HIGHLIGHT_SPREAD_PLAN,
    emulsion_highlight_spread_field,
)
from film_foundry.core.film_border import (
    apply_edge_imprint_to_linear,
    validate_film_border_config,
)
from film_foundry.core.halation import apply_halation, halation_return_field
from film_foundry.core.implementation_plan import resolve_implementation_plan
from film_foundry.core.io_utils import load_image_with_color, probe_image_dimensions, save_image
from film_foundry.core.light_piping import (
    LIGHT_PIPING_EDGE_MODES,
    REDUCED_LIGHT_PIPING_PLAN,
    light_piping_exposure_field,
)
from film_foundry.core.electronic_negative import (
    export_layer_pack,
    export_plate_set,
    export_transparent_plate_set,
    save_linear_rgb_tiff,
    scanner_raw_export_border_width,
    scanner_raw_with_reference_border,
    scanner_raw_with_clear_border,
)
from film_foundry.core.execution import (
    processing_long_edge,
    resolve_execution_axes,
    resolve_execution_mode,
    uses_reduced_implementation,
)
from film_foundry.core.execution_topology import reference_execution_enabled
from film_foundry.core.film_process.integration import (
    develop_bw_negative_reduced,
    develop_bw_reversal_reduced,
    develop_color_negative_reduced,
    develop_color_reversal_reduced,
    shape_positive_dye_chroma,
)
from film_foundry.core.film_process.recipe import (
    process_program_payload,
    program_from_develop_recipe,
    resolve_program_key,
)
from film_foundry.core.media import (
    config_medium_process,
    developed_medium_contract,
    is_monochrome_material,
    is_monochrome_process,
)
from film_foundry.core.media_registry import get_develop_pipeline, get_scan_pipeline, install_default_media_pipelines
from film_foundry.core.runtime_validate import (
    _resolved_process_program_key,
    _uses_reduced_bw_negative,
    _uses_reduced_bw_reversal,
    _uses_reduced_color_negative,
    _uses_reduced_color_reversal,
    _validate_developed_medium_arrays,
    _validate_developed_medium_state,
    _validate_develop_runtime,
    _validate_numeric_config_group,
    _validate_output_runtime,
    _validate_rgb_image_array,
    _validate_scan_runtime,
)
from film_foundry.core.mtf import apply_emulsion_mtf
from film_foundry.core.preview import (
    negative_visual_preview,
    optical_density_visual_preview,
    resize_to_long_edge,
)
from film_foundry.core.resource_planning import (
    enforce_memory_budget,
    estimate_pipeline_memory,
    exact_material_tiling_required,
    warn_outside_comfort_zone,
)
from film_foundry.core.runtime_resources import configure_native_thread_limit
from film_foundry.core.spatial_fields import (
    LAZY_LAYER_EXPOSURE_FIELD_TYPES,
    LayerExposureAdditionField,
    StepDevelopmentRateField,
    combine_layer_exposure_addition_fields,
)
from film_foundry.core.silver_grain import (
    SilverGrainPlan,
    apply_metallic_silver_grain,
)
from film_foundry.core.provenance import (
    apply_scanner_raw_border_watermark,
    payload_with_config,
    provenance_npz_array,
    provenance_payload,
)
from film_foundry.core.scanner import (
    apply_scan_normalization_range,
    balance_negative_base,
    capture_optical_density,
    estimate_negative_base_transmittance,
    invert_negative_image,
    negative_scanner_compensation_matrix,
    negative_total_density_rgb,
    normalize_scan_rgb,
    reconstruct_negative_channels,
    render_negative_image,
    render_positive_scan,
    render_positive_transparency_scan,
    render_transparency_image,
    scan_negative_raw,
    scanner_raw_to_positive_rgb,
    scan_normalization_range,
    transmission_illuminant_rgb,
)
from film_foundry.core.sensitometry import exposure_to_density
from film_foundry.core.sidecar import (
    developed_negative_sidecar,
    final_positive_sidecar,
    layer_pack_metadata,
    scanner_raw_sidecar,
    transmission_raw_source_kind,
)
from film_foundry.core.states import (
    DevelopedNegative,
    ScannedPositive,
    ScanOutput,
    developed_medium_metadata,
)
from film_foundry.core.subtractive import density_to_positive_rgb
from film_foundry.model.config import DarkroomConfig


_RETAIN_FORMATION_LAYER_MASTERS: ContextVar[bool] = ContextVar(
    "film_foundry_retain_formation_layer_masters",
    default=True,
)


NEGATIVE_MATERIAL_DIR_NAME = "negatives"
POSITIVE_MATERIAL_DIR_NAME = "positives"
NEGATIVE_SUFFIX = ".darkroom_negative.npz"
POSITIVE_SUFFIX = ".darkroom_positive.npz"


def seed_from_path(path: str | Path, base_seed: int = 0) -> int:
    """由文件路径派生稳定 seed：同图可复现，不同图不共用颗粒纹理。"""
    payload = str(Path(path)).encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return (int.from_bytes(digest, "little") + int(base_seed)) % (2**32)


def _rng_for_input(input_path: str | Path | None, config: DarkroomConfig) -> tuple[np.random.Generator, int | None]:
    strategy = str(config.seed_strategy).lower()
    if strategy == "fixed":
        seed = 0 if config.random_seed is None else int(config.random_seed)
        return np.random.default_rng(seed), seed
    if strategy == "path" and input_path is not None:
        seed = seed_from_path(input_path, 0 if config.random_seed is None else int(config.random_seed))
        return np.random.default_rng(seed), seed
    # Random remains different for every run, but material/accident fields
    # must still be auditable afterwards.  Persist the concrete entropy that
    # seeded this run instead of saving an irreproducible null marker.
    seed = int(np.random.SeedSequence().generate_state(1, dtype=np.uint32)[0])
    return np.random.default_rng(seed), seed


def _density_preview(density_cmy: np.ndarray, config: DarkroomConfig) -> np.ndarray:
    d_min = np.asarray(config.film.density_min, dtype=np.float32)
    d_max = np.asarray(config.film.density_max, dtype=np.float32)
    return np.clip((density_cmy - d_min) / np.maximum(d_max - d_min, 1e-6), 0.0, 1.0)


def _rgb_density_preview(density_rgb: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(density_rgb, dtype=np.float32) / 3.0, 0.0, 1.0)


def _scale_dye_selectivity(matrix, selectivity: float):
    """调节染料吸收矩阵的选择性，而不是在最终 RGB 上直接拉饱和。"""
    selectivity = max(0.0, float(selectivity))
    rows = []
    for row in matrix:
        neutral = sum(float(v) for v in row) / 3.0
        rows.append(tuple(max(0.0, neutral + (float(v) - neutral) * selectivity) for v in row))
    return tuple(rows)


def _apply_look_strength(config: DarkroomConfig) -> None:
    """把一个总控强度映射到明确物理参数，避免直接 RGB 混合。"""
    look = config.look
    strength = float(look.look_strength)
    if abs(strength - 1.0) >= 1e-6:
        config.film.halation_strength *= strength
        config.film.granularity_sigma = tuple(float(v) * strength for v in config.film.granularity_sigma)
        config.film.silver_grain_strength *= strength
        gamma_gain = 1.0 + (strength - 1.0) * 0.35
        config.film.hd_gamma = tuple(float(v) * gamma_gain for v in config.film.hd_gamma)

    config.film.hd_gamma = tuple(float(v) * float(look.negative_contrast) for v in config.film.hd_gamma)
    config.film.dye_absorption_matrix = _scale_dye_selectivity(
        config.film.dye_absorption_matrix,
        float(look.saturation_multiplier),
    )
    config.film.halation_strength *= float(look.halation_multiplier)
    sensitivity = float(np.clip(look.halation_sensitivity, -1.0, 1.0))
    config.film.halation_threshold = float(np.clip(config.film.halation_threshold - 0.22 * sensitivity, 0.05, 1.25))
    config.film.granularity_sigma = tuple(
        float(v) * float(look.grain_multiplier) for v in config.film.granularity_sigma
    )
    config.film.grain_density_correlation_radius *= max(float(look.grain_size_multiplier), 0.05)
    config.film.silver_grain_strength *= float(look.grain_multiplier)
    config.film.silver_grain_radius *= max(float(look.grain_size_multiplier), 0.05)

    if look.emulsion_mtf_strength is not None:
        config.film.emulsion_mtf_strength = float(look.emulsion_mtf_strength)
    if look.digital_artifact_suppression is not None:
        config.film.digital_artifact_suppression = float(look.digital_artifact_suppression)
    if look.halation_edge_compensation is not None:
        config.film.halation_gradient_suppression = float(look.halation_edge_compensation)


def _force_bw_negative(config: DarkroomConfig, *, include_scanner: bool = False) -> None:
    # Material normalization is part of development. Scanner neutralization is
    # opt-in and is only requested after a monochrome final medium is observed.
    """黑白负片模式：把三层参数同步成单一银盐密度响应，避免彩色染料色偏。"""
    program_key = str(config.chemistry.program_key).strip().lower()
    legacy_bw_mode = program_key in {"", "auto"} and is_monochrome_process(config)
    if not is_monochrome_material(config) and not legacy_bw_mode:
        return
    lum = (0.2126, 0.7152, 0.0722)
    config.film.layer_sensitivity_matrix = (lum, lum, lum)
    config.film.dye_absorption_matrix = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    config.film.hd_gamma = tuple([float(np.mean(config.film.hd_gamma))] * 3)
    config.film.density_min = tuple([float(np.mean(config.film.density_min))] * 3)
    config.film.density_max = tuple([float(np.mean(config.film.density_max))] * 3)
    config.film.log_exposure_toe = tuple([float(np.mean(config.film.log_exposure_toe))] * 3)
    config.film.log_exposure_shoulder = tuple([float(np.mean(config.film.log_exposure_shoulder))] * 3)
    config.film.granularity_sigma = tuple([float(np.mean(config.film.granularity_sigma))] * 3)
    config.film.film_base_density_rgb = tuple([float(np.mean(config.film.film_base_density_rgb))] * 3)
    if not include_scanner:
        return
    config.scanner.print_reference_density = tuple([float(np.mean(config.scanner.print_reference_density))] * 3)
    config.scanner.scanner_light_color = (1.0, 1.0, 1.0)
    config.scanner.scanner_response_matrix = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    config.scanner.negative_channel_matrix = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    config.scanner.negative_channel_gamma = (1.0, 1.0, 1.0)
    config.scanner.negative_channel_compensation_enabled = False
    config.scanner.print_color_shift = (0.0, 0.0, 0.0)
    config.scanner.print_color_bias = (1.0, 1.0, 1.0)
    config.scanner.highlight_color_bias = (1.0, 1.0, 1.0)
    config.scanner.scan_normalize_mode = "luma"


def _density_is_monochrome(density: np.ndarray, tolerance: float = 1e-5) -> bool:
    """识别由黑白负片流程保存的三通道等值密度母版。"""
    density = np.asarray(density, dtype=np.float32)
    if density.ndim != 3 or density.shape[-1] != 3:
        return False
    rg = float(np.max(np.abs(density[..., 0] - density[..., 1])))
    rb = float(np.max(np.abs(density[..., 0] - density[..., 2])))
    return max(rg, rb) <= float(tolerance)


def _developed_medium_is_monochrome(medium: DevelopedNegative) -> bool:
    """Use the final-medium contract, with a legacy layer-data fallback."""
    color_system = str(getattr(medium, "color_system", "")).strip().lower()
    if color_system in {"silver_bw", "silver_on_color_material", "monochrome"}:
        return True
    if color_system in {
        "color_negative_dye",
        "positive_dye",
        "color_coupler",
    }:
        return False
    if medium.layer_masters_available:
        return _density_is_monochrome(medium.density_grain)
    if medium.optical_density_rgb is not None:
        return _density_is_monochrome(medium.optical_density_rgb)
    return False


def _legacy_final_medium_contract(
    density: np.ndarray,
    config: DarkroomConfig,
    medium_contract: dict[str, object],
) -> dict[str, object]:
    """Describe current density output through the new final-medium contract."""
    density = np.asarray(density, dtype=np.float32)
    if density.ndim != 3 or density.shape[-1] != 3:
        raise ValueError(f"legacy density must have HxWx3 shape, got {density.shape}")
    # This adapter represents an already-collapsed dye-density master. Building
    # zero-valued silver and halide images solely to ask for their contract used
    # to allocate multiple full frames. The contract is configuration-derived;
    # only the dye-presence boolean needs to inspect pixels.
    payload: dict[str, object] = {
        "material_key": str(config.film.name),
        "process_key": str(medium_contract["medium_process"]),
        "image_polarity": str(medium_contract["image_polarity"]),
        "view_mode": "transmissive",
        "compatible_interpreters": [
            str(value) for value in medium_contract["compatible_interpreters"]
        ],
        "components": {
            "metallic_silver": False,
            "dye": bool(float(np.max(density)) > 1e-6),
            "residual_halide": False,
            "bleached_halide": False,
            "auxiliary_remaining": 0.0,
        },
        "optical_observation": {
            "dye_absorption_matrix": [
                [float(value) for value in row]
                for row in config.film.dye_absorption_matrix
            ],
            "base_dye_interaction_strength": float(
                config.film.base_dye_interaction_strength
            ),
            "base_dye_interaction_matrix": [
                [float(value) for value in row]
                for row in config.film.base_dye_interaction_matrix
            ],
            "silver_density_per_layer": [0.0, 0.0, 0.0],
            "residual_halide_density_per_layer": [0.0, 0.0, 0.0],
            "retained_halide_density_rgb": [0.62, 0.82, 1.0],
            "base_density_rgb": [
                float(value) for value in config.film.film_base_density_rgb
            ],
            "auxiliary_density_rgb": [0.0, 0.0, 0.0],
            "auxiliary_remaining": 0.0,
        },
    }
    payload["representation"] = "legacy_density_adapter"
    observation = payload["optical_observation"]
    if isinstance(observation, dict):
        observation["density_min"] = [float(value) for value in config.film.density_min]
        observation["density_max"] = [float(value) for value in config.film.density_max]
        observation["color_process"] = str(config.film.color_process)
    payload["process_program"] = process_program_payload(
        program_from_develop_recipe(
            config.chemistry,
            mode=config.mode,
            material_process=config_medium_process(config),
        )
    )
    return payload


def _final_medium_observation_payload(medium: DevelopedNegative) -> dict[str, object] | None:
    metadata = medium.metadata if isinstance(medium.metadata, dict) else {}
    process_model = metadata.get("film_process_model")
    if process_model is None and isinstance(metadata.get("developed_medium"), dict):
        process_model = metadata["developed_medium"].get("film_process_model")
    if not isinstance(process_model, dict):
        return None
    observation = process_model.get("optical_observation")
    return observation if isinstance(observation, dict) else None


def apply_optical_observation_snapshot(
    config: DarkroomConfig,
    observation: dict[str, object] | None,
) -> bool:
    """Restore immutable material optics from a developed-medium snapshot."""
    if not isinstance(observation, dict):
        return False
    restored = False

    def finite_array(key: str, shape: tuple[int, ...]) -> np.ndarray | None:
        value = observation.get(key)
        if value is None:
            return None
        try:
            array = np.asarray(value, dtype=np.float64)
        except (TypeError, ValueError):
            return None
        if array.shape != shape or not np.isfinite(array).all():
            return None
        return array

    matrix = finite_array("dye_absorption_matrix", (3, 3))
    if matrix is not None:
        config.film.dye_absorption_matrix = tuple(
            tuple(float(value) for value in row) for row in matrix
        )
        restored = True
    base_interaction_matrix = finite_array("base_dye_interaction_matrix", (3, 3))
    if base_interaction_matrix is not None:
        config.film.base_dye_interaction_matrix = tuple(
            tuple(float(value) for value in row) for row in base_interaction_matrix
        )
        restored = True
    base_interaction_strength = observation.get("base_dye_interaction_strength")
    if isinstance(base_interaction_strength, (int, float, np.number)) and np.isfinite(
        float(base_interaction_strength)
    ):
        config.film.base_dye_interaction_strength = float(base_interaction_strength)
        restored = True
    base = finite_array("base_density_rgb", (3,))
    if base is not None:
        config.film.film_base_density_rgb = tuple(float(value) for value in base)
        restored = True
    density_min = finite_array("density_min", (3,))
    if density_min is not None:
        config.film.density_min = tuple(float(value) for value in density_min)
        restored = True
    density_max = finite_array("density_max", (3,))
    if density_max is not None:
        config.film.density_max = tuple(float(value) for value in density_max)
        restored = True
    color_process = observation.get("color_process")
    if isinstance(color_process, str) and color_process.strip():
        config.film.color_process = str(color_process)
        restored = True
    return restored


def _apply_final_medium_observation(config: DarkroomConfig, medium: DevelopedNegative) -> bool:
    """Restore immutable material optics without accepting them from a scanner preset."""
    return apply_optical_observation_snapshot(
        config,
        _final_medium_observation_payload(medium),
    )


def _clear_base_scanner_sample(
    config: DarkroomConfig,
    medium: DevelopedNegative | None = None,
) -> np.ndarray:
    """Return the known clear-base scanner sample for generated negatives."""
    clear_density = _clear_base_optical_density_rgb(config, medium).reshape(1, 1, 3)
    return capture_optical_density(
        clear_density,
        config.scanner,
        illuminant_rgb=transmission_illuminant_rgb(config.scanner),
    ).reshape(1, 1, 3)


def _clear_base_optical_density_rgb(
    config: DarkroomConfig,
    medium: DevelopedNegative | None = None,
) -> np.ndarray:
    if medium is not None and medium.clear_base_optical_density_rgb is not None:
        return np.asarray(
            medium.clear_base_optical_density_rgb,
            dtype=np.float32,
        ).reshape(3)
    clear_layer_density = np.asarray(config.film.density_min, dtype=np.float32).reshape(
        1,
        1,
        3,
    )
    return negative_total_density_rgb(clear_layer_density, config.film).reshape(3)


def _optical_density_with_optional_reference_border(
    optical_density_rgb: np.ndarray,
    config: DarkroomConfig,
    medium: DevelopedNegative,
) -> np.ndarray:
    """Frame a derived optical master without mutating or replacing the medium."""
    if not bool(config.scanner.include_clear_base_border):
        return optical_density_rgb
    optical = np.asarray(optical_density_rgb, dtype=np.float32)
    border = scanner_raw_export_border_width(
        optical.shape,
        config.output.scanner_raw_border_percent,
        config.output.scanner_raw_border_min_px,
    )
    if border <= 0:
        return optical
    height, width = optical.shape[:2]
    canvas = np.empty(
        (height + border * 2, width + border * 2, 3),
        dtype=np.float32,
    )
    canvas[...] = _clear_base_optical_density_rgb(config, medium).reshape(1, 1, 3)
    canvas[border : border + height, border : border + width] = optical
    return canvas


def _with_scan_interpretation(
    negative_config: DarkroomConfig,
    scan_config: DarkroomConfig,
) -> DarkroomConfig:
    """保留已冲洗负片的胶片身份，只替换当前扫描/输出解释。"""
    combined = copy.deepcopy(negative_config)
    combined.scanner = copy.deepcopy(scan_config.scanner)
    combined.output = copy.deepcopy(scan_config.output)
    combined.look.print_contrast = float(scan_config.look.print_contrast)
    combined.look.print_exposure_ev = float(scan_config.look.print_exposure_ev)
    combined.enable_subtractive = bool(scan_config.enable_subtractive)
    combined.fast_mode = bool(scan_config.fast_mode)
    combined.debug_output = bool(scan_config.debug_output)
    combined.save_sidecar = bool(scan_config.save_sidecar)
    combined.comparison_grid = bool(scan_config.comparison_grid)
    return combined


def _config_for_developed_medium_export(
    medium: DevelopedNegative,
    requested_config: DarkroomConfig,
) -> DarkroomConfig:
    """Build an export config without accepting material optics from a caller."""
    stored = medium.metadata.get("runtime_config")
    if isinstance(stored, DarkroomConfig):
        material_config = copy.deepcopy(stored)
    else:
        material_config = _config_from_developed_medium_identity(medium)
    _apply_final_medium_observation(material_config, medium)
    return _with_scan_interpretation(material_config, requested_config)


def _config_from_developed_medium_identity(negative: DevelopedNegative) -> DarkroomConfig:
    config = DarkroomConfig()
    config.film.medium_family = str(getattr(negative, "medium_family", "film"))
    config.film.medium_process = str(getattr(negative, "medium_process", "negative"))
    config.film.image_polarity = str(getattr(negative, "image_polarity", "negative"))
    config.film.film_base_density_rgb = (0.03, 0.03, 0.035) if str(getattr(negative, "base_type", "")) == "clear_base" else config.film.film_base_density_rgb

    interpreters = tuple(str(v) for v in getattr(negative, "compatible_interpreters", ()) or ())
    if "positive_transparency_scan" in interpreters or (
        str(getattr(negative, "image_polarity", "")) == "positive"
        and str(getattr(negative, "view_mode", "")) == "transmissive"
    ):
        config.scanner.interpreter_key = "positive_transparency_scan"
        config.scanner.target_medium_process = config.film.medium_process
        config.scanner.input_polarity = "positive"
        config.scanner.output_polarity = "positive"
        config.scanner.scan_method = "positive_transparency"
        return config

    config.scanner.interpreter_key = "negative_scan"
    config.scanner.target_medium_process = "negative"
    config.scanner.input_polarity = "negative"
    config.scanner.output_polarity = "positive"
    return config


def _runtime_config(config: DarkroomConfig | None, prepared: bool = False) -> DarkroomConfig:
    # Prepared configurations are already private runtime snapshots. Returning
    # them directly avoids a second deepcopy at every registry dispatch while
    # preserving isolation for all public entry points.
    if prepared and config is not None:
        runtime = config
    else:
        runtime = copy.deepcopy(config or DarkroomConfig())
    if not prepared:
        _force_bw_negative(runtime)
        _apply_look_strength(runtime)
    if str(runtime.scanner.interpretation_mode or "auto").strip().lower() in {
        "",
        "auto",
    }:
        legacy_positive = (
            str(runtime.scanner.interpreter_key).strip().lower()
            == "positive_transparency_scan"
        )
        runtime.scanner.remove_base_mask = not legacy_positive
        runtime.scanner.invert_transmission = not legacy_positive
    configure_native_thread_limit(runtime.processing.native_thread_limit)
    return runtime


def _medium_optical_density_rgb(
    medium: DevelopedNegative,
    film,
    row_slice: slice | None = None,
) -> np.ndarray:
    """Return authoritative RGB density, falling back to the portable CMY master."""
    if medium.optical_density_rgb is not None:
        optical = np.asarray(medium.optical_density_rgb, dtype=np.float32)
        return optical if row_slice is None else optical[row_slice]
    density = medium.density_grain if row_slice is None else medium.density_grain[row_slice]
    return negative_total_density_rgb(density, film)


def _clip01(value: float) -> float:
    return float(np.clip(float(value), 0.0, 1.0))


def _color_program_representation(*, material_is_mono: bool, positive: bool) -> str:
    if positive:
        return (
            "reduced_bw_material_color_reversal_pool_v1"
            if material_is_mono
            else "reduced_color_reversal_pool_v1"
        )
    return (
        "reduced_bw_material_color_coupler_pool_v1"
        if material_is_mono
        else "reduced_color_coupler_pool_v1"
    )


def _bw_material_color_program_cross_process() -> dict[str, object]:
    return {
        "material_color_system": "silver_bw",
        "program_color_system": "color_coupler",
        "dye_formed": False,
    }



def _attach_reduced_process_contract(
    payload: dict[str, object],
    process_result,
    representation: str,
    effective_development=None,
    compatibility=None,
    process_totals: dict[str, float] | None = None,
) -> None:
    material_contract = process_result.final_medium.contract()
    payload["representation"] = representation
    payload["components"] = material_contract["components"]
    payload["material_pool_optical_observation"] = material_contract["optical_observation"]
    payload["pool_totals"] = (
        dict(process_totals)
        if process_totals is not None
        else process_result.state.totals()
    )
    payload["process_trace"] = [
        {
            "label": report.label,
            "action": report.action,
            "reacted_amount": float(report.reacted_amount),
            "strength": float(report.strength),
            "developability_gamma": float(report.developability_gamma),
            "highlight_compensation": float(report.highlight_compensation),
            "dye_destruction_basis": str(report.dye_destruction_basis),
        }
        for report in process_result.trace
    ]
    if effective_development is not None:
        payload["effective_development"] = asdict(effective_development)
    if compatibility is not None:
        payload["compatibility_profile"] = asdict(compatibility)


def _apply_process_variation(config: DarkroomConfig, rng: np.random.Generator) -> dict[str, object]:
    strength = _clip01(getattr(config.chemistry, "process_variation", 0.0))
    if strength <= 0.0:
        return {"strength": 0.0, "applied": False}

    chem = config.chemistry

    time_factor = float(np.clip(1.0 + rng.normal(0.0, 0.035 * strength), 0.75, 1.35))
    concentration_factor = float(np.clip(1.0 + rng.normal(0.0, 0.030 * strength), 0.80, 1.25))
    agitation_factor = float(np.clip(1.0 + rng.normal(0.0, 0.080 * strength), 0.65, 1.45))
    temperature_delta = float(np.clip(rng.normal(0.0, 0.55 * strength), -2.5, 2.5))
    exhaustion_delta = float(np.clip(rng.normal(0.0, 0.025 * strength), -0.08, 0.10))
    fixer_delta = float(np.clip(rng.normal(0.0, 0.018 * strength), -0.06, 0.08))
    # Accident controls are explicit tendencies, not hidden side effects of a
    # general bath-variation slider.  Preserve exact zero so process variation
    # cannot unpredictably allocate stain/uneven spatial fields; once enabled,
    # apply bounded multiplicative drift around the declared tendency.
    stain_relative_delta = float(
        np.clip(rng.normal(0.0, 0.12 * strength), -0.40, 0.50)
    )
    uneven_relative_delta = float(
        np.clip(rng.normal(0.0, 0.16 * strength), -0.45, 0.60)
    )
    original_reticulation = _clip01(float(chem.reticulation_strength))
    reticulation_relative_delta = (
        float(np.clip(rng.normal(0.0, 0.10 * strength), -0.35, 0.45))
        if original_reticulation > 0.0
        else 0.0
    )
    original_dye_damage = _clip01(float(chem.dye_damage_strength))
    dye_damage_relative_delta = (
        float(np.clip(rng.normal(0.0, 0.10 * strength), -0.35, 0.45))
        if original_dye_damage > 0.0
        else 0.0
    )
    occlusion_originals = {
        key: _clip01(float(getattr(chem, key)))
        for key in EXPOSURE_OCCLUSION_AMOUNT_FIELDS
    }
    occlusion_relative_deltas = {
        key: (
            float(np.clip(rng.normal(0.0, 0.10 * strength), -0.35, 0.45))
            if original > 0.0
            else 0.0
        )
        for key, original in occlusion_originals.items()
    }

    chem.time_min = float(np.clip(float(chem.time_min) * time_factor, 0.01, 240.0))
    chem.temperature_c = float(np.clip(float(chem.temperature_c) + temperature_delta, 0.0, 100.0))
    chem.concentration = float(np.clip(float(chem.concentration) * concentration_factor, 0.01, 10.0))
    chem.agitation = float(np.clip(float(chem.agitation) * agitation_factor, 0.0, 10.0))
    chem.developer_exhaustion = _clip01(float(chem.developer_exhaustion) + exhaustion_delta)
    chem.fixer_exhaustion = _clip01(float(chem.fixer_exhaustion) + fixer_delta)
    original_stain = _clip01(float(chem.chemical_stain))
    original_uneven = _clip01(float(chem.uneven_development))
    chem.chemical_stain = _clip01(
        original_stain * (1.0 + stain_relative_delta)
    )
    chem.uneven_development = _clip01(
        original_uneven * (1.0 + uneven_relative_delta)
    )
    chem.reticulation_strength = _clip01(
        original_reticulation * (1.0 + reticulation_relative_delta)
    )
    chem.dye_damage_strength = _clip01(
        original_dye_damage * (1.0 + dye_damage_relative_delta)
    )
    for key, original in occlusion_originals.items():
        setattr(
            chem,
            key,
            _clip01(original * (1.0 + occlusion_relative_deltas[key])),
        )
    stain_delta = float(chem.chemical_stain - original_stain)
    uneven_delta = float(chem.uneven_development - original_uneven)
    reticulation_delta = float(
        chem.reticulation_strength - original_reticulation
    )
    dye_damage_delta = float(chem.dye_damage_strength - original_dye_damage)

    return {
        "strength": strength,
        "applied": True,
        "time_factor": time_factor,
        "temperature_delta": temperature_delta,
        "concentration_factor": concentration_factor,
        "agitation_factor": agitation_factor,
        "developer_exhaustion_delta": exhaustion_delta,
        "fixer_exhaustion_delta": fixer_delta,
        "chemical_stain_delta": stain_delta,
        "uneven_development_delta": uneven_delta,
        "reticulation_strength_delta": reticulation_delta,
        "dye_damage_strength_delta": dye_damage_delta,
        "chemical_stain_relative_delta": stain_relative_delta,
        "uneven_development_relative_delta": uneven_relative_delta,
        "reticulation_strength_relative_delta": reticulation_relative_delta,
        "dye_damage_strength_relative_delta": dye_damage_relative_delta,
        **{
            f"{key}_delta": float(getattr(chem, key) - occlusion_originals[key])
            for key in EXPOSURE_OCCLUSION_AMOUNT_FIELDS
        },
        **{
            f"{key}_relative_delta": occlusion_relative_deltas[key]
            for key in EXPOSURE_OCCLUSION_AMOUNT_FIELDS
        },
    }


def _resolve_scan_interpretation_config(
    negative: DevelopedNegative,
    config: DarkroomConfig | None,
    prepared_config: bool,
) -> tuple[DarkroomConfig, bool]:
    """Resolve scan-time config without changing the developed negative identity."""
    stored = negative.metadata.get("runtime_config")
    if isinstance(stored, DarkroomConfig):
        material_config = copy.deepcopy(stored)
        _apply_final_medium_observation(material_config, negative)
        if config is None:
            return material_config, True
        if not prepared_config:
            return _with_scan_interpretation(material_config, config), True
        return config, prepared_config

    material_config = _config_from_developed_medium_identity(negative)
    has_observation_snapshot = _apply_final_medium_observation(material_config, negative)
    if config is None:
        return material_config, has_observation_snapshot
    if has_observation_snapshot and not prepared_config:
        return _with_scan_interpretation(material_config, config), True
    return config, prepared_config


def _align_interpreter_to_medium(config: DarkroomConfig, medium: DevelopedNegative) -> None:
    """Resolve the legacy automatic mode into explicit observation controls."""
    compatible = tuple(str(value) for value in medium.compatible_interpreters)
    if not compatible:
        return
    key = compatible[0]
    config.scanner.interpreter_key = key
    config.scanner.target_medium_process = str(medium.medium_process)
    config.scanner.input_polarity = str(medium.image_polarity)
    config.scanner.output_polarity = "positive"
    if key == "positive_transparency_scan":
        config.scanner.remove_base_mask = False
        config.scanner.invert_transmission = False
        config.scanner.scan_method = "positive_transparency"
    elif key == "negative_scan":
        config.scanner.remove_base_mask = True
        config.scanner.invert_transmission = True
        if str(config.scanner.scan_method).lower() == "positive_transparency":
            config.scanner.scan_method = "negative_inversion"


def _validate_interpreter_compatibility(config: DarkroomConfig, medium: DevelopedNegative) -> None:
    # Explicit user choices are observation decisions, not material mutations.
    # The medium contract remains useful for recommendations and legacy auto
    # mode, but it must not prohibit a direct negative transparency capture or
    # an intentionally unconventional interpretation.
    selection = str(config.scanner.interpretation_mode or "auto").strip().lower()
    if selection in {"manual", "negative", "positive", "direct"}:
        return
    compatible = tuple(str(value) for value in medium.compatible_interpreters)
    requested = str(config.scanner.interpreter_key)
    if compatible and requested not in compatible:
        raise ValueError(
            f"Interpreter '{requested}' is incompatible with developed medium "
            f"'{medium.medium_process}' ({medium.image_polarity}); expected one of {compatible}."
        )


def _processing_work_long_edge(config: DarkroomConfig, module_name: str) -> int | None:
    """Resolve internal work size for low-frequency or stochastic modules."""
    quality = str(config.processing.quality_mode).strip().lower()
    configured = getattr(config.processing, f"{module_name}_work_long_edge", None)
    if configured is not None:
        edge: int | None = int(configured)
    elif quality in {"high", "full", "native"}:
        edge = None
    elif quality in {"draft", "preview"}:
        edge = 1200
    else:
        edge = 1800

    if edge is not None and edge <= 0:
        edge = None
    if uses_reduced_implementation(config):
        # Reduced execution must remain reduced even when the internal detail
        # tier is ``high``.  Previously the early native return delegated this
        # cap to individual modules, which made the resolved plan misleading
        # and caused explicit smaller overrides to be ignored.  Keeping the
        # cap here makes one authoritative plan without changing the existing
        # 1600 px reduced-mode result.
        edge = 1600 if edge is None else min(edge, 1600)
    return edge


def _adjacency_work_long_edge(config: DarkroomConfig) -> int:
    """Resolve the bounded global grid for reduced adjacency kinetics."""
    configured = config.processing.adjacency_work_long_edge
    if configured is not None:
        edge = int(configured)
    else:
        quality = str(config.processing.quality_mode).strip().lower()
        if quality in {"draft", "preview"}:
            edge = 1200
        elif quality in {"high", "full", "native"}:
            edge = 3200
        else:
            edge = 1800
    if uses_reduced_implementation(config):
        edge = min(edge, 1200)
    return max(1, edge)


def _dir_work_long_edge(config: DarkroomConfig) -> int:
    """Resolve the bounded global grid for reduced layer DIR kinetics."""
    configured = config.processing.dir_work_long_edge
    if configured is not None:
        edge = int(configured)
    else:
        quality = str(config.processing.quality_mode).strip().lower()
        if quality in {"draft", "preview"}:
            edge = 900
        elif quality in {"high", "full", "native"}:
            edge = 1800
        else:
            edge = 1400
    if uses_reduced_implementation(config):
        edge = min(edge, 900)
    return max(1, edge)


def _emulsion_spread_work_long_edge(config: DarkroomConfig) -> int:
    """Resolve the bounded optical-propagation grid for compact spread."""
    configured = config.processing.emulsion_spread_work_long_edge
    if configured is not None:
        edge = int(configured)
    else:
        quality = str(config.processing.quality_mode).strip().lower()
        if quality in {"draft", "preview"}:
            edge = 1400
        elif quality in {"high", "full", "native"}:
            edge = 3200
        else:
            edge = 2200
    if uses_reduced_implementation(config):
        edge = min(edge, 1400)
    return max(1, edge)


def _material_pool_tile_rows(
    config: DarkroomConfig,
    image: np.ndarray,
) -> int | None:
    """Resolve exact row tiling for large pointwise material transitions."""
    configured_rows = int(config.processing.material_tile_rows)
    if configured_rows <= 0:
        return None
    height, width = image.shape[:2]
    if not exact_material_tiling_required(
        width,
        height,
        threshold_megapixels=float(
            config.processing.material_tile_threshold_megapixels
        ),
        memory_budget_mb=config.processing.memory_budget_mb,
    ):
        return None
    # Keep a tile near two megapixels even for unusually wide panoramas.
    rows_for_two_megapixels = max(1, int(2_000_000 / max(width, 1)))
    return max(1, min(configured_rows, rows_for_two_megapixels, height))


def _merge_reduced_audit_payload(
    aggregate: dict[str, object] | None,
    tile: dict[str, object],
) -> dict[str, object]:
    """Merge pointwise tile audit data without retaining material-pool arrays."""
    if aggregate is None:
        return copy.deepcopy(tile)

    aggregate_components = aggregate.get("components")
    tile_components = tile.get("components")
    if isinstance(aggregate_components, dict) and isinstance(tile_components, dict):
        for key, value in tile_components.items():
            if isinstance(value, bool):
                aggregate_components[key] = bool(aggregate_components.get(key, False)) or value

    aggregate_totals = aggregate.get("pool_totals")
    tile_totals = tile.get("pool_totals")
    if isinstance(aggregate_totals, dict) and isinstance(tile_totals, dict):
        for key, value in tile_totals.items():
            if key == "auxiliary_remaining":
                continue
            aggregate_totals[key] = float(aggregate_totals.get(key, 0.0)) + float(value)

    aggregate_trace = aggregate.get("process_trace")
    tile_trace = tile.get("process_trace")
    if isinstance(aggregate_trace, list) and isinstance(tile_trace, list):
        if len(aggregate_trace) != len(tile_trace):
            raise RuntimeError("Material tile process traces do not share one program.")
        for aggregate_step, tile_step in zip(aggregate_trace, tile_trace):
            if not isinstance(aggregate_step, dict) or not isinstance(tile_step, dict):
                raise RuntimeError("Material tile process trace is malformed.")
            if (
                aggregate_step.get("action") != tile_step.get("action")
                or aggregate_step.get("label") != tile_step.get("label")
            ):
                raise RuntimeError("Material tile process traces are inconsistent.")
            aggregate_step["reacted_amount"] = float(
                aggregate_step.get("reacted_amount", 0.0)
            ) + float(tile_step.get("reacted_amount", 0.0))
    return aggregate


def _silver_grain_plan(
    config: DarkroomConfig,
    rng: np.random.Generator,
    frame_shape: tuple[int, int],
    program_kind: str | None,
) -> SilverGrainPlan | None:
    """Resolve one dedicated, tile-independent silver-grain stream."""
    strength = float(getattr(config.film, "silver_grain_strength", 0.0))
    if (
        not config.enable_grain
        or program_kind is None
        or strength <= 0.0
    ):
        return None
    effective = build_effective_development(config.chemistry)
    degradation = float(
        np.clip(getattr(config.film, "material_degradation", 0.0), 0.0, 1.0)
    )
    seed = int(rng.integers(0, np.iinfo(np.uint32).max, dtype=np.uint32))
    return SilverGrainPlan(
        full_shape=(int(frame_shape[0]), int(frame_shape[1])),
        seed=seed,
        strength=float(np.clip(
            strength * float(effective.grain_factor) * (1.0 + 0.35 * degradation),
            0.0,
            0.50,
        )),
        radius=float(np.clip(
            float(getattr(config.film, "silver_grain_radius", 0.0008))
            * float(effective.grain_radius_factor),
            1e-5,
            0.08,
        )),
        clump_mix=float(np.clip(
            float(getattr(config.film, "silver_grain_clump_mix", 0.22))
            * float(np.sqrt(effective.grain_clump_factor)),
            0.0,
            1.0,
        )),
    )


def _reduced_silver_density(reduced, film) -> np.ndarray:
    """Return the scalar neutral density already assigned to metallic silver."""
    if hasattr(reduced, "silver_density"):
        return np.asarray(reduced.silver_density, dtype=np.float32)
    if hasattr(reduced, "silver_density_rgb"):
        return np.asarray(reduced.silver_density_rgb[..., 0], dtype=np.float32)
    amount = np.mean(
        reduced.process_result.final_medium.metallic_silver,
        axis=-1,
        dtype=np.float32,
    )
    density_range = max(
        float(
            np.mean(
                np.asarray(film.density_max, dtype=np.float32)
                - np.asarray(film.density_min, dtype=np.float32)
            )
        ),
        1e-6,
    )
    return (
        amount * density_range * float(reduced.effective_development.d_max_factor)
    ).astype(np.float32, copy=False)


def _apply_reduced_silver_grain(
    reduced,
    film,
    plan: SilverGrainPlan | None,
    *,
    row_offset: int = 0,
) -> bool:
    if plan is None:
        return False
    silver_density = _reduced_silver_density(reduced, film)
    try:
        return apply_metallic_silver_grain(
            reduced.optical_density_rgb,
            silver_density,
            plan,
            row_offset=row_offset,
        )
    finally:
        del silver_density


def _develop_reduced_material_tiled(
    image: np.ndarray,
    config: DarkroomConfig,
    program_kind: str,
    tile_rows: int,
    local_development_rate: object | None = None,
    latent_layer_exposure_addition: np.ndarray | LayerExposureAdditionField | None = None,
    silver_grain_plan: SilverGrainPlan | None = None,
    consume_input_as_density: bool = False,
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None, dict[str, object]]:
    """Run exact pointwise pool transitions in bounded row tiles."""
    image = np.asarray(image)
    height, width = image.shape[:2]
    if consume_input_as_density:
        if (
            image.shape != (height, width, 3)
            or image.dtype != np.float32
            or not image.flags.c_contiguous
            or not image.flags.writeable
        ):
            raise ValueError(
                "consumed tiled material input must be writable "
                "C-contiguous float32 RGB"
            )
        density = image
    else:
        density = np.empty((height, width, 3), dtype=np.float32)
    optical_density = np.empty((height, width, 3), dtype=np.float32)
    audit: dict[str, object] | None = None
    clear_base_optical_density_rgb: np.ndarray | None = None
    neutral_silver_sum = np.zeros(3, dtype=np.float64)
    neutral_halide_sum = np.zeros(3, dtype=np.float64)
    bleached_halide_sum = np.zeros(3, dtype=np.float64)
    neutral_pixels = 0
    material_is_mono = is_monochrome_material(config)
    is_positive = str(developed_medium_contract(config)["image_polarity"]) == "positive"
    silver_grain_applied = False
    if latent_layer_exposure_addition is not None:
        expected_layers = (
            1
            if material_is_mono and program_kind.startswith("bw_")
            else 3
        )
        expected_shape = (height, width, expected_layers)
        addition_shape = (
            latent_layer_exposure_addition.shape
            if isinstance(latent_layer_exposure_addition, LAZY_LAYER_EXPOSURE_FIELD_TYPES)
            else np.shape(latent_layer_exposure_addition)
        )
        if addition_shape != expected_shape:
            raise ValueError(
                "tiled latent_layer_exposure_addition must match the full "
                f"material layer shape {expected_shape}, got "
                f"{addition_shape}"
            )

    for start in range(0, height, tile_rows):
        stop = min(start + tile_rows, height)
        tile_image = image[start:stop]
        if local_development_rate is None:
            tile_rate = None
        elif isinstance(local_development_rate, StepDevelopmentRateField):
            tile_rate = local_development_rate.slice_rows(start, stop)
        else:
            tile_rate = np.asarray(local_development_rate[start:stop], dtype=np.float32)
        if latent_layer_exposure_addition is None:
            tile_layer_addition = None
        elif isinstance(latent_layer_exposure_addition, LAZY_LAYER_EXPOSURE_FIELD_TYPES):
            tile_layer_addition = latent_layer_exposure_addition.slice_rows(start, stop)
        else:
            tile_layer_addition = np.asarray(
                latent_layer_exposure_addition[start:stop],
                dtype=np.float32,
            )
        if program_kind == "bw_negative":
            reduced = develop_bw_negative_reduced(
                tile_image,
                config.film,
                config.chemistry,
                local_development_rate=tile_rate,
                latent_layer_exposure_addition=tile_layer_addition,
                retain_latent_fraction=False,
                retain_process_state=False,
            )
            tile_density = reduced.density_rgb
            representation = (
                "reduced_bw_silver_pool_v1"
                if material_is_mono
                else "reduced_color_material_bw_silver_pool_v1"
            )
        elif program_kind == "bw_reversal":
            reduced = develop_bw_reversal_reduced(
                tile_image,
                config.film,
                config.chemistry,
                local_development_rate=tile_rate,
                latent_layer_exposure_addition=tile_layer_addition,
                retain_latent_fraction=False,
                retain_process_state=False,
            )
            tile_density = reduced.density_rgb
            representation = (
                "reduced_bw_reversal_pool_v1"
                if material_is_mono
                else "reduced_color_material_bw_reversal_pool_v1"
            )
        elif program_kind == "color_negative":
            reduced = develop_color_negative_reduced(
                tile_image,
                config.film,
                config.chemistry,
                local_development_rate=tile_rate,
                latent_layer_exposure_addition=tile_layer_addition,
                retain_latent_fraction=False,
                retain_process_state=False,
            )
            tile_density = reduced.density_cmy
            representation = _color_program_representation(
                material_is_mono=material_is_mono,
                positive=False,
            )
        elif program_kind == "color_reversal":
            reduced = develop_color_reversal_reduced(
                tile_image,
                config.film,
                config.chemistry,
                local_development_rate=tile_rate,
                latent_layer_exposure_addition=tile_layer_addition,
                retain_latent_fraction=False,
                retain_process_state=False,
            )
            tile_density = reduced.density_cmy
            representation = _color_program_representation(
                material_is_mono=material_is_mono,
                positive=True,
            )
        else:
            raise ValueError(f"Unsupported tiled material program: {program_kind}")

        silver_grain_applied = (
            _apply_reduced_silver_grain(
                reduced,
                config.film,
                silver_grain_plan,
                row_offset=start,
            )
            or silver_grain_applied
        )
        density[start:stop] = tile_density
        optical_density[start:stop] = reduced.optical_density_rgb
        if clear_base_optical_density_rgb is None:
            clear_base_optical_density_rgb = np.asarray(
                reduced.clear_base_optical_density_rgb,
                dtype=np.float32,
            ).reshape(3)
        tile_audit: dict[str, object] = {}
        _attach_reduced_process_contract(
            tile_audit,
            reduced.process_result,
            representation,
            reduced.effective_development,
            reduced.compatibility,
            process_totals=getattr(reduced, "process_totals", None),
        )
        audit = _merge_reduced_audit_payload(audit, tile_audit)
        if hasattr(reduced, "silver_density"):
            silver_sum = float(
                np.sum(reduced.silver_density, dtype=np.float64)
            )
            neutral_silver_sum += silver_sum
            neutral_halide_sum += np.sum(
                reduced.residual_halide_density_rgb, axis=(0, 1), dtype=np.float64
            )
            bleached_halide_sum += np.sum(
                reduced.bleached_halide_density_rgb, axis=(0, 1), dtype=np.float64
            )
            neutral_pixels += (stop - start) * width
        del reduced, tile_density, tile_audit

    if audit is None:
        raise RuntimeError("Material tiling produced no process audit payload.")
    if program_kind.startswith("bw_") and not material_is_mono:
        audit["cross_process"] = {
            "material_color_system": "color_coupler",
            "program_color_system": "silver_only",
            "dye_formed": False,
        }
    elif program_kind.startswith("color_") and material_is_mono:
        audit["cross_process"] = _bw_material_color_program_cross_process()
    elif program_kind.startswith("bw_"):
        audit["cross_process"] = None
    if neutral_pixels:
        audit["neutral_density_components"] = {
            "silver_rgb_mean": [
                float(value) for value in neutral_silver_sum / neutral_pixels
            ],
            "residual_halide_rgb_mean": [
                float(value) for value in neutral_halide_sum / neutral_pixels
            ],
            "bleached_halide_rgb_mean": [
                float(value) for value in bleached_halide_sum / neutral_pixels
            ],
        }
    audit["execution"] = {
        "material_pool_tiling": "exact_row_tiles_v1",
        "tile_rows": int(tile_rows),
        "tile_count": int((height + tile_rows - 1) // tile_rows),
        "independent_silver_grain_applied": bool(silver_grain_applied),
    }
    return density, optical_density, clear_base_optical_density_rgb, audit


def develop_negative(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
    *,
    _prepared_config: bool = False,
    _allow_discarded_history: bool = False,
) -> DevelopedNegative:
    """Dispatch development through the registered media pipeline."""
    image_srgb = _validate_rgb_image_array(image_srgb)
    if config is not None and not _prepared_config:
        _validate_develop_runtime(config)
    runtime = _runtime_config(config, prepared=_prepared_config)
    _validate_develop_runtime(
        runtime,
        allow_discarded_history=_allow_discarded_history,
    )
    if rng is None:
        rng, _ = _rng_for_input(None, runtime)
    variation = _apply_process_variation(runtime, rng)
    pipeline = get_develop_pipeline(runtime)
    negative = pipeline.develop(image_srgb, runtime, rng, True)
    _validate_developed_medium_state(negative)
    negative.metadata["process_variation"] = variation
    return negative


develop_medium = develop_negative


def _develop_silver_halide_pipeline(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
    _prepared_config: bool = False,
) -> DevelopedNegative:
    """把输入 sRGB 图像冲洗成 CMY 底片密度状态。"""
    config = _runtime_config(config, prepared=_prepared_config)
    implementation_plan = resolve_implementation_plan(config)
    if rng is None:
        rng, _ = _rng_for_input(None, config)

    history_policy = str(config.processing.history_storage_policy).strip().lower()
    discard_history = history_policy == "discard"
    cold_history = history_policy == "cold_fp16"
    history_dtype = np.float16 if cold_history else np.float32

    linear = srgb_to_linear(image_srgb)
    linear = linear * (2.0 ** float(config.look.exposure_ev))
    if is_monochrome_material(config):
        linear = np.repeat(luminance(linear)[..., None], 3, axis=-1)
    linear, exposure_occlusion_map = apply_exposure_occlusion_to_linear(
        linear,
        config.chemistry,
        rng=rng,
    )
    has_exposure_occlusion_map = exposure_occlusion_map is not None
    del exposure_occlusion_map
    linear_input = None if discard_history else np.clip(linear, 0.0, 1.0)

    if config.enable_mtf:
        # ``linear`` is a private formation buffer and has no consumer after
        # this call; the public MTF helper remains non-mutating by default.
        after_mtf = apply_emulsion_mtf(
            linear,
            config.film,
            consume_input=True,
            model=implementation_plan.emulsion_mtf_model,
        )
    else:
        after_mtf = linear
    del linear

    # Date-back / lab-edge LEDs sit on the film plane. Apply them after MTF
    # so the flash stays sharp, and after scene occlusion so a back/side lamp
    # is not blocked by dust on the emulsion face.
    after_mtf, edge_imprint_map = apply_edge_imprint_to_linear(
        after_mtf,
        config.film_border,
    )
    has_edge_imprint_map = edge_imprint_map is not None
    del edge_imprint_map

    # A leak is additional exposure reaching the material, not a density frame
    # laid over the developed result. Put it before emulsion/base scattering so
    # it shares the same latent-state path while its directional generator keeps
    # it from becoming an artificial four-edge halo.
    light_leak_pattern = None
    light_leak_secondary_pattern = None
    if float(config.chemistry.light_leak_strength) > 1e-6:
        identity_seed = config.chemistry.light_leak_identity_seed
        leak_rng = (
            rng
            if identity_seed is None
            else np.random.default_rng(int(identity_seed))
        )
        # Default ``auto`` still consumes the frame RNG in the historical
        # local-edge order. A dedicated identity seed is the only exception.
        light_leak_pattern = sample_light_leak_pattern(
            leak_rng,
            template_key=resolved_light_leak_template(config.chemistry),
        )
        secondary_key = str(
            config.chemistry.light_leak_secondary_template
        ).strip().lower()
        if secondary_key not in {"", "none"}:
            light_leak_secondary_pattern = sample_light_leak_pattern(
                leak_rng,
                template_key=secondary_key,
            )
    after_light_leak, light_leak_map = apply_light_leak_to_exposure(
        after_mtf,
        config.chemistry,
        rng=rng,
        pattern=light_leak_pattern,
        secondary_pattern=light_leak_secondary_pattern,
    )
    material_layer_count = (
        1
        if is_monochrome_material(config)
        and (_uses_reduced_bw_negative(config) or _uses_reduced_bw_reversal(config))
        else 3
    )
    layer_emulsion_spread_addition = emulsion_highlight_spread_field(
        after_light_leak,
        config.film,
        layer_count=material_layer_count,
        work_long_edge=_emulsion_spread_work_long_edge(config),
    )
    emulsion_spread_applied = layer_emulsion_spread_addition is not None
    emulsion_spread_work_shape = (
        None
        if layer_emulsion_spread_addition is None
        else tuple(int(value) for value in layer_emulsion_spread_addition.work_shape[:2])
    )
    halation_return_model = str(config.film.halation_return_model).strip().lower()
    configured_layer_weights = (
        np.asarray(config.film.halation_layer_return_weights, dtype=np.float32).reshape(3)
        if halation_return_model == "layer_selective"
        else None
    )
    layer_halation_addition: LayerExposureAdditionField | None = None
    if halation_return_model == "layer_selective":
        if (
            config.enable_halation
            and float(config.film.halation_strength) > 0.0
            and configured_layer_weights is not None
            and float(configured_layer_weights.max(initial=0.0)) > 0.0
        ):
            spread = halation_return_field(
                after_light_leak,
                config.film,
                fast=implementation_plan.lower_order_enabled,
                work_long_edge=_processing_work_long_edge(config, "halation"),
                spread_scale_weights=config.film.halation_spread_scale_weights,
                allow_empty_none=True,
                source_model=implementation_plan.halation_source_model,
            )
            if spread is None:
                layer_halation_addition = None
            else:
                # filter2D may leave ~1e-17 negative round-off around an otherwise
                # non-negative optical return. Layer exposure cannot be negative,
                # so clamp only the material-layer branch; compatibility RGB keeps
                # its historical pixels and hash.
                np.maximum(spread, 0.0, out=spread)
                layer_weights = (
                    (float(np.mean(configured_layer_weights)),)
                    if material_layer_count == 1
                    else tuple(float(value) for value in configured_layer_weights)
                )
                layer_halation_addition = LayerExposureAdditionField(
                    spread,
                    layer_weights,
                    strength=float(config.film.halation_strength),
                )
                del spread
        after_halation = after_light_leak
    elif config.enable_halation:
        after_halation = apply_halation(
            after_light_leak,
            config.film,
            fast=implementation_plan.lower_order_enabled,
            work_long_edge=_processing_work_long_edge(config, "halation"),
            source_model=implementation_plan.halation_source_model,
        )
    else:
        after_halation = after_light_leak
    layer_light_piping_addition = light_piping_exposure_field(
        after_halation.shape,
        config.film,
        layer_count=material_layer_count,
    )
    light_piping_applied = layer_light_piping_addition is not None
    light_piping_edge_weights = (
        None
        if layer_light_piping_addition is None
        else tuple(float(value) for value in layer_light_piping_addition.edge_weights)
    )
    latent_layer_exposure_addition = combine_layer_exposure_addition_fields(
        layer_emulsion_spread_addition,
        layer_halation_addition,
        layer_light_piping_addition,
    )
    del after_light_leak
    has_light_leak_map = light_leak_map is not None
    del light_leak_map

    tiled_program_kind: str | None = None
    if _uses_reduced_bw_negative(config):
        tiled_program_kind = "bw_negative"
    elif _uses_reduced_color_negative(config):
        tiled_program_kind = "color_negative"
    elif _uses_reduced_bw_reversal(config):
        tiled_program_kind = "bw_reversal"
    elif _uses_reduced_color_reversal(config):
        tiled_program_kind = "color_reversal"

    uneven_development_map = uneven_development_rate_field(
        after_halation,
        config.chemistry,
        rng=rng,
        fast=implementation_plan.lower_order_enabled,
        work_long_edge=_processing_work_long_edge(config, "grain"),
    )
    adjacency_rate_field = None
    adjacency_audit: dict[str, object] = {
        "enabled": False,
        "applied": False,
        "strength": float(config.chemistry.development_adjacency_strength),
    }
    if tiled_program_kind is not None:
        adjacency_rate_field, adjacency_audit = build_development_adjacency_field(
            after_halation,
            config.film,
            config.chemistry,
            tiled_program_kind,
            latent_layer_exposure_addition=latent_layer_exposure_addition,
            work_long_edge=_adjacency_work_long_edge(config),
        )
    dir_rate_field = None
    configured_dir_matrix = np.asarray(
        config.film.dir_material_response_matrix,
        dtype=np.float64,
    )
    dir_audit: dict[str, object] = {
        "material_dir_present": bool(np.any(configured_dir_matrix > 0.0)),
        "enabled": False,
        "applied": False,
        "material_response_matrix": tuple(
            tuple(float(value) for value in row) for row in configured_dir_matrix
        ),
        "process_effectiveness": config.chemistry.dir_process_effectiveness,
    }
    if tiled_program_kind in {"color_negative", "color_reversal"}:
        dir_rate_field, dir_audit = build_dir_material_response_field(
            after_halation,
            config.film,
            config.chemistry,
            tiled_program_kind,
            latent_layer_exposure_addition=latent_layer_exposure_addition,
            work_long_edge=_dir_work_long_edge(config),
        )
    step_rate_fields = ()
    if dir_rate_field is not None:
        step_rate_fields = (
            (
                str(dir_audit["color_development_step"]),
                str(dir_audit["color_development_action"]),
                dir_rate_field,
            ),
        )
    if adjacency_rate_field is not None or dir_rate_field is not None:
        first_step_label = (
            str(adjacency_audit["first_development_step"])
            if adjacency_rate_field is not None
            else str(dir_audit["color_development_step"])
        )
        development_rate_field: object | None = StepDevelopmentRateField(
            full_shape=after_halation.shape[:2],
            first_step_label=first_step_label,
            common_rate=uneven_development_map,
            first_step_rate=adjacency_rate_field,
            step_rate_fields=step_rate_fields,
        )
    else:
        development_rate_field = uneven_development_map
    after_accidents = after_halation
    if cold_history:
        # Both arrays have completed their last formation-side read. Compact
        # them before the material pools are allocated, not after development.
        linear_input = np.asarray(linear_input, dtype=np.float16)
        after_mtf_history = np.asarray(after_mtf, dtype=np.float16)
    elif discard_history:
        after_mtf_history = None
    else:
        after_mtf_history = after_mtf
    del after_mtf, after_halation
    reduced_bw = None
    reduced_color = None
    formed_optical_density_rgb: np.ndarray | None = None
    clear_base_optical_density_rgb: np.ndarray | None = None
    reduced_process_payload: dict[str, object] | None = None
    tile_rows = _material_pool_tile_rows(config, after_accidents)

    silver_grain_plan = _silver_grain_plan(
        config,
        rng,
        after_accidents.shape[:2],
        tiled_program_kind,
    )
    independent_silver_grain_applied = False

    # Explicit legacy density mode retains its historical exposure-equivalent
    # unevenness. The unified material-pool path below sends the same field to
    # operator kinetics and never changes the frozen latent state.
    if tiled_program_kind is None and uneven_development_map is not None:
        after_accidents = np.clip(
            after_accidents * uneven_development_map[..., None],
            0.0,
            4.0,
        ).astype(np.float32, copy=False)

    if tile_rows is not None and tiled_program_kind is not None:
        (
            density_cmy,
            formed_optical_density_rgb,
            clear_base_optical_density_rgb,
            reduced_process_payload,
        ) = _develop_reduced_material_tiled(
            after_accidents,
            config,
            tiled_program_kind,
            tile_rows,
            local_development_rate=development_rate_field,
            latent_layer_exposure_addition=latent_layer_exposure_addition,
            silver_grain_plan=silver_grain_plan,
            consume_input_as_density=bool(
                discard_history and not reference_execution_enabled()
            ),
        )
        execution_payload = reduced_process_payload.get("execution")
        if isinstance(execution_payload, dict):
            independent_silver_grain_applied = bool(
                execution_payload.get("independent_silver_grain_applied", False)
            )
    elif _uses_reduced_bw_negative(config):
        reduced_bw = develop_bw_negative_reduced(
            after_accidents,
            config.film,
            config.chemistry,
            local_development_rate=development_rate_field,
            latent_layer_exposure_addition=latent_layer_exposure_addition,
            retain_latent_fraction=False,
            retain_process_state=False,
        )
        independent_silver_grain_applied = _apply_reduced_silver_grain(
            reduced_bw,
            config.film,
            silver_grain_plan,
        )
        density_cmy = reduced_bw.density_rgb
        formed_optical_density_rgb = reduced_bw.optical_density_rgb
        clear_base_optical_density_rgb = reduced_bw.clear_base_optical_density_rgb
    elif _uses_reduced_color_negative(config):
        reduced_color = develop_color_negative_reduced(
            after_accidents,
            config.film,
            config.chemistry,
            local_development_rate=development_rate_field,
            latent_layer_exposure_addition=latent_layer_exposure_addition,
            retain_latent_fraction=False,
            retain_process_state=False,
        )
        independent_silver_grain_applied = _apply_reduced_silver_grain(
            reduced_color,
            config.film,
            silver_grain_plan,
        )
        density_cmy = reduced_color.density_cmy
        formed_optical_density_rgb = reduced_color.optical_density_rgb
        clear_base_optical_density_rgb = reduced_color.clear_base_optical_density_rgb
    elif _uses_reduced_bw_reversal(config):
        reduced_bw = develop_bw_reversal_reduced(
            after_accidents,
            config.film,
            config.chemistry,
            local_development_rate=development_rate_field,
            latent_layer_exposure_addition=latent_layer_exposure_addition,
            retain_latent_fraction=False,
            retain_process_state=False,
        )
        independent_silver_grain_applied = _apply_reduced_silver_grain(
            reduced_bw,
            config.film,
            silver_grain_plan,
        )
        density_cmy = reduced_bw.density_rgb
        formed_optical_density_rgb = reduced_bw.optical_density_rgb
        clear_base_optical_density_rgb = reduced_bw.clear_base_optical_density_rgb
    elif _uses_reduced_color_reversal(config):
        reduced_color = develop_color_reversal_reduced(
            after_accidents,
            config.film,
            config.chemistry,
            local_development_rate=development_rate_field,
            latent_layer_exposure_addition=latent_layer_exposure_addition,
            retain_latent_fraction=False,
            retain_process_state=False,
        )
        independent_silver_grain_applied = _apply_reduced_silver_grain(
            reduced_color,
            config.film,
            silver_grain_plan,
        )
        density_cmy = reduced_color.density_cmy
        formed_optical_density_rgb = reduced_color.optical_density_rgb
        clear_base_optical_density_rgb = reduced_color.clear_base_optical_density_rgb
    else:
        density_cmy = exposure_to_density(after_accidents, config.film, config.chemistry)
        if str(developed_medium_contract(config)["image_polarity"]) == "positive":
            density_cmy = _positive_transparency_density_from_negative_proxy(density_cmy, config)
    formed_density_cmy = density_cmy
    discard_formation_layer_masters = bool(
        not _RETAIN_FORMATION_LAYER_MASTERS.get()
        and formed_optical_density_rgb is not None
    )
    formed_density_has_material = bool(
        discard_formation_layer_masters
        and float(np.max(formed_density_cmy)) > 1e-6
    )
    after_halation_history = (
        None
        if discard_history
        else np.asarray(after_accidents, dtype=history_dtype)
    )
    del after_accidents
    del layer_halation_addition, layer_light_piping_addition
    del latent_layer_exposure_addition
    del development_rate_field, adjacency_rate_field, dir_rate_field

    # Collapse the large transient pool result into its small audit payload
    # before density accidents and grain allocate their own full-frame fields.
    # The authoritative formed density has already been extracted above.
    if reduced_bw is not None:
        reduced_process_payload = {}
        material_is_mono = is_monochrome_material(config)
        is_positive_program = str(developed_medium_contract(config)["image_polarity"]) == "positive"
        if is_positive_program:
            representation = (
                "reduced_bw_reversal_pool_v1"
                if material_is_mono
                else "reduced_color_material_bw_reversal_pool_v1"
            )
        else:
            representation = (
                "reduced_bw_silver_pool_v1"
                if material_is_mono
                else "reduced_color_material_bw_silver_pool_v1"
            )
        _attach_reduced_process_contract(
            reduced_process_payload,
            reduced_bw.process_result,
            representation,
            reduced_bw.effective_development,
            reduced_bw.compatibility,
            process_totals=reduced_bw.process_totals,
        )
        reduced_process_payload["cross_process"] = (
            None
            if material_is_mono
            else {
                "material_color_system": "color_coupler",
                "program_color_system": "silver_only",
                "dye_formed": False,
            }
        )
        reduced_bw = None
    elif reduced_color is not None:
        reduced_process_payload = {}
        material_is_mono = is_monochrome_material(config)
        is_positive_program = str(developed_medium_contract(config)["image_polarity"]) == "positive"
        _attach_reduced_process_contract(
            reduced_process_payload,
            reduced_color.process_result,
            _color_program_representation(
                material_is_mono=material_is_mono,
                positive=is_positive_program,
            ),
            reduced_color.effective_development,
            reduced_color.compatibility,
            process_totals=reduced_color.process_totals,
        )
        if material_is_mono:
            reduced_process_payload["cross_process"] = (
                _bw_material_color_program_cross_process()
            )
        reduced_process_payload["neutral_density_components"] = {
            "silver_rgb_mean": [
                float(value)
                for value in reduced_color.silver_density_rgb.mean(axis=(0, 1))
            ],
            "residual_halide_rgb_mean": [
                float(value)
                for value in reduced_color.residual_halide_density_rgb.mean(axis=(0, 1))
            ],
            "bleached_halide_rgb_mean": [
                float(value)
                for value in reduced_color.bleached_halide_density_rgb.mean(axis=(0, 1))
            ],
        }
        reduced_color = None

    has_uneven_development_map = uneven_development_map is not None
    # The local rate field has completed its only material-pool consumer.
    # Keep only its metadata identity before stain/plating/reticulation allocate
    # their own full-size maps; retaining those fields together wastes one scalar
    # frame without adding a diagnostic export.
    del uneven_development_map

    accident_components = apply_density_accident_components(
        density_cmy,
        config.chemistry,
        rng=rng,
        film=config.film,
        fast=implementation_plan.lower_order_enabled,
        work_long_edge=_processing_work_long_edge(config, "grain"),
        defer_compatibility_density=True,
    )
    processed_density_cmy = accident_components.density_cmy
    accident_density_deferred = bool(
        accident_components.compatibility_density_deferred
    )
    accident_stain_scale = accident_components.chemical_stain_layer_scale
    accident_plating_layer_scale = accident_components.surface_silver_layer_scale
    accident_plating_optical_scale = float(
        accident_components.surface_silver_density_scale
    )
    accident_reticulation_layer_scale = (
        accident_components.reticulation_layer_scale
    )
    accident_reticulation_optical_scale = float(
        accident_components.reticulation_density_scale
    )
    accident_density_aliases_formed = np.shares_memory(
        processed_density_cmy,
        formed_density_cmy,
    )
    accident_maps = accident_components.maps
    accident_map_name_set = set(accident_maps)
    if has_uneven_development_map:
        # The rate field has completed its only material consumer.  Metadata
        # retains the accident identity, not the full spatial diagnostic map.
        accident_map_name_set.add("uneven_development")
    accident_map_names = tuple(sorted(accident_map_name_set))
    has_silver_plating_map = "silver_plating" in accident_maps
    del accident_components
    independent_silver_only_grain = bool(
        silver_grain_plan is not None
        and tiled_program_kind is not None
        and tiled_program_kind.startswith("bw_")
    )
    consume_layer_density_for_deferred_accidents = False
    optical_layer_density_is_effective_delta = False
    if config.enable_grain and not independent_silver_only_grain:
        # Emulsion/dye-cloud grain is generated from the formed layer master.
        # Surface silver and chemical deposits are later optical components;
        # they must not retroactively change the emulsion-grain response.
        optical_layer_density = apply_density_grain(
            formed_density_cmy,
            config.film,
            config.chemistry,
            rng=rng,
            fast=implementation_plan.lower_order_enabled,
            work_long_edge=_processing_work_long_edge(config, "grain"),
            image_polarity=str(developed_medium_contract(config)["image_polarity"]),
            component_scope="emulsion",
            consume_input_as_effective_delta=discard_formation_layer_masters,
            model=implementation_plan.emulsion_grain_model,
        )
        optical_layer_density_is_effective_delta = (
            discard_formation_layer_masters
        )
        # Keep the portable compatibility master inclusive of both layer grain
        # and accidents without allocating a third full-resolution frame.  A
        # no-accident result deliberately aliases the formed master; never
        # merge into that alias or density_cmy ceases to mean "formed layers"
        # and the later RGB grain delta collapses to zero.
        if optical_layer_density_is_effective_delta:
            # ``formed_density_cmy`` was a private, final-output-only layer
            # master and now stores only the exact effective grain delta.
            # Accident deposits are observed independently below; no portable
            # compatibility master is needed on this retention contract.
            density_grain = optical_layer_density
            merge_layer_grain = False
        elif (
            accident_density_deferred
            and formed_optical_density_rgb is not None
            and not is_monochrome_material(config)
        ):
            # ``optical_layer_density`` is a private formed+grain buffer.  The
            # optical pass below first snapshots each tile's pure grain delta,
            # then rewrites this same buffer into the portable accident+grain
            # master in the historical addition order.  This avoids one full
            # RGB master at the post-grain peak.
            density_grain = optical_layer_density
            merge_layer_grain = False
            consume_layer_density_for_deferred_accidents = True
        elif accident_density_deferred:
            # Accident maps have already consumed their historical RNG slots,
            # but the compatibility master is allocated only after the much
            # larger grain workspace has been released.  Preserve the original
            # channel-addition order before merging the grain delta.
            density_grain = compose_density_accident_master(
                formed_density_cmy,
                accident_maps,
                chemical_stain_layer_scale=accident_stain_scale,
                surface_silver_layer_scale=accident_plating_layer_scale,
                reticulation_layer_scale=accident_reticulation_layer_scale,
            )
            merge_layer_grain = True
        elif accident_density_aliases_formed:
            density_grain = optical_layer_density
            merge_layer_grain = False
        else:
            density_grain = processed_density_cmy
            merge_layer_grain = True
        if merge_layer_grain:
            height, width = density_grain.shape[:2]
            merge_rows = max(1, min(height, int(2_000_000 / max(width, 1))))
            for start in range(0, height, merge_rows):
                stop = min(start + merge_rows, height)
                density_grain[start:stop] += (
                    optical_layer_density[start:stop] - formed_density_cmy[start:stop]
                )
            np.maximum(density_grain, 0.0, out=density_grain)
    else:
        density_grain = (
            compose_density_accident_master(
                formed_density_cmy,
                accident_maps,
                chemical_stain_layer_scale=accident_stain_scale,
                surface_silver_layer_scale=accident_plating_layer_scale,
                reticulation_layer_scale=accident_reticulation_layer_scale,
            )
            if accident_density_deferred
            else processed_density_cmy
        )
        optical_layer_density = formed_density_cmy
    if is_monochrome_material(config) and not discard_formation_layer_masters:
        density_grain = np.repeat(density_grain.mean(axis=-1, keepdims=True), 3, axis=-1)

    if formed_optical_density_rgb is not None:
        # Grain remains a layer/emulsion phenomenon and therefore passes
        # through the dye absorption matrix. Chemical stain, deposited surface
        # silver, and reticulation are separate post-process components: stain
        # is converted from its own layer-colour definition; silver and reduced
        # gelatin topography enter as neutral broadband RGB density. Do not send
        # the combined compatibility-master delta through one matrix, or those
        # neutral components would silently become coloured dye.
        absorption = np.asarray(config.film.dye_absorption_matrix, dtype=np.float32).reshape(3, 3)
        height, width = optical_layer_density.shape[:2]
        delta_rows = max(1, min(height, int(2_000_000 / max(width, 1))))
        stain_map = accident_maps.get("chemical_stain")
        plating_map = accident_maps.get("silver_plating")
        reticulation_map = accident_maps.get("reticulation")
        stain_scale = accident_stain_scale
        plating_scale = accident_plating_optical_scale
        for start in range(0, height, delta_rows):
            stop = min(start + delta_rows, height)
            # Subtract the already-authored accident master first. Only the
            # stochastic layer-grain delta belongs to this conversion.
            if optical_layer_density_is_effective_delta:
                layer_delta = optical_layer_density[start:stop]
            else:
                layer_delta = (
                    optical_layer_density[start:stop]
                    - formed_density_cmy[start:stop]
                )
            if is_monochrome_material(config):
                # The legacy dye-layer generator has partly independent layer
                # noise.  Silver-only monochrome observation must collapse
                # that proxy to one neutral broadband density contribution.
                rgb_delta = np.mean(
                    layer_delta,
                    axis=-1,
                    keepdims=True,
                    dtype=np.float32,
                )
            else:
                rgb_delta = np.einsum("...l,rl->...r", layer_delta, absorption).astype(
                    np.float32,
                    copy=False,
                )
            formed_optical_density_rgb[start:stop] += rgb_delta
            if stain_map is not None and stain_scale is not None:
                stain_layers = (
                    stain_map[start:stop, ..., None]
                    * np.asarray(stain_scale, dtype=np.float32).reshape(1, 1, 3)
                )
                if is_monochrome_material(config):
                    stain_rgb = stain_layers
                else:
                    stain_rgb = np.einsum(
                        "...l,rl->...r", stain_layers, absorption
                    ).astype(np.float32, copy=False)
                formed_optical_density_rgb[start:stop] += stain_rgb
                del stain_layers, stain_rgb
            if plating_map is not None and plating_scale > 0.0:
                formed_optical_density_rgb[start:stop] += (
                    plating_map[start:stop, ..., None] * plating_scale
                )
            if (
                reticulation_map is not None
                and accident_reticulation_optical_scale > 0.0
            ):
                formed_optical_density_rgb[start:stop] += (
                    reticulation_map[start:stop, ..., None]
                    * accident_reticulation_optical_scale
                )
            np.maximum(formed_optical_density_rgb[start:stop], 0.0, out=formed_optical_density_rgb[start:stop])
            if consume_layer_density_for_deferred_accidents:
                # Reuse the private formed+grain layer buffer as the final
                # compatibility master.  Keep the eager arithmetic order:
                # formed -> stain -> plating proxy -> reticulation proxy
                # -> pure grain delta.
                density_tile = density_grain[start:stop]
                np.copyto(density_tile, formed_density_cmy[start:stop])
                compatibility_scalar = np.empty(
                    density_tile.shape[:2],
                    dtype=np.float32,
                )
                if stain_map is not None and accident_stain_scale is not None:
                    for channel, scale_value in enumerate(accident_stain_scale):
                        np.multiply(
                            stain_map[start:stop],
                            float(scale_value),
                            out=compatibility_scalar,
                        )
                        density_tile[..., channel] += compatibility_scalar
                if (
                    plating_map is not None
                    and accident_plating_layer_scale is not None
                ):
                    for channel, scale_value in enumerate(
                        accident_plating_layer_scale
                    ):
                        np.multiply(
                            plating_map[start:stop],
                            float(scale_value),
                            out=compatibility_scalar,
                        )
                        density_tile[..., channel] += compatibility_scalar
                if (
                    reticulation_map is not None
                    and accident_reticulation_layer_scale is not None
                ):
                    for channel, scale_value in enumerate(
                        accident_reticulation_layer_scale
                    ):
                        np.multiply(
                            reticulation_map[start:stop],
                            float(scale_value),
                            out=compatibility_scalar,
                        )
                        density_tile[..., channel] += compatibility_scalar
                density_tile += layer_delta
                np.maximum(density_tile, 0.0, out=density_tile)
                del compatibility_scalar, density_tile
            del layer_delta, rgb_delta
    del processed_density_cmy
    del optical_layer_density
    optical_component_contract = {
        "formed_material": "FilmFinalMedium component densities",
        "layer_grain": "emulsion-only CMY/layer density delta -> dye absorption matrix",
        "chemical_stain": "separate deposit density -> RGB optical density",
        "surface_silver": "neutral broadband RGB optical density",
        "reticulation": (
            "gelatin/emulsion structural density -> neutral broadband RGB optical density"
        ),
        "retained_silver_grain": (
            "coordinate-stable neutral density -> RGB optical master"
            if silver_grain_plan is not None
            else "disabled; density component only"
        ),
    }
    del accident_maps

    medium_contract = developed_medium_contract(config)
    is_positive = str(medium_contract["image_polarity"]) == "positive"
    if discard_formation_layer_masters:
        # The private formed-layer buffer may already contain a grain delta.
        # The legacy adapter only inspects whether any density exists; retain
        # that frozen fact in a tiny proxy instead of keeping an image-sized
        # compatibility master alive for metadata construction.
        contract_density = np.full(
            (1, 1, 3),
            1.0 if formed_density_has_material else 0.0,
            dtype=np.float32,
        )
    else:
        contract_density = density_grain
    final_medium_contract = _legacy_final_medium_contract(
        contract_density,
        config,
        medium_contract,
    )
    del contract_density
    if reduced_process_payload is not None:
        final_medium_contract.update(reduced_process_payload)
        pool_observation = final_medium_contract.get("material_pool_optical_observation")
        optical_observation = final_medium_contract.get("optical_observation")
        if isinstance(pool_observation, dict) and isinstance(optical_observation, dict):
            # The process result owns final base/mask state. Preserve legacy
            # density anchors while replacing only material-pool optics.
            optical_observation.update(pool_observation)
    final_medium_contract["development_adjacency"] = adjacency_audit
    final_medium_contract["dir_material_response"] = dir_audit
    if has_silver_plating_map:
        final_medium_contract["surface_deposits"] = {
            "metallic_silver": {
                "representation": "neutral_broadband_surface_density_v1",
                "control_strength": float(config.chemistry.silver_plating),
                "source": "rapid_or_monobath_processing_accident",
            }
        }
    if "reticulation" in accident_map_name_set:
        final_medium_contract["emulsion_damage"] = {
            "reticulation": {
                "stage": "post_process_emulsion_structure_density",
                "representation": "bounded_gelatin_structure_density_v1",
                "control_strength": float(
                    config.chemistry.reticulation_strength
                ),
                "spectral_model": "neutral_broadband",
                "clear_support_affected": False,
                "scanner_owned": False,
                "temperature_shock_inferred_from_single_bath": False,
            }
        }
    final_medium_contract["material_degradation"] = {
        "strength": float(np.clip(config.film.material_degradation, 0.0, 1.0)),
        "speed_loss_stops_at_full_strength": float(config.film.degradation_speed_loss_stops),
        "fog_density_rgb_at_full_strength": tuple(
            float(value) for value in config.film.degradation_fog_density_rgb
        ),
        "layer_balance_at_full_strength": tuple(
            float(value) for value in config.film.degradation_layer_balance
        ),
    }
    final_medium_contract["extreme_exposure_latent_tail"] = {
        "stage": "material_exposure_to_latent_state",
        "enabled": bool(config.film.extreme_exposure_reversal_strength > 0.0),
        "strength": float(config.film.extreme_exposure_reversal_strength),
        "start_loge": tuple(
            float(value)
            for value in config.film.extreme_exposure_reversal_start_loge
        ),
        "transition_width_loge": float(config.film.extreme_exposure_reversal_width),
        "curve_join": "c1_smoothstep_from_ordinary_shoulder",
        "scope": "research_only_exceptional_material",
        "ordinary_stock_default": "disabled_shoulder_to_dmax",
        "not_sabattier_reexposure": True,
        "scanner_owned": False,
    }
    final_medium_contract["halation_formation"] = {
        "stage": "pre_latent_exposure",
        "enabled": bool(config.enable_halation),
        "applied": bool(
            config.enable_halation
            and float(config.film.halation_strength) > 0.0
            and (
                halation_return_model == "compatibility_rgb"
                or (
                    configured_layer_weights is not None
                    and float(configured_layer_weights.max(initial=0.0)) > 0.0
                )
            )
        ),
        "return_model": halation_return_model,
        "source_model": implementation_plan.halation_source_model,
        "execution_mode": implementation_plan.execution_mode,
        "strength": float(config.film.halation_strength),
        "layer_return_weights": (
            tuple(float(value) for value in config.film.halation_layer_return_weights)
            if halation_return_model == "layer_selective"
            else None
        ),
        "spread_scale_weights": (
            tuple(float(value) for value in config.film.halation_spread_scale_weights)
            if halation_return_model == "layer_selective"
            else None
        ),
        "legacy_after_halation_history": (
            "pre_layer_rgb_exposure"
            if halation_return_model == "layer_selective"
            else "compatibility_rgb_exposure_with_return"
        ),
    }
    final_medium_contract["emulsion_highlight_spread_formation"] = {
        **COMPACT_EMULSION_HIGHLIGHT_SPREAD_PLAN.as_dict(),
        "enabled": bool(config.film.emulsion_highlight_spread_strength > 0.0),
        "applied": bool(emulsion_spread_applied),
        "strength": float(config.film.emulsion_highlight_spread_strength),
        "radius_relative_to_short_edge": float(
            config.film.emulsion_highlight_spread_radius
        ),
        "highlight_threshold": float(
            config.film.emulsion_highlight_spread_threshold
        ),
        "highlight_softness": float(
            config.film.emulsion_highlight_spread_softness
        ),
        "layer_radius_scales": tuple(
            float(value)
            for value in config.film.emulsion_highlight_spread_layer_radius_scales
        ),
        "layer_strengths": tuple(
            float(value)
            for value in config.film.emulsion_highlight_spread_layer_strengths
        ),
        "work_shape": emulsion_spread_work_shape,
        "source_detection": "full_resolution_per_material_layer_before_work_grid",
        "destination": "material_layer_exposure_before_latent_curve",
        "support_or_backing_return": False,
        "scanner_owned": False,
    }
    final_medium_contract["silver_grain_formation"] = {
        "stage": "component_specific_optical_density",
        "enabled": bool(silver_grain_plan is not None),
        "applied": bool(independent_silver_grain_applied),
        "source_component": "FilmFinalMedium.metallic_silver",
        "destination": "derived_optical_density_rgb",
        "spectral_model": "neutral_broadband",
        "random_field": "global_coordinate_counter_v1",
        "tile_invariant": True,
        "strength": (
            None if silver_grain_plan is None else float(silver_grain_plan.strength)
        ),
        "radius": (
            None if silver_grain_plan is None else float(silver_grain_plan.radius)
        ),
        "clump_mix": (
            None if silver_grain_plan is None else float(silver_grain_plan.clump_mix)
        ),
        "legacy_density_grain_semantics": "compatibility_layer_master_only",
    }
    final_medium_contract["emulsion_grain_formation"] = {
        "stage": "material_layer_density",
        "enabled": bool(config.enable_grain and not independent_silver_only_grain),
        "applied": bool(config.enable_grain and not independent_silver_only_grain),
        "model": implementation_plan.emulsion_grain_model,
        "execution_mode": implementation_plan.execution_mode,
        "work_long_edge": _processing_work_long_edge(config, "grain"),
        "preserves_requested_output_shape": True,
        "source_component": "formed_material_layer_density",
        "scanner_owned": False,
    }
    final_medium_contract["execution_implementation"] = (
        implementation_plan.as_dict()
    )
    final_medium_contract["light_piping_formation"] = {
        **REDUCED_LIGHT_PIPING_PLAN.as_dict(),
        "enabled": bool(
            float(config.film.light_piping_strength) > 0.0
            and str(config.film.light_piping_edge_mode).strip().lower() != "none"
        ),
        "applied": bool(light_piping_applied),
        "edge_mode": str(config.film.light_piping_edge_mode).strip().lower(),
        "resolved_edge_weights_top_right_bottom_left": light_piping_edge_weights,
        "strength": float(config.film.light_piping_strength),
        "depth_scale": float(config.film.light_piping_depth),
        "layer_weights": tuple(
            float(value) for value in config.film.light_piping_layer_weights
        ),
        "source_policy": "declared_frame_edges_only",
        "reads_scene_pixels": False,
        "scanner_owned": False,
    }
    native_polarity = str(config.film.image_polarity).strip().lower()
    final_polarity = str(medium_contract["image_polarity"]).strip().lower()
    existing_cross = final_medium_contract.get("cross_process")
    cross_payload = dict(existing_cross) if isinstance(existing_cross, dict) else {}
    if native_polarity != final_polarity or cross_payload:
        cross_payload.update(
            {
                "material_process": config_medium_process(config),
                "program_process": str(medium_contract["medium_process"]),
                "material_polarity": native_polarity,
                "final_polarity": final_polarity,
            }
        )
        final_medium_contract["cross_process"] = cross_payload

    # History arrays have completed all formation-side reads.  Clamp their
    # private buffers in place and transfer them instead of allocating a clip
    # result and then copying that result again through astype().  Disabled MTF
    # or halation may make two histories alias; preserve the public fields as
    # independently mutable snapshots in that case.
    if discard_history:
        # Output-only callers have no consumer for these already-completed
        # formation stages. Keep explicit, independently owned empty sentinels
        # so the state shape is unambiguous without retaining three image-sized
        # buffers. Persisted and diagnostic media never select this policy.
        linear_input_output = np.empty((0, 0, 3), dtype=np.float32)
        after_mtf_output = np.empty((0, 0, 3), dtype=np.float32)
        after_halation_output = np.empty((0, 0, 3), dtype=np.float32)
    else:
        linear_input_output = np.asarray(linear_input, dtype=history_dtype)
        after_mtf_output = np.asarray(after_mtf_history, dtype=history_dtype)
        after_halation_output = np.asarray(after_halation_history, dtype=history_dtype)
        np.clip(linear_input_output, 0.0, 1.0, out=linear_input_output)
        np.clip(after_mtf_output, 0.0, 1.0, out=after_mtf_output)
        np.clip(after_halation_output, 0.0, 1.0, out=after_halation_output)
        if np.shares_memory(linear_input_output, after_mtf_output):
            after_mtf_output = after_mtf_output.copy()
        if np.shares_memory(linear_input_output, after_halation_output) or np.shares_memory(
            after_mtf_output,
            after_halation_output,
        ):
            after_halation_output = after_halation_output.copy()

    if discard_formation_layer_masters:
        # Immediate output has already transferred every layer/material
        # contribution into the authoritative RGB optical master. Do not
        # recreate two full compatibility masters solely for the caller to
        # discard at the next boundary.
        formed_density_cmy = np.empty((0, 0, 3), dtype=np.float32)
        density_grain = np.empty((0, 0, 3), dtype=np.float32)

    # All resident masters were allocated inside this formation call, so transfer
    # their existing float32 buffers into the returned state.  The historical
    # unconditional astype() calls copied up to two extra full RGB frames at
    # the return boundary.  Preserve the public mutation isolation contract if
    # a disabled-effect path legitimately aliases the formed/composite master,
    # and guard against the read-only optical snapshot sharing either buffer.
    formed_density_output = np.asarray(formed_density_cmy, dtype=np.float32)
    density_grain_output = np.asarray(density_grain, dtype=np.float32)
    optical_density_output = (
        None
        if formed_optical_density_rgb is None
        else np.asarray(formed_optical_density_rgb, dtype=np.float32)
    )
    if np.shares_memory(formed_density_output, density_grain_output):
        density_grain_output = density_grain_output.copy()
    if optical_density_output is not None:
        if np.shares_memory(optical_density_output, formed_density_output):
            formed_density_output = formed_density_output.copy()
        if np.shares_memory(optical_density_output, density_grain_output):
            density_grain_output = density_grain_output.copy()

    return DevelopedNegative(
        linear_input=linear_input_output,
        after_mtf=after_mtf_output,
        after_halation=after_halation_output,
        density_cmy=formed_density_output,
        density_grain=density_grain_output,
        optical_density_rgb=optical_density_output,
        clear_base_optical_density_rgb=(
            None
            if clear_base_optical_density_rgb is None
            else tuple(float(value) for value in clear_base_optical_density_rgb)
        ),
        medium_family=str(medium_contract["medium_family"]),
        medium_process=str(medium_contract["medium_process"]),
        image_polarity=str(medium_contract["image_polarity"]),
        view_mode=str(medium_contract["view_mode"]),
        base_type=str(medium_contract["base_type"]),
        color_system=str(medium_contract["color_system"]),
        compatible_interpreters=tuple(str(v) for v in medium_contract["compatible_interpreters"]),
        metadata={
            "runtime_config": config,
            "stage": "developed_positive_transparency" if is_positive else "developed_negative",
            "medium": config.medium,
            "medium_family": str(medium_contract["medium_family"]),
            "medium_process": str(medium_contract["medium_process"]),
            "image_polarity": str(medium_contract["image_polarity"]),
            "view_mode": str(medium_contract["view_mode"]),
            "base_type": str(medium_contract["base_type"]),
            "color_system": str(medium_contract["color_system"]),
            "compatible_interpreters": tuple(str(v) for v in medium_contract["compatible_interpreters"]),
            "film_process_model": final_medium_contract,
            "density_formation": str(final_medium_contract["representation"]),
            "light_leak_strength": float(config.chemistry.light_leak_strength),
            "light_leak_template": str(config.chemistry.light_leak_template),
            "light_leak_secondary_template": str(
                config.chemistry.light_leak_secondary_template
            ),
            "light_leak_mix": str(config.chemistry.light_leak_mix),
            "light_leak_mask_path": str(config.chemistry.light_leak_mask_path),
            "light_leak_placement": str(config.chemistry.light_leak_placement),
            "light_leak_pattern": (
                None
                if light_leak_pattern is None
                else asdict(
                    resolve_light_leak_pattern(
                        light_leak_pattern,
                        config.chemistry,
                    )
                )
            ),
            "light_leak_secondary_pattern": (
                None
                if light_leak_secondary_pattern is None
                else asdict(light_leak_secondary_pattern)
            ),
            "chemical_stain": float(config.chemistry.chemical_stain),
            "silver_plating": float(config.chemistry.silver_plating),
            "uneven_development": float(config.chemistry.uneven_development),
            "reticulation_strength": float(
                config.chemistry.reticulation_strength
            ),
            "dye_damage_strength": float(config.chemistry.dye_damage_strength),
            "exposure_occlusion_dust": float(config.chemistry.exposure_occlusion_dust),
            "exposure_occlusion_hair": float(config.chemistry.exposure_occlusion_hair),
            "exposure_occlusion_insect": float(config.chemistry.exposure_occlusion_insect),
            "exposure_occlusion_debris": float(config.chemistry.exposure_occlusion_debris),
            "exposure_occlusion_identity_seed": config.chemistry.exposure_occlusion_identity_seed,
            "has_exposure_occlusion_map": has_exposure_occlusion_map,
            "imprint_style": str(config.film_border.imprint_style),
            "imprint_side": str(config.film_border.imprint_side),
            "has_edge_imprint_map": has_edge_imprint_map,
            "development_adjacency_strength": float(
                config.chemistry.development_adjacency_strength
            ),
            "development_adjacency": adjacency_audit,
            "dir_material_response": dir_audit,
            "has_light_leak_map": has_light_leak_map,
            "accident_maps": accident_map_names,
            "accident_stages": {
                "exposure_occlusion": "pre_latent_exposure",
                "edge_imprint": "pre_latent_exposure",
                "light_leak": "pre_latent_exposure",
                "uneven_development": "development_formation",
                "chemical_stain": "post_process_component_density",
                "silver_plating": "post_process_surface_silver_density",
                "reticulation": "post_process_emulsion_structure_density",
                "dye_damage": "process_operator_material_dye_pool",
            },
            "optical_master_role": "derived_immutable_scanner_input",
            "optical_master_components": optical_component_contract,
            **(
                {
                    "stage_storage": {
                        "profile": "scan_optical_only_v1",
                        "layer_masters": "discarded_during_optical_compose",
                        "optical_master": "resident_authoritative",
                        "reversible_full_path": (
                            "retain_layer_masters=True or reference topology"
                        ),
                    }
                }
                if discard_formation_layer_masters
                else {}
            ),
            **(
                {
                    "prototype": "unified_silver_halide_pool_v1",
                    "positive_density_contrast": float(config.film.positive_density_contrast),
                    "positive_density_bias": float(config.film.positive_density_bias),
                    "positive_latitude_compression": float(config.film.positive_latitude_compression),
                    "positive_dye_saturation": float(config.film.positive_dye_saturation),
                    "positive_midtone_density": float(config.film.positive_midtone_density),
                    "positive_shadow_toe": float(config.film.positive_shadow_toe),
                    "positive_shadow_toe_width": float(config.film.positive_shadow_toe_width),
                    "positive_highlight_shoulder": float(config.film.positive_highlight_shoulder),
                    "positive_highlight_shoulder_width": float(config.film.positive_highlight_shoulder_width),
                    "positive_highlight_chroma_retention": float(config.film.positive_highlight_chroma_retention),
                    "positive_shadow_chroma_retention": float(config.film.positive_shadow_chroma_retention),
                    "mask_bleach_completion": float(config.chemistry.mask_bleach_completion),
                }
                if is_positive
                else {}
            ),
        },
    )


def _positive_transparency_density_from_negative_proxy(
    negative_density: np.ndarray,
    config: DarkroomConfig,
) -> np.ndarray:
    """Convert the shared H-D exposure proxy into a slide-like positive density."""
    d_min = np.asarray(config.film.density_min, dtype=np.float32).reshape(1, 1, 3)
    d_max = np.asarray(config.film.density_max, dtype=np.float32).reshape(1, 1, 3)
    density_range = np.maximum(d_max - d_min, 1e-6)

    negative_norm = np.clip((np.asarray(negative_density, dtype=np.float32) - d_min) / density_range, 0.0, 1.0)
    positive_norm = 1.0 - negative_norm

    contrast = max(float(getattr(config.film, "positive_density_contrast", 1.0)), 0.01)
    positive_norm = (positive_norm - 0.5) * contrast + 0.5
    positive_norm = positive_norm + float(getattr(config.film, "positive_density_bias", 0.0))

    latitude = float(np.clip(getattr(config.film, "positive_latitude_compression", 0.0), 0.0, 1.0))
    if latitude > 0.0:
        smooth = positive_norm * positive_norm * (3.0 - 2.0 * positive_norm)
        positive_norm = positive_norm * (1.0 - latitude) + smooth * latitude

    midtone_density = float(np.clip(getattr(config.film, "positive_midtone_density", 0.0), 0.0, 1.0))
    if midtone_density > 0.0:
        midtone_weight = 1.0 - np.clip(np.abs(positive_norm - 0.5) / 0.5, 0.0, 1.0)
        midtone_weight = midtone_weight * midtone_weight * (3.0 - 2.0 * midtone_weight)
        positive_norm = positive_norm + midtone_density * 0.20 * midtone_weight

    shadow_toe = float(np.clip(getattr(config.film, "positive_shadow_toe", 0.0), 0.0, 1.0))
    shadow_toe_width = float(np.clip(getattr(config.film, "positive_shadow_toe_width", 0.22), 0.02, 0.80))
    if shadow_toe > 0.0:
        dense_zone = np.clip((positive_norm - (1.0 - shadow_toe_width)) / shadow_toe_width, 0.0, 1.0)
        dense_zone = dense_zone * dense_zone * (3.0 - 2.0 * dense_zone)
        density_relief = shadow_toe * shadow_toe_width * 0.45 * dense_zone
        positive_norm = positive_norm - density_relief

    shoulder = float(np.clip(getattr(config.film, "positive_highlight_shoulder", 0.0), 0.0, 1.0))
    shoulder_width = float(np.clip(getattr(config.film, "positive_highlight_shoulder_width", 0.18), 0.02, 0.80))
    if shoulder > 0.0:
        thin_zone = 1.0 - np.clip(positive_norm / shoulder_width, 0.0, 1.0)
        thin_zone = thin_zone * thin_zone * (3.0 - 2.0 * thin_zone)
        density_lift = shoulder * shoulder_width * 0.55 * thin_zone
        positive_norm = positive_norm + density_lift

    positive_norm = shape_positive_dye_chroma(
        np.clip(positive_norm, 0.0, 1.0),
        config.film,
    )
    density_cmy = d_min + positive_norm * density_range
    return np.clip(density_cmy, d_min, d_max).astype(np.float32, copy=False)



from film_foundry.core.scan_runtime import (
    _normalize_encode_output_tiled,
    _scan_medium_output_only,
    _scan_negative_output_only,
    _scan_negative_output_tiled,
    _scan_negative_pipeline,
    _scan_output_tile_rows,
    _scan_positive_output_only,
    _scan_positive_output_tiled,
    _scan_positive_transparency_pipeline,
    _scan_scanner_raw_array,
    configure_scan_interpretation,
    scan_medium,
    scan_medium_direct,
    scan_medium_output,
    scan_negative,
    scan_scanner_raw,
    scan_scanner_raw_direct,
)
import film_foundry.core.scan_runtime as _scan_runtime


class _ForwardEngineName:
    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, *args, **kwargs):
        return globals()[self.name](*args, **kwargs)


_scan_runtime._bind_engine_services({
    name: _ForwardEngineName(name) for name in _scan_runtime._ENGINE_SERVICE_NAMES
})

develop_medium = develop_negative
scan_medium = scan_negative


def _process_array_with_runtime(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray], DarkroomConfig, DevelopedNegative]:
    """处理数组并返回关键中间结果，供 debug 输出使用。"""
    config = _runtime_config(config)
    config.processing.history_storage_policy = "full"
    if rng is None:
        rng, _ = _rng_for_input(None, config)
    negative = develop_negative(image_srgb, config, rng=rng, _prepared_config=True)
    runtime_config = negative.metadata.get("runtime_config", config)
    if not isinstance(runtime_config, DarkroomConfig):
        runtime_config = config
    interpretation_mode = str(config.scanner.interpretation_mode).strip().lower()
    if interpretation_mode in {"", "auto"}:
        # Auto full workflow follows the newly formed medium contract.
        scanned = scan_negative(negative)
    else:
        # Manual full workflow changes observation only; the developed medium
        # and its saved polarity/chemistry remain untouched.
        scanned = scan_medium_direct(negative, runtime_config, interpretation_mode)
    scanned_runtime = scanned.metadata.get("runtime_config")
    if isinstance(scanned_runtime, DarkroomConfig):
        runtime_config = scanned_runtime
    stages: dict[str, np.ndarray] = {
        "01_linear_input": negative.linear_input,
        "02_after_mtf": negative.after_mtf,
        "03_after_halation": negative.after_halation,
        "04_density_cmy": negative.density_cmy,
        "04_positive_no_grain": scanned.positive_no_grain,
        "05_density_grain": negative.density_grain,
        "06_negative_total_density": scanned.negative_total_density,
        "07_negative_linear": scanned.negative_linear,
        "08_negative_base_balanced": scanned.negative_base_balanced,
        "09_positive_raw": scanned.positive_raw,
        "09b_negative_channel_reconstructed": scanned.negative_channel_reconstructed,
        "10_positive_linear": scanned.positive_linear,
        "11_output_srgb": scanned.output_srgb,
    }
    return scanned.output_srgb, stages, runtime_config, negative


def _process_output_with_runtime(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
    *,
    retain_development_stages: bool = False,
    retain_cold_history: bool = True,
    retain_layer_masters: bool = True,
    _stage_observer: Callable[[str], None] | None = None,
) -> tuple[np.ndarray, DarkroomConfig, DevelopedNegative]:
    """Production path: form the full medium, retain only the observed output."""
    config = _runtime_config(config)
    if retain_development_stages:
        config.processing.history_storage_policy = "full"
    elif retain_cold_history:
        config.processing.history_storage_policy = "cold_fp16"
    else:
        config.processing.history_storage_policy = "discard"
    if rng is None:
        rng, _ = _rng_for_input(None, config)
    retain_layers_during_formation = bool(
        retain_development_stages
        or retain_layer_masters
        or reference_execution_enabled()
    )
    retention_token = _RETAIN_FORMATION_LAYER_MASTERS.set(
        retain_layers_during_formation
    )
    try:
        medium = develop_negative(
            image_srgb,
            config,
            rng=rng,
            _prepared_config=True,
            _allow_discarded_history=not retain_cold_history,
        )
    finally:
        _RETAIN_FORMATION_LAYER_MASTERS.reset(retention_token)
    runtime_config = medium.metadata.get("runtime_config", config)
    if not isinstance(runtime_config, DarkroomConfig):
        runtime_config = config
    if not retain_development_stages and retain_cold_history:
        # These three arrays describe already-consumed historical stages. They
        # are not read by scanning or by the default medium/transparent export.
        # Keeping cold copies in FP16 halves their resident size while both
        # authoritative density masters and every active calculation remain
        # FP32. Diagnostic and plate/layer-pack paths retain the original FP32.
        # The formation pipeline already compacted these histories immediately
        # after their last active consumer. Keep a defensive dtype assertion at
        # this public boundary without creating another full-frame copy.
        medium.linear_input = np.asarray(medium.linear_input, dtype=np.float16)
        medium.after_mtf = np.asarray(medium.after_mtf, dtype=np.float16)
        medium.after_halation = np.asarray(medium.after_halation, dtype=np.float16)
        medium.metadata["stage_storage"] = {
            "policy": "production_mixed_precision_v1",
            "cold_history_dtype": "float16",
            "authoritative_density_dtype": "float32",
            "cold_history_consumers": [],
        }
    elif not retain_development_stages:
        # Registered media pipelines may implement the discard policy at
        # formation time (the unified silver-halide pipeline does) or return
        # ordinary histories for this shared boundary to release. This keeps
        # the registry contract extensible without weakening the output-only
        # retention guarantee.
        medium.linear_input = np.empty((0, 0, 3), dtype=np.float32)
        medium.after_mtf = np.empty((0, 0, 3), dtype=np.float32)
        medium.after_halation = np.empty((0, 0, 3), dtype=np.float32)
        medium.metadata["stage_storage"] = {
            "policy": "output_only_discarded_history_v1",
            "cold_history_dtype": None,
            "authoritative_density_dtype": "float32",
            "discarded_histories": [
                "linear_input",
                "after_mtf",
                "after_halation",
            ],
        }
    if _stage_observer is not None:
        _stage_observer("after_develop")
    if (
        not retain_development_stages
        and not retain_layer_masters
        and medium.optical_density_rgb is not None
    ):
        # Immediate array output has no layer-export, resave, or Inspector
        # consumer. The authoritative optical master is already immutable, so
        # releasing the two compatibility layers changes only residency. File
        # processing and the default internal API keep them unless the caller
        # opts into this explicit ownership transfer.
        discarded_during_formation = not medium.layer_masters_available
        medium.density_cmy = np.empty((0, 0, 3), dtype=np.float32)
        medium.density_grain = np.empty((0, 0, 3), dtype=np.float32)
        storage = dict(medium.metadata.get("stage_storage", {}))
        storage.update({
            "profile": "scan_optical_only_v1",
            "layer_masters": (
                "discarded_during_optical_compose"
                if discarded_during_formation
                else "discarded_after_optical_compose"
            ),
            "optical_master": "resident_authoritative",
            "reversible_full_path": "retain_layer_masters=True",
        })
        medium.metadata["stage_storage"] = storage
    output, scan_runtime = _scan_medium_output_only(
        medium,
        runtime_config,
        str(config.scanner.interpretation_mode),
    )
    if _stage_observer is not None:
        _stage_observer("after_scan")
    return output, scan_runtime, medium


def process_array_with_stages(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """处理数组并返回关键中间结果，供 debug 输出使用。"""
    output, stages, _, _ = _process_array_with_runtime(image_srgb, config, rng)
    return output, stages


def process_array(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """处理一张已经归一化到 [0, 1] 的 sRGB 图像数组。"""
    output, _, _ = _process_output_with_runtime(
        image_srgb,
        config,
        rng=rng,
        retain_cold_history=False,
        retain_layer_masters=False,
    )
    return output


def _save_debug_outputs(
    output_path: Path,
    image_srgb: np.ndarray,
    stages: dict[str, np.ndarray],
    config: DarkroomConfig,
) -> None:
    debug_dir = output_path.with_suffix("")
    debug_dir = debug_dir.parent / f"{debug_dir.name}_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    save_image(image_srgb, debug_dir / "00_original.png", config.output)
    for name, array in stages.items():
        if "density" in name:
            np.save(debug_dir / f"{name}.npy", array)
            if name == "06_negative_total_density":
                save_image(_rgb_density_preview(array), debug_dir / f"{name}_density_debug.png", config.output)
            else:
                save_image(_density_preview(array, config), debug_dir / f"{name}_density_debug.png", config.output)
            if name == "04_density_cmy":
                save_image(negative_visual_preview(array, config.film), debug_dir / "10_negative_visual_no_grain.png", config.output)
            if name == "05_density_grain":
                save_image(negative_visual_preview(array, config.film), debug_dir / "11_negative_visual.png", config.output)
        else:
            is_srgb = name == "11_output_srgb"
            preview = array if is_srgb else linear_to_srgb(array)
            save_image(preview, debug_dir / f"{name}.png", config.output)

    if config.comparison_grid:
        original = image_srgb
        after_mtf = linear_to_srgb(stages["02_after_mtf"])
        no_grain = linear_to_srgb(stages["04_positive_no_grain"])
        final = stages["11_output_srgb"]
        min_h = min(original.shape[0], after_mtf.shape[0], no_grain.shape[0], final.shape[0])
        min_w = min(original.shape[1], after_mtf.shape[1], no_grain.shape[1], final.shape[1])
        cells = [img[:min_h, :min_w] for img in (original, after_mtf, no_grain, final)]
        grid = np.vstack((np.hstack((cells[0], cells[1])), np.hstack((cells[2], cells[3]))))
        save_image(grid, debug_dir / "comparison_grid.png", config.output)


def _save_sidecar(
    input_path: Path,
    output_path: Path,
    config: DarkroomConfig,
    resolved_seed: int | None,
    preview: bool,
) -> None:
    provenance = None
    if config.output.watermark_metadata:
        provenance = provenance_payload(
            stage="final_positive",
            input_path=input_path,
            output_path=output_path,
            resolved_seed=resolved_seed,
        )
    sidecar = final_positive_sidecar(
        input_path=input_path,
        output_path=output_path,
        config=config,
        resolved_seed=resolved_seed,
        preview=preview,
        provenance=provenance,
    )
    sidecar_path = output_path.with_suffix(output_path.suffix + ".json")
    atomic_write_json(sidecar_path, sidecar)


def _is_positive_developed_medium(medium: DevelopedNegative) -> bool:
    return str(getattr(medium, "image_polarity", "")).lower() == "positive"


def _developed_medium_path(input_path: Path, output_path: Path, medium: DevelopedNegative) -> Path:
    if _is_positive_developed_medium(medium):
        return output_path.parent / POSITIVE_MATERIAL_DIR_NAME / f"{input_path.stem}{POSITIVE_SUFFIX}"
    return output_path.parent / NEGATIVE_MATERIAL_DIR_NAME / f"{input_path.stem}{NEGATIVE_SUFFIX}"


def _developed_medium_bundle_targets(
    negative_path: Path,
) -> tuple[Path, ...]:
    """Return the complete managed namespace for one medium generation.

    Disabled products are included deliberately.  On successful replacement,
    stale raw/sidecar/material directories from an older configuration are
    removed; on failure the complete older generation is restored.
    """
    material_base = negative_path.with_suffix("")
    scanner_raw = negative_path.with_suffix(".scanner_raw.tiff")
    light_table_raw = negative_path.with_suffix(".light_table_raw.tiff")
    return (
        negative_path,
        negative_path.with_suffix(".negative_visual.png"),
        negative_path.with_suffix(".positive_visual.png"),
        scanner_raw,
        scanner_raw.with_suffix(scanner_raw.suffix + ".json"),
        light_table_raw,
        light_table_raw.with_suffix(light_table_raw.suffix + ".json"),
        negative_path.with_suffix(negative_path.suffix + ".json"),
        material_base.parent / f"{material_base.name}_layer_pack",
        material_base.parent / f"{material_base.name}_transparent_plate",
        material_base.parent / f"{material_base.name}_plate_set",
    )


def _assert_output_set_safe(input_path: Path, targets: tuple[Path, ...]) -> None:
    source = input_path.resolve()
    for target in targets:
        resolved = target.resolve()
        if resolved == source or resolved in source.parents:
            raise ValueError(
                "Output paths must not overwrite the source input or replace a directory containing it: "
                f"{target}"
            )


def _save_full_developed_medium_materials(
    input_path: Path,
    output_path: Path,
    negative: DevelopedNegative,
    config: DarkroomConfig,
    resolved_seed: int | None,
    developed_path: Path | None = None,
    _transactional: bool = True,
    _export_config_prepared: bool = False,
) -> dict[str, str]:
    """Save the reusable developed medium created by the full pipeline."""
    if not _export_config_prepared:
        config = _config_for_developed_medium_export(negative, config)
    _validate_develop_runtime(config)
    _validate_output_runtime(config)
    _validate_developed_medium_state(negative)
    is_positive = _is_positive_developed_medium(negative)
    if is_positive or config.output.save_scanner_raw:
        _validate_scan_runtime(config)
    negative_path = developed_path or _developed_medium_path(input_path, output_path, negative)
    bundle_targets = _developed_medium_bundle_targets(negative_path)
    _assert_output_set_safe(input_path, bundle_targets)
    if _transactional:
        with atomic_path_set(bundle_targets):
            return _save_full_developed_medium_materials(
                input_path,
                output_path,
                negative,
                config,
                resolved_seed,
                developed_path=negative_path,
                _transactional=False,
                _export_config_prepared=True,
            )
    negative_path.parent.mkdir(parents=True, exist_ok=True)
    material_key = "positive_path" if is_positive else "negative_path"
    visual_suffix = ".positive_visual.png" if is_positive else ".negative_visual.png"
    provenance_stage = "developed_positive_transparency" if is_positive else "developed_negative"
    provenance = provenance_payload(
        stage=provenance_stage,
        input_path=input_path,
        output_path=output_path,
        negative_path=negative_path,
        resolved_seed=resolved_seed,
    )
    npz_payload: dict[str, np.ndarray] = {
        "density_cmy": np.asarray(negative.density_cmy, dtype=np.float32),
        "density_grain": np.asarray(negative.density_grain, dtype=np.float32),
        "developed_medium_metadata": np.asarray(
            json.dumps(developed_medium_metadata(negative), ensure_ascii=False, allow_nan=False)
        ),
    }
    if negative.optical_density_rgb is not None:
        npz_payload["optical_density_rgb"] = np.asarray(
            negative.optical_density_rgb,
            dtype=np.float32,
        )
    if negative.clear_base_optical_density_rgb is not None:
        npz_payload["clear_base_optical_density_rgb"] = np.asarray(
            negative.clear_base_optical_density_rgb,
            dtype=np.float32,
        )
    if config.output.watermark_negative_material:
        npz_payload["film_foundry_provenance"] = provenance_npz_array(payload_with_config(provenance, asdict(config)))
    atomic_savez(
        negative_path,
        compressed=(
            str(config.output.medium_npz_compression).strip().lower()
            == "compressed"
        ),
        **npz_payload,
    )
    del npz_payload

    paths: dict[str, str] = {material_key: str(negative_path)}
    if is_positive:
        paths["negative_path"] = str(negative_path)
    preview_path = negative_path.with_suffix(visual_suffix)
    positive_transmission_raw: np.ndarray | None = None
    if is_positive:
        positive_transmission_raw = capture_optical_density(
            _medium_optical_density_rgb(negative, config.film),
            config.scanner,
            illuminant_rgb=transmission_illuminant_rgb(config.scanner),
        )
        preview_linear = positive_transmission_raw
        save_image(linear_to_srgb(np.clip(preview_linear, 0.0, 1.0)), preview_path, config.output)
        del preview_linear
        paths["positive_visual_preview"] = str(preview_path)
    else:
        preview = (
            negative_visual_preview(negative.density_grain, config.film)
            if negative.optical_density_rgb is None
            else optical_density_visual_preview(negative.optical_density_rgb)
        )
        save_image(preview, preview_path, config.output)
        del preview
        paths["negative_visual_preview"] = str(preview_path)

    scanner_raw_path: Path | None = None
    scanner_raw_border_px = 0
    if config.output.save_scanner_raw:
        scanner_raw_border_px = scanner_raw_export_border_width(
            negative.density_grain.shape,
            config.output.scanner_raw_border_percent,
            config.output.scanner_raw_border_min_px,
        )
        if is_positive:
            scanner_raw = positive_transmission_raw
            if scanner_raw is None:
                scanner_raw = capture_optical_density(
                    _medium_optical_density_rgb(negative, config.film),
                    config.scanner,
                    illuminant_rgb=transmission_illuminant_rgb(config.scanner),
                )
            scanner_raw = scanner_raw_with_reference_border(
                scanner_raw,
                _clear_base_scanner_sample(config, negative).reshape(3),
                border_percent=config.output.scanner_raw_border_percent,
                border_min_px=config.output.scanner_raw_border_min_px,
            )
            scanner_raw_path = negative_path.with_suffix(".light_table_raw.tiff")
        else:
            scanner_raw = scanner_raw_with_clear_border(
                negative.density_grain,
                config.film,
                config.scanner,
                border_percent=config.output.scanner_raw_border_percent,
                border_min_px=config.output.scanner_raw_border_min_px,
                optical_density_rgb=negative.optical_density_rgb,
                clear_base_optical_density_rgb=negative.clear_base_optical_density_rgb,
            )
            scanner_raw_path = negative_path.with_suffix(".scanner_raw.tiff")
        if config.output.watermark_scanner_raw_border:
            scanner_raw = apply_scanner_raw_border_watermark(
                scanner_raw,
                provenance,
                border_width=scanner_raw_border_px,
            )
        save_linear_rgb_tiff(scanner_raw, scanner_raw_path)
        del scanner_raw
        paths["light_table_raw_path" if is_positive else "scanner_raw_path"] = str(scanner_raw_path)
        if config.save_sidecar:
            scanner_raw_sidecar_path = scanner_raw_path.with_suffix(scanner_raw_path.suffix + ".json")
            atomic_write_json(
                scanner_raw_sidecar_path,
                scanner_raw_sidecar(
                    input_path=input_path,
                    negative_path=negative_path,
                    scanner_raw_path=scanner_raw_path,
                    config=config,
                    negative=negative,
                    provenance=provenance if config.output.watermark_metadata else None,
                    border_width_px=scanner_raw_border_px,
                ),
            )
            paths["scanner_raw_sidecar"] = str(scanner_raw_sidecar_path)
    positive_transmission_raw = None

    material_base = negative_path.with_suffix("")
    material_paths: dict[str, str] = {}
    if config.output.export_layer_pack:
        material_paths.update(
            export_layer_pack(
                negative,
                config.film,
                material_base.parent / f"{material_base.name}_layer_pack",
                polarity="positive" if is_positive else "negative",
                source_negative_path=negative_path,
                scanner_raw_path=scanner_raw_path,
                orange_preview_path=preview_path,
                metadata={
                    **layer_pack_metadata(
                        input_path=input_path,
                        output_path=output_path,
                        negative_path=negative_path,
                        config=config,
                        negative=negative,
                        paths=paths,
                    ),
                },
            )
        )
    else:
        if config.output.export_transparent_plate:
            material_paths.update(
                export_transparent_plate_set(
                    negative.density_grain,
                    config.film,
                    material_base.parent / f"{material_base.name}_transparent_plate",
                    polarity="positive" if is_positive else "negative",
                    optical_density_rgb=negative.optical_density_rgb,
                    clear_base_optical_density_rgb=negative.clear_base_optical_density_rgb,
                )
            )
        if config.output.export_plate_set:
            material_paths.update(
                export_plate_set(
                    negative.density_cmy,
                    negative.density_grain,
                    negative.after_mtf,
                    negative.after_halation,
                    config.film,
                    material_base.parent / f"{material_base.name}_plate_set",
                )
            )
    paths.update({f"material:{key}": value for key, value in material_paths.items()})

    if config.save_sidecar:
        sidecar_path = negative_path.with_suffix(negative_path.suffix + ".json")
        atomic_write_json(
            sidecar_path,
            developed_negative_sidecar(
                input_path=input_path,
                output_path=output_path,
                negative_path=negative_path,
                config=config,
                negative=negative,
                paths=paths,
                resolved_seed=resolved_seed,
                provenance=provenance if config.output.watermark_metadata else None,
            ),
        )
        paths["sidecar"] = str(sidecar_path)
    return paths


def _save_full_negative_materials(
    input_path: Path,
    output_path: Path,
    negative: DevelopedNegative,
    config: DarkroomConfig,
    resolved_seed: int | None,
) -> dict[str, str]:
    """Compatibility wrapper for older callers/tests."""
    return _save_full_developed_medium_materials(input_path, output_path, negative, config, resolved_seed)


def save_developed_medium_materials(
    input_path: str | Path,
    output_path: str | Path,
    medium: DevelopedNegative,
    config: DarkroomConfig,
    resolved_seed: int | None = None,
) -> dict[str, str]:
    """Save reusable electronic negative/positive medium files for tools and GUIs."""
    return _save_full_developed_medium_materials(Path(input_path), Path(output_path), medium, config, resolved_seed)


def save_developed_medium_at_path(
    input_path: str | Path,
    developed_path: str | Path,
    medium: DevelopedNegative,
    config: DarkroomConfig,
    resolved_seed: int | None = None,
) -> dict[str, str]:
    """Save a developed medium at an explicit path using the unified exporter."""
    developed_path = Path(developed_path)
    input_path = Path(input_path)
    if developed_path.resolve() == input_path.resolve():
        raise ValueError("Developed-medium output path must not overwrite the source input file.")
    return _save_full_developed_medium_materials(
        input_path,
        developed_path,
        medium,
        config,
        resolved_seed,
        developed_path=developed_path,
    )


def process_file(
    input_path: str | Path,
    output_path: str | Path,
    config: DarkroomConfig | None = None,
    preview: bool = False,
) -> Path:
    """读取、处理并保存一张图像文件。

    ``preview=True`` 是旧兼容入口；它开启按目标长边缩放，而不是专门生成
    某种 GUI 预览产品。缩放不会关闭独立的处理质量或低阶算子开关。
    """
    config = config or DarkroomConfig()
    execution_axes = resolve_execution_axes(config, scaled_override=preview)
    # Persist a backward-compatible representation of the orthogonal axes in
    # runtime metadata while leaving the caller's reusable configuration
    # untouched. ``scaled_fast + fast_mode=True`` represents the combined run.
    stored_execution_mode = (
        "scaled_fast"
        if execution_axes.scaling_enabled
        else ("reduced_fast" if execution_axes.lower_order_enabled else "quality")
    )
    if (
        str(config.processing.execution_mode).strip().lower()
        != stored_execution_mode
        or bool(config.fast_mode) != execution_axes.lower_order_enabled
    ):
        config = copy.deepcopy(config)
        config.processing.execution_mode = stored_execution_mode
        config.fast_mode = execution_axes.lower_order_enabled
    _validate_output_runtime(config)
    input_path = Path(input_path)
    output_path = Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Final output path must not overwrite the source input image.")
    long_edge = processing_long_edge(config)
    source_width, source_height = probe_image_dimensions(input_path)
    retain_development_stages = bool(
        config.output.export_plate_set or config.output.export_layer_pack
    )
    memory_estimate = estimate_pipeline_memory(
        source_width,
        source_height,
        long_edge=long_edge,
        diagnostic=bool(config.debug_output),
        retain_development_stages=retain_development_stages,
        comfort_zone_megapixels=config.processing.comfort_zone_megapixels,
        decoder_reduced=(
            execution_axes.scaling_enabled
            and input_path.suffix.lower() in {".jpg", ".jpeg"}
        ),
    )
    warn_outside_comfort_zone(memory_estimate)
    enforce_memory_budget(
        memory_estimate,
        config.processing.memory_budget_mb,
        config.processing.memory_budget_policy,
    )
    if execution_axes.scaling_enabled:
        image, input_color = load_image_with_color(
            input_path,
            decode_long_edge=long_edge,
            input_icc_policy=config.input_icc_policy,
        )
    else:
        image, input_color = load_image_with_color(
            input_path,
            input_icc_policy=config.input_icc_policy,
        )
    image = resize_to_long_edge(image, long_edge)
    rng, resolved_seed = _rng_for_input(input_path, config)
    if config.debug_output:
        result, stages, runtime_config, negative = _process_array_with_runtime(
            image,
            config,
            rng=rng,
        )
    else:
        result, runtime_config, negative = _process_output_with_runtime(
            image,
            config,
            rng=rng,
            retain_development_stages=retain_development_stages,
        )
        stages = None
        # The source working image has no remaining consumer once formation
        # and observation complete. Release it before output encoding and the
        # developed-medium export allocate their own buffers.
        del image
    negative.metadata["input_color"] = input_color
    developed_path = _developed_medium_path(input_path, output_path, negative)
    transaction_targets = [
        output_path,
        *_developed_medium_bundle_targets(developed_path),
    ]
    if runtime_config.debug_output:
        debug_base = output_path.with_suffix("")
        transaction_targets.append(debug_base.parent / f"{debug_base.name}_debug")
    if runtime_config.save_sidecar:
        transaction_targets.append(output_path.with_suffix(output_path.suffix + ".json"))
    transaction_tuple = tuple(transaction_targets)
    _assert_output_set_safe(input_path, transaction_tuple)

    with atomic_path_set(transaction_tuple):
        save_image(result, output_path, runtime_config.output)
        del result
        _save_full_developed_medium_materials(
            input_path,
            output_path,
            negative,
            runtime_config,
            resolved_seed,
            developed_path=developed_path,
            _transactional=False,
        )

        if runtime_config.debug_output:
            if stages is None:
                raise RuntimeError("Debug output requires retained diagnostic stages.")
            _save_debug_outputs(output_path, image, stages, runtime_config)
        if runtime_config.save_sidecar:
            _save_sidecar(
                input_path,
                output_path,
                runtime_config,
                resolved_seed,
                execution_axes.scaling_enabled,
            )
    return output_path


install_default_media_pipelines(
    negative_develop=_develop_silver_halide_pipeline,
    negative_scan=_scan_negative_pipeline,
    positive_transparency_develop=_develop_silver_halide_pipeline,
    positive_transparency_scan=_scan_positive_transparency_pipeline,
)
