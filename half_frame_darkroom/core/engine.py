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

from half_frame_darkroom.core.accidents import (
    apply_density_accident_components,
    apply_light_leak_to_exposure,
    compose_density_accident_master,
    uneven_development_rate_field,
)
from half_frame_darkroom.core.atomic_io import atomic_path_set, atomic_savez, atomic_write_json
from half_frame_darkroom.core.color import linear_to_srgb, luminance, srgb_to_linear
from half_frame_darkroom.core.density_grain import apply_density_grain
from half_frame_darkroom.core.development import build_effective_development
from half_frame_darkroom.core.development_adjacency import (
    build_development_adjacency_field,
)
from half_frame_darkroom.core.halation import apply_halation, halation_return_field
from half_frame_darkroom.core.io_utils import load_image, probe_image_dimensions, save_image
from half_frame_darkroom.core.light_piping import (
    LIGHT_PIPING_EDGE_MODES,
    REDUCED_LIGHT_PIPING_PLAN,
    light_piping_exposure_field,
)
from half_frame_darkroom.core.electronic_negative import (
    export_layer_pack,
    export_plate_set,
    export_transparent_plate_set,
    save_linear_rgb_tiff,
    scanner_raw_export_border_width,
    scanner_raw_with_reference_border,
    scanner_raw_with_clear_border,
)
from half_frame_darkroom.core.execution import (
    processing_long_edge,
    resolve_execution_mode,
    uses_reduced_implementation,
)
from half_frame_darkroom.core.execution_topology import reference_execution_enabled
from half_frame_darkroom.core.film_process.integration import (
    develop_bw_negative_reduced,
    develop_bw_reversal_reduced,
    develop_color_negative_reduced,
    develop_color_reversal_reduced,
    shape_positive_dye_chroma,
)
from half_frame_darkroom.core.film_process.recipe import (
    process_program_payload,
    program_from_develop_recipe,
    resolve_program_key,
)
from half_frame_darkroom.core.media import (
    config_medium_process,
    developed_medium_contract,
    is_monochrome_material,
    is_monochrome_process,
)
from half_frame_darkroom.core.media_registry import get_develop_pipeline, get_scan_pipeline, install_default_media_pipelines
from half_frame_darkroom.core.mtf import apply_emulsion_mtf
from half_frame_darkroom.core.preview import (
    negative_visual_preview,
    optical_density_visual_preview,
    resize_to_long_edge,
)
from half_frame_darkroom.core.resource_planning import (
    enforce_memory_budget,
    estimate_pipeline_memory,
    exact_material_tiling_required,
    warn_outside_comfort_zone,
)
from half_frame_darkroom.core.runtime_resources import configure_native_thread_limit
from half_frame_darkroom.core.spatial_fields import (
    LAZY_LAYER_EXPOSURE_FIELD_TYPES,
    LayerExposureAdditionField,
    StepDevelopmentRateField,
    combine_layer_exposure_addition_fields,
)
from half_frame_darkroom.core.silver_grain import (
    SilverGrainPlan,
    apply_metallic_silver_grain,
)
from half_frame_darkroom.core.provenance import (
    apply_scanner_raw_border_watermark,
    payload_with_config,
    provenance_npz_array,
    provenance_payload,
)
from half_frame_darkroom.core.scanner import (
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
from half_frame_darkroom.core.sensitometry import exposure_to_density
from half_frame_darkroom.core.sidecar import (
    developed_negative_sidecar,
    final_positive_sidecar,
    layer_pack_metadata,
    scanner_raw_sidecar,
    transmission_raw_source_kind,
)
from half_frame_darkroom.core.states import (
    DevelopedNegative,
    ScannedPositive,
    ScanOutput,
    developed_medium_metadata,
)
from half_frame_darkroom.core.subtractive import density_to_positive_rgb
from half_frame_darkroom.model.config import DarkroomConfig


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


def _validate_numeric_config_group(group: str, values: dict[str, object]) -> None:
    for key, value in values.items():
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


def _clip01(value: float) -> float:
    return float(np.clip(float(value), 0.0, 1.0))


def _uses_reduced_bw_negative(config: DarkroomConfig) -> bool:
    return _resolved_process_program_key(config) == "bw_negative"


def _uses_reduced_color_negative(config: DarkroomConfig) -> bool:
    key = _resolved_process_program_key(config)
    return not is_monochrome_material(config) and key in {
        "color_negative",
        "color_negative_bleach_bypass",
    }


def _uses_reduced_bw_reversal(config: DarkroomConfig) -> bool:
    key = _resolved_process_program_key(config)
    return key == "bw_reversal"


def _uses_reduced_color_reversal(config: DarkroomConfig) -> bool:
    key = _resolved_process_program_key(config)
    return not is_monochrome_material(config) and key == "color_reversal"


def _resolved_process_program_key(config: DarkroomConfig) -> str:
    return resolve_program_key(
        config.chemistry,
        mode=config.mode,
        material_process=config_medium_process(config),
    )


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
    stain_delta = float(chem.chemical_stain - original_stain)
    uneven_delta = float(chem.uneven_development - original_uneven)

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
        "chemical_stain_relative_delta": stain_relative_delta,
        "uneven_development_relative_delta": uneven_relative_delta,
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
    if quality in {"high", "full", "native"}:
        return None
    if quality in {"draft", "preview"}:
        default_edge = 1200
    else:
        default_edge = 1800

    configured = getattr(config.processing, f"{module_name}_work_long_edge", None)
    edge = default_edge if configured is None else int(configured)
    if edge <= 0:
        return None
    if uses_reduced_implementation(config):
        edge = min(edge, 1600)
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
            getattr(config.film, "silver_grain_clump_mix", 0.22),
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
            representation = "reduced_color_coupler_pool_v1"
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
            representation = "reduced_color_reversal_pool_v1"
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


def _develop_silver_halide_pipeline(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
    _prepared_config: bool = False,
) -> DevelopedNegative:
    """把输入 sRGB 图像冲洗成 CMY 底片密度状态。"""
    config = _runtime_config(config, prepared=_prepared_config)
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
    linear_input = None if discard_history else np.clip(linear, 0.0, 1.0)

    if config.enable_mtf:
        # ``linear`` is a private formation buffer and has no consumer after
        # this call; the public MTF helper remains non-mutating by default.
        after_mtf = apply_emulsion_mtf(
            linear,
            config.film,
            consume_input=True,
        )
    else:
        after_mtf = linear
    del linear

    # A leak is additional exposure reaching the material, not a density frame
    # laid over the developed result. Put it before emulsion/base scattering so
    # it shares the same latent-state path while its directional generator keeps
    # it from becoming an artificial four-edge halo.
    after_light_leak, light_leak_map = apply_light_leak_to_exposure(
        after_mtf,
        config.chemistry,
        rng=rng,
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
                fast=uses_reduced_implementation(config),
                work_long_edge=_processing_work_long_edge(config, "halation"),
                spread_scale_weights=config.film.halation_spread_scale_weights,
            )
            # filter2D may leave ~1e-17 negative round-off around an otherwise
            # non-negative optical return. Layer exposure cannot be negative,
            # so clamp only the material-layer branch; compatibility RGB keeps
            # its historical pixels and hash.
            np.maximum(spread, 0.0, out=spread)
            layer_weights = (
                (float(np.mean(configured_layer_weights)),)
                if is_monochrome_material(config)
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
            fast=uses_reduced_implementation(config),
            work_long_edge=_processing_work_long_edge(config, "halation"),
        )
    else:
        after_halation = after_light_leak
    light_piping_layer_count = (
        1
        if is_monochrome_material(config)
        and (_uses_reduced_bw_negative(config) or _uses_reduced_bw_reversal(config))
        else 3
    )
    layer_light_piping_addition = light_piping_exposure_field(
        after_halation.shape,
        config.film,
        layer_count=light_piping_layer_count,
    )
    light_piping_applied = layer_light_piping_addition is not None
    light_piping_edge_weights = (
        None
        if layer_light_piping_addition is None
        else tuple(float(value) for value in layer_light_piping_addition.edge_weights)
    )
    latent_layer_exposure_addition = combine_layer_exposure_addition_fields(
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
        fast=uses_reduced_implementation(config),
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
    if adjacency_rate_field is not None:
        development_rate_field: object | None = StepDevelopmentRateField(
            full_shape=after_halation.shape[:2],
            first_step_label=str(adjacency_audit["first_development_step"]),
            common_rate=uneven_development_map,
            first_step_rate=adjacency_rate_field,
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
    del development_rate_field, adjacency_rate_field

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
        is_positive_program = str(developed_medium_contract(config)["image_polarity"]) == "positive"
        _attach_reduced_process_contract(
            reduced_process_payload,
            reduced_color.process_result,
            "reduced_color_reversal_pool_v1" if is_positive_program else "reduced_color_coupler_pool_v1",
            reduced_color.effective_development,
            reduced_color.compatibility,
            process_totals=reduced_color.process_totals,
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
    # Keep only its metadata identity before stain/plating allocate their own
    # full-size maps; retaining all three fields together wastes one scalar
    # frame without adding a diagnostic export.
    del uneven_development_map

    accident_components = apply_density_accident_components(
        density_cmy,
        config.chemistry,
        rng=rng,
        film=config.film,
        fast=uses_reduced_implementation(config),
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
            fast=uses_reduced_implementation(config),
            work_long_edge=_processing_work_long_edge(config, "grain"),
            image_polarity=str(developed_medium_contract(config)["image_polarity"]),
            component_scope="emulsion",
            consume_input_as_effective_delta=discard_formation_layer_masters,
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
            )
            if accident_density_deferred
            else processed_density_cmy
        )
        optical_layer_density = formed_density_cmy
    if is_monochrome_material(config) and not discard_formation_layer_masters:
        density_grain = np.repeat(density_grain.mean(axis=-1, keepdims=True), 3, axis=-1)

    if formed_optical_density_rgb is not None:
        # Grain remains a layer/emulsion phenomenon and therefore passes
        # through the dye absorption matrix. Chemical stain and deposited
        # surface silver are separate post-process components: the former is
        # converted from its own layer-colour definition, while the latter is
        # added as neutral broadband RGB density. Do not send the combined
        # compatibility-master delta through one matrix, or surface silver
        # would silently become coloured dye.
        absorption = np.asarray(config.film.dye_absorption_matrix, dtype=np.float32).reshape(3, 3)
        height, width = optical_layer_density.shape[:2]
        delta_rows = max(1, min(height, int(2_000_000 / max(width, 1))))
        stain_map = accident_maps.get("chemical_stain")
        plating_map = accident_maps.get("silver_plating")
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
            np.maximum(formed_optical_density_rgb[start:stop], 0.0, out=formed_optical_density_rgb[start:stop])
            if consume_layer_density_for_deferred_accidents:
                # Reuse the private formed+grain layer buffer as the final
                # compatibility master.  Keep the old eager arithmetic order:
                # formed -> stain -> plating proxy -> pure grain delta.
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
    if has_silver_plating_map:
        final_medium_contract["surface_deposits"] = {
            "metallic_silver": {
                "representation": "neutral_broadband_surface_density_v1",
                "control_strength": float(config.chemistry.silver_plating),
                "source": "rapid_or_monobath_processing_accident",
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
            "chemical_stain": float(config.chemistry.chemical_stain),
            "silver_plating": float(config.chemistry.silver_plating),
            "uneven_development": float(config.chemistry.uneven_development),
            "development_adjacency_strength": float(
                config.chemistry.development_adjacency_strength
            ),
            "development_adjacency": adjacency_audit,
            "has_light_leak_map": has_light_leak_map,
            "accident_maps": accident_map_names,
            "accident_stages": {
                "light_leak": "pre_latent_exposure",
                "uneven_development": "development_formation",
                "chemical_stain": "post_process_component_density",
                "silver_plating": "post_process_surface_silver_density",
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


def scan_negative(
    negative: DevelopedNegative,
    config: DarkroomConfig | None = None,
    *,
    _prepared_config: bool = False,
) -> ScannedPositive:
    """Dispatch scan/render through the registered media pipeline."""
    auto_interpreter = config is None
    resolved_config, prepared = _resolve_scan_interpretation_config(negative, config, _prepared_config)
    runtime = _runtime_config(resolved_config, prepared=prepared)
    if auto_interpreter:
        _align_interpreter_to_medium(runtime, negative)
    _validate_interpreter_compatibility(runtime, negative)
    pipeline = get_scan_pipeline(negative)
    scanned = pipeline.scan(negative, runtime, True)
    if auto_interpreter:
        scanned.metadata["interpretation_selection"] = "automatic_medium_contract"
        scanned.metadata["requested_interpretation"] = "auto"
        scanned.metadata["resolved_interpretation"] = (
            "positive" if str(scanned.input_polarity).strip().lower() == "positive" else "negative"
        )
    return scanned


def configure_scan_interpretation(config: DarkroomConfig, mode: str) -> str:
    """Configure an observation role without changing any developed medium."""
    normalized = str(mode or "auto").strip().lower().replace("-", "_")
    aliases = {
        "negative_scan": "negative",
        "positive_transparency_scan": "positive",
        "transmission_scan": "direct",
        "direct_transmission": "direct",
        "reversal": "positive",
        "slide": "positive",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"auto", "manual", "negative", "positive", "direct"}:
        raise ValueError(f"Unsupported scan interpretation mode: {mode}")
    config.scanner.interpretation_mode = normalized
    if normalized == "negative":
        config.scanner.remove_base_mask = True
        config.scanner.invert_transmission = True
        config.scanner.interpreter_key = "negative_scan"
        config.scanner.target_medium_process = "negative"
        config.scanner.input_polarity = "negative"
        config.scanner.output_polarity = "positive"
        if str(config.scanner.scan_method).lower() == "positive_transparency":
            config.scanner.scan_method = "negative_inversion"
    elif normalized == "positive":
        config.scanner.remove_base_mask = False
        config.scanner.invert_transmission = False
        config.scanner.interpreter_key = "positive_transparency_scan"
        config.scanner.target_medium_process = "positive"
        config.scanner.input_polarity = "positive"
        config.scanner.output_polarity = "positive"
        config.scanner.scan_method = "positive_transparency"
    elif normalized == "direct":
        config.scanner.remove_base_mask = False
        config.scanner.invert_transmission = False
        config.scanner.interpreter_key = "transmission_scan"
        config.scanner.target_medium_process = "transmissive"
        config.scanner.input_polarity = "uninterpreted"
        config.scanner.output_polarity = "same_as_input"
        config.scanner.scan_method = "direct_transmission"
    elif normalized == "manual":
        config.scanner.interpreter_key = (
            "negative_scan"
            if bool(config.scanner.invert_transmission)
            else "transmission_scan"
        )
        config.scanner.target_medium_process = "transmissive"
        config.scanner.input_polarity = "user_selected"
        config.scanner.output_polarity = (
            "positive" if bool(config.scanner.invert_transmission) else "same_as_input"
        )
        config.scanner.scan_method = (
            "negative_inversion"
            if bool(config.scanner.invert_transmission)
            else "direct_transmission"
        )
    return normalized


def scan_medium_direct(
    medium: DevelopedNegative,
    config: DarkroomConfig,
    mode: str,
) -> ScannedPositive:
    """Interpret a density master exactly as requested, ignoring recorded polarity.

    Material optical coefficients are still restored from the immutable
    observation snapshot. Only the choice of negative inversion versus positive
    transparency viewing ignores the stored result-kind contract.
    """
    runtime = _runtime_config(config)
    selected = configure_scan_interpretation(runtime, mode)
    if selected == "auto":
        _align_interpreter_to_medium(runtime, medium)
        scanned = scan_negative(medium, runtime)
        scanned.metadata["interpretation_selection"] = "automatic_medium_contract"
        scanned.metadata["requested_interpretation"] = "auto"
        scanned.metadata["resolved_interpretation"] = (
            "positive" if str(scanned.input_polarity).strip().lower() == "positive" else "negative"
        )
        return scanned
    if selected == "manual":
        if bool(runtime.scanner.invert_transmission):
            scanned = _scan_negative_pipeline(medium, runtime)
        else:
            scanned = _scan_positive_transparency_pipeline(medium, runtime)
    elif selected == "negative":
        scanned = _scan_negative_pipeline(medium, runtime)
    else:
        scanned = _scan_positive_transparency_pipeline(medium, runtime)
    scanned.metadata["interpretation_selection"] = "manual_direct"
    scanned.metadata["requested_interpretation"] = selected
    scanned.metadata["resolved_interpretation"] = selected
    scanned.metadata["recorded_medium_polarity"] = str(medium.image_polarity)
    return scanned


def _scan_negative_pipeline(
    negative: DevelopedNegative,
    config: DarkroomConfig | None = None,
    _prepared_config: bool = False,
) -> ScannedPositive:
    """把已冲洗底片解释成可观看正像。"""
    _validate_developed_medium_arrays(negative)
    config, prepared = _resolve_scan_interpretation_config(negative, config, _prepared_config)
    auto_bw = _developed_medium_is_monochrome(negative)
    if auto_bw:
        config.mode = "bw_negative"
    config = _runtime_config(config, prepared=prepared)
    if auto_bw:
        _force_bw_negative(config, include_scanner=True)
    _validate_scan_runtime(config)

    known_base = _clear_base_scanner_sample(config, negative).reshape(3)
    # Inspector-only compatibility preview. It intentionally uses the formed
    # layer master because retaining a second full RGB optical master solely
    # for a no-grain diagnostic would be prohibitive at 30--60 MP. It never
    # contributes to scanner_raw, positive_linear, or output_srgb.
    if negative.layer_masters_available:
        positive_no_grain = density_to_positive_rgb(
            negative.density_cmy,
            config.film,
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
            base_samples=known_base.reshape(1, 1, 3),
        )
        positive_no_grain_source = "legacy_formed_layer_diagnostic_only"
    else:
        # The scan-only loader deliberately leaves compatibility layers on
        # disk. The scanner result remains exact because it observes the RGB
        # optical master; only this non-authoritative Inspector preview is
        # unavailable and therefore falls back to the rendered positive.
        positive_no_grain = None
        positive_no_grain_source = "scan_optical_only_render_fallback"
    negative_total_density = _medium_optical_density_rgb(negative, config.film)
    negative_total_density = _optical_density_with_optional_reference_border(
        negative_total_density,
        config,
        negative,
    )
    negative_linear = capture_optical_density(
        negative_total_density,
        config.scanner,
        illuminant_rgb=transmission_illuminant_rgb(config.scanner),
    )
    scanner_raw = negative_linear
    return _scan_scanner_raw_array(
        scanner_raw,
        config,
        known_base_transmittance_rgb=known_base,
        positive_no_grain=(
            None
            if positive_no_grain is None
            else np.clip(positive_no_grain, 0.0, 1.0).astype(
                np.float32,
                copy=False,
            )
        ),
        negative_total_density=negative_total_density,
        metadata={
            "runtime_config": config,
            "stage": "scanned_positive",
            "scan_source": "density_negative",
            "interpreter_key": config.scanner.interpreter_key,
            "medium": config.medium,
            "medium_process": config_medium_process(config),
            "input_polarity": config.scanner.input_polarity,
            "output_polarity": config.scanner.output_polarity,
            "base_balance_source": "generated_clear_base",
            "positive_no_grain_source": positive_no_grain_source,
        },
        _prepared_config=True,
    )


def _scan_positive_transparency_pipeline(
    negative: DevelopedNegative,
    config: DarkroomConfig | None = None,
    _prepared_config: bool = False,
) -> ScannedPositive:
    """Prototype light-table scan for developed positive transparencies."""
    _validate_developed_medium_arrays(negative)
    config, prepared = _resolve_scan_interpretation_config(negative, config, _prepared_config)
    config = _runtime_config(config, prepared=prepared)
    _validate_scan_runtime(config)
    negative_total_density = _medium_optical_density_rgb(negative, config.film)
    negative_total_density = _optical_density_with_optional_reference_border(
        negative_total_density,
        config,
        negative,
    )
    scanner_raw = capture_optical_density(
        negative_total_density,
        config.scanner,
        illuminant_rgb=transmission_illuminant_rgb(config.scanner),
    )
    interpreted_raw = scanner_raw
    if config.scanner.remove_base_mask:
        interpreted_raw = balance_negative_base(
            scanner_raw,
            known_base_transmittance_rgb=_clear_base_scanner_sample(
                config,
                negative,
            ).reshape(3),
        )
    positive_linear = render_positive_transparency_scan(
        interpreted_raw,
        config.scanner,
        print_contrast=config.look.print_contrast,
        print_exposure_ev=config.look.print_exposure_ev,
    )
    if (
        config.scanner.scan_normalize
        and float(config.scanner.scan_normalize_strength) > 0.0
    ):
        positive_linear = normalize_scan_rgb(
            positive_linear,
            black_percentile=config.scanner.scan_black_percentile,
            white_percentile=config.scanner.scan_white_percentile,
            strength=config.scanner.scan_normalize_strength,
            mode=config.scanner.scan_normalize_mode,
        )
    positive_linear = np.clip(positive_linear, 0.0, 1.0).astype(
        np.float32,
        copy=False,
    )
    return ScannedPositive(
        negative_linear=scanner_raw.astype(np.float32),
        negative_base_balanced=interpreted_raw.astype(np.float32),
        positive_raw=interpreted_raw.astype(np.float32),
        negative_channel_reconstructed=interpreted_raw.astype(np.float32),
        scanner_raw=scanner_raw.astype(np.float32),
        negative_total_density=negative_total_density,
        positive_linear=positive_linear,
        output_srgb=linear_to_srgb(positive_linear),
        positive_no_grain=positive_linear,
        interpreter_key=str(config.scanner.interpreter_key),
        input_polarity=str(config.scanner.input_polarity),
        output_polarity=str(config.scanner.output_polarity),
        view_mode="display",
        metadata={
            "runtime_config": config,
            "stage": "scanned_positive",
            "scan_source": "density_positive_transparency",
            "interpreter_key": config.scanner.interpreter_key,
            "medium": config.medium,
            "medium_process": config_medium_process(config),
            "input_polarity": config.scanner.input_polarity,
            "output_polarity": config.scanner.output_polarity,
            "capture_model": "transmission_illuminant_sensor_v2",
            "scanner_saturation_fraction": float(
                np.mean(np.any(scanner_raw >= 1.0 - 1e-6, axis=-1))
            ),
            "scanner_floor_fraction": float(
                np.mean(np.any(scanner_raw <= 1e-6 * 1.01, axis=-1))
            ),
            "positive_interpretation_order": (
                "transmission_capture",
                "positive_tone_mapping",
            ),
            "light_table_ev": float(config.scanner.light_table_ev),
            "light_table_temperature_k": float(config.scanner.light_table_temperature_k),
            "positive_scan_color_control_strength": float(config.scanner.positive_scan_color_control_strength),
            "prototype": "positive_transparency_scan_v1",
        },
    )


def scan_scanner_raw(
    scanner_raw: np.ndarray,
    config: DarkroomConfig | None = None,
    *,
    base_samples: np.ndarray | None = None,
    known_base_transmittance_rgb: np.ndarray | None = None,
    known_base_density_rgb: np.ndarray | None = None,
    source_path: str | Path | None = None,
    _prepared_config: bool = False,
) -> ScannedPositive:
    """把电子负片 scanner raw 直接解释成正像，适合 scan-only 快速重扫。"""
    config = _runtime_config(config, prepared=_prepared_config)
    return _scan_scanner_raw_array(
        scanner_raw,
        config,
        base_samples=base_samples,
        known_base_transmittance_rgb=known_base_transmittance_rgb,
        known_base_density_rgb=known_base_density_rgb,
        metadata={
            "runtime_config": config,
            "stage": "scanned_positive",
            "scan_source": "scanner_raw_tiff",
            "source_path": str(source_path) if source_path is not None else None,
            "interpreter_key": config.scanner.interpreter_key,
            "medium": config.medium,
            "medium_process": config_medium_process(config),
            "input_polarity": config.scanner.input_polarity,
            "output_polarity": config.scanner.output_polarity,
        },
        _prepared_config=True,
    )


def scan_scanner_raw_direct(
    scanner_raw: np.ndarray,
    config: DarkroomConfig,
    mode: str,
    *,
    base_samples: np.ndarray | None = None,
    known_base_transmittance_rgb: np.ndarray | None = None,
    known_base_density_rgb: np.ndarray | None = None,
    source_path: str | Path | None = None,
    raw_source_kind: str | None = None,
) -> ScannedPositive:
    """Directly interpret raw transmission data as negative or positive."""
    runtime = _runtime_config(config)
    requested = configure_scan_interpretation(runtime, mode)
    physical_kind = raw_source_kind
    if physical_kind is None and source_path is not None:
        physical_kind = transmission_raw_source_kind(source_path)
    selected = requested
    if requested == "auto":
        if physical_kind == "light_table_raw_tiff":
            selected = "positive"
        elif physical_kind == "scanner_raw_tiff":
            selected = "negative"
        elif str(runtime.scanner.interpreter_key).lower() == "positive_transparency_scan":
            selected = "positive"
        else:
            selected = "negative"
        configure_scan_interpretation(runtime, selected)
    if physical_kind is None:
        physical_kind = "scanner_raw_tiff"
    _validate_scan_runtime(runtime)
    raw_input = np.asarray(scanner_raw, dtype=np.float32)
    if runtime.scanner.include_clear_base_border:
        border_reference = known_base_transmittance_rgb
        if border_reference is None and base_samples is not None:
            samples = np.asarray(base_samples, dtype=np.float32)
            if (
                samples.size == 0
                or samples.size % 3 != 0
                or not np.all(np.isfinite(samples))
            ):
                raise ValueError("base_samples must contain finite RGB samples")
            border_reference = np.median(
                samples.reshape(-1, 3),
                axis=0,
            ).astype(np.float32, copy=False)
        if border_reference is None and known_base_density_rgb is not None:
            density = np.asarray(known_base_density_rgb, dtype=np.float32)
            if (
                density.size != 3
                or not np.all(np.isfinite(density))
                or np.any(density < 0.0)
            ):
                raise ValueError(
                    "known_base_density_rgb must contain three finite "
                    "nonnegative values"
                )
            border_reference = capture_optical_density(
                density.reshape(1, 1, 3),
                runtime.scanner,
                illuminant_rgb=transmission_illuminant_rgb(runtime.scanner),
            ).reshape(3)
        if border_reference is None:
            border_reference = estimate_negative_base_transmittance(
                raw_input,
                runtime.scanner.scan_base_percentile,
            )
        raw_input = scanner_raw_with_reference_border(
            raw_input,
            border_reference,
            border_percent=runtime.output.scanner_raw_border_percent,
            border_min_px=runtime.output.scanner_raw_border_min_px,
        )
    invert_selected = (
        selected == "negative"
        or (selected == "manual" and bool(runtime.scanner.invert_transmission))
    )
    if invert_selected:
        scanned = scan_scanner_raw(
            raw_input,
            runtime,
            base_samples=base_samples,
            known_base_transmittance_rgb=known_base_transmittance_rgb,
            known_base_density_rgb=known_base_density_rgb,
            source_path=source_path,
            _prepared_config=True,
        )
    else:
        raw = np.clip(raw_input, 1e-6, 1.0)
        interpreted_raw = raw
        if bool(runtime.scanner.remove_base_mask):
            if (
                known_base_transmittance_rgb is not None
                and known_base_density_rgb is not None
            ):
                raise ValueError(
                    "provide only one of known_base_transmittance_rgb and "
                    "known_base_density_rgb"
                )
            known_base = known_base_transmittance_rgb
            if known_base_density_rgb is not None:
                density = np.asarray(known_base_density_rgb, dtype=np.float32)
                if (
                    density.size != 3
                    or not np.all(np.isfinite(density))
                    or np.any(density < 0.0)
                ):
                    raise ValueError(
                        "known_base_density_rgb must contain three finite "
                        "nonnegative values"
                    )
                known_base = capture_optical_density(
                    density.reshape(1, 1, 3),
                    runtime.scanner,
                    illuminant_rgb=transmission_illuminant_rgb(runtime.scanner),
                ).reshape(3)
            interpreted_raw = balance_negative_base(
                raw,
                base_percentile=runtime.scanner.scan_base_percentile,
                base_samples=base_samples,
                known_base_transmittance_rgb=known_base,
            )
        positive_linear = render_positive_transparency_scan(
            interpreted_raw,
            runtime.scanner,
            print_contrast=runtime.look.print_contrast,
            print_exposure_ev=runtime.look.print_exposure_ev,
        )
        if runtime.scanner.scan_normalize:
            positive_linear = normalize_scan_rgb(
                positive_linear,
                black_percentile=runtime.scanner.scan_black_percentile,
                white_percentile=runtime.scanner.scan_white_percentile,
                strength=runtime.scanner.scan_normalize_strength,
                mode=runtime.scanner.scan_normalize_mode,
            )
        positive_linear = np.clip(positive_linear, 0.0, 1.0).astype(
            np.float32,
            copy=False,
        )
        scanned = ScannedPositive(
            negative_linear=raw,
            negative_base_balanced=interpreted_raw,
            positive_raw=interpreted_raw,
            negative_channel_reconstructed=interpreted_raw,
            scanner_raw=raw,
            negative_total_density=(-np.log10(raw)).astype(np.float32, copy=False),
            positive_linear=positive_linear,
            output_srgb=linear_to_srgb(positive_linear),
            positive_no_grain=positive_linear,
            interpreter_key=str(runtime.scanner.interpreter_key),
            input_polarity=str(runtime.scanner.input_polarity),
            output_polarity=str(runtime.scanner.output_polarity),
            view_mode="display",
            metadata={
                "runtime_config": runtime,
                "stage": "scanned_positive",
                "scan_source": "scanner_raw_tiff",
                "source_path": str(source_path) if source_path is not None else None,
                "interpreter_key": runtime.scanner.interpreter_key,
                "scanner_saturation_fraction": float(
                    np.mean(np.any(raw >= 1.0 - 1e-6, axis=-1))
                ),
                "scanner_floor_fraction": float(
                    np.mean(np.any(raw <= 1e-6 * 1.01, axis=-1))
                ),
            },
        )
    scanned.metadata["interpretation_selection"] = "manual_direct"
    scanned.metadata["requested_interpretation"] = requested
    scanned.metadata["resolved_interpretation"] = selected
    scanned.metadata["scan_source"] = physical_kind
    return scanned


def _scan_scanner_raw_array(
    scanner_raw: np.ndarray,
    config: DarkroomConfig,
    *,
    base_samples: np.ndarray | None = None,
    known_base_transmittance_rgb: np.ndarray | None = None,
    known_base_density_rgb: np.ndarray | None = None,
    positive_no_grain: np.ndarray | None = None,
    negative_total_density: np.ndarray | None = None,
    metadata: dict | None = None,
    _prepared_config: bool = False,
) -> ScannedPositive:
    """共享的 scanner raw -> positive 路径。"""
    config = _runtime_config(config, prepared=_prepared_config)
    _validate_scan_runtime(config)
    scanner_raw = _validate_rgb_image_array(scanner_raw, "scanner raw")
    metadata = dict(metadata or {"runtime_config": config, "stage": "scanned_positive"})
    if known_base_transmittance_rgb is not None and known_base_density_rgb is not None:
        raise ValueError(
            "provide only one of known_base_transmittance_rgb and known_base_density_rgb"
        )
    known_base = known_base_transmittance_rgb
    known_source = "known_base_transmittance"
    if known_base_density_rgb is not None:
        density = np.asarray(known_base_density_rgb, dtype=np.float32)
        if density.size != 3 or not np.all(np.isfinite(density)) or np.any(density < 0.0):
            raise ValueError("known_base_density_rgb must contain three finite nonnegative values")
        known_base = capture_optical_density(
            density.reshape(1, 1, 3),
            config.scanner,
            illuminant_rgb=transmission_illuminant_rgb(config.scanner),
        ).reshape(3)
        known_source = "known_base_density"
    if known_base is not None:
        known_base = np.asarray(known_base, dtype=np.float32)
        if known_base.size != 3 or not np.all(np.isfinite(known_base)):
            raise ValueError("known_base_transmittance_rgb must contain three finite values")
        known_base = np.clip(known_base.reshape(3), 1e-6, 1.0)
    base_removal_enabled = bool(config.scanner.remove_base_mask)
    metadata.setdefault(
        "base_balance_source",
        (
            known_source
            if known_base is not None
            else (
                "explicit_clear_base_samples"
                if base_samples is not None
                else "image_percentile_fallback"
            )
        )
        if base_removal_enabled
        else "preserved_by_user",
    )
    metadata.setdefault("capture_model", "transmission_illuminant_sensor_v2")
    metadata.setdefault("negative_interpretation_order", (
        "base_mask_removal",
        "log_density_inversion",
        "channel_reconstruction",
        "tone_mapping",
    ))
    metadata.setdefault("negative_backlight_ev", float(config.scanner.negative_backlight_ev))
    metadata.setdefault(
        "negative_backlight_temperature_k",
        float(config.scanner.negative_backlight_temperature_k),
    )
    scanner_raw = np.clip(np.asarray(scanner_raw, dtype=np.float32), 1e-6, 1.0)
    metadata["scanner_saturation_fraction"] = float(
        np.mean(np.any(scanner_raw >= 1.0 - 1e-6, axis=-1))
    )
    metadata["scanner_floor_fraction"] = float(
        np.mean(np.any(scanner_raw <= 1e-6 * 1.01, axis=-1))
    )
    estimated_base = None
    if base_removal_enabled and known_base is None and base_samples is None:
        estimated_base = estimate_negative_base_transmittance(
            scanner_raw,
            config.scanner.scan_base_percentile,
        )
    if base_removal_enabled and known_base is not None:
        base_median = known_base
        metadata["base_anchor_rgb"] = [float(value) for value in base_median]
        metadata["base_saturated_channels"] = [
            bool(value >= 1.0 - 1e-6) for value in base_median
        ]
    elif base_removal_enabled and base_samples is not None:
        base = np.clip(np.asarray(base_samples, dtype=np.float32), 1e-6, 1.0).reshape(-1, 3)
        base_median = np.median(base, axis=0)
        metadata["base_anchor_rgb"] = [float(value) for value in base_median]
        metadata["base_saturated_channels"] = [
            bool(value >= 1.0 - 1e-6) for value in base_median
        ]
    elif base_removal_enabled:
        base_median = estimated_base
        metadata["base_anchor_rgb"] = [float(value) for value in base_median]
        metadata["base_saturated_channels"] = [
            bool(value >= 1.0 - 1e-6) for value in base_median
        ]
        metadata["base_estimation_percentile"] = float(
            config.scanner.scan_base_percentile
        )
    if _density_is_monochrome(scanner_raw, tolerance=1e-4):
        config.mode = "bw_negative"
        _force_bw_negative(config, include_scanner=True)
    metadata["negative_channel_compensation_enabled"] = bool(
        config.scanner.negative_channel_compensation_enabled
    )
    metadata["negative_channel_compensation_strength"] = float(
        config.scanner.negative_channel_compensation_strength
    )
    if config.scanner.negative_channel_compensation_enabled:
        compensation_matrix = negative_scanner_compensation_matrix(
            strength=config.scanner.negative_channel_compensation_strength,
        )
        metadata["negative_channel_compensation_matrix"] = [
            [float(value) for value in row] for row in compensation_matrix
        ]
        # Retain the old key for sidecar/API compatibility. Its value is now
        # explicitly scanner-side and independent of material composition.
        metadata["negative_material_compensation_matrix"] = metadata[
            "negative_channel_compensation_matrix"
        ]
        metadata["negative_channel_compensation_scope"] = "scanner_rgb_density"
    negative_linear = scanner_raw
    if negative_total_density is None:
        negative_total_density = (-np.log10(scanner_raw)).astype(
            np.float32,
            copy=False,
        )

    if base_removal_enabled:
        negative_base_balanced = balance_negative_base(
            negative_linear,
            base_percentile=config.scanner.scan_base_percentile,
            base_samples=base_samples,
            known_base_transmittance_rgb=(
                known_base if known_base is not None else estimated_base
            ),
        )
    else:
        negative_base_balanced = negative_linear
    positive_raw = invert_negative_image(negative_base_balanced)
    negative_channel_reconstructed = reconstruct_negative_channels(
        positive_raw,
        config.scanner,
    )

    if config.enable_subtractive:
        if str(config.scanner.scan_method).lower() == "legacy_density_mapping":
            positive_linear = scanner_raw_to_positive_rgb(
                scanner_raw,
                config.scanner,
                print_contrast=config.look.print_contrast,
                print_exposure_ev=config.look.print_exposure_ev,
                base_percentile=config.scanner.scan_base_percentile,
                base_samples=base_samples,
                known_base_transmittance_rgb=(
                    known_base if known_base is not None else estimated_base
                ),
            )
        else:
            positive_linear = render_positive_scan(
                negative_channel_reconstructed,
                config.scanner,
                print_contrast=config.look.print_contrast,
                print_exposure_ev=config.look.print_exposure_ev,
            )
    else:
        positive_linear = np.clip(
            negative_channel_reconstructed / max(float(np.max(negative_channel_reconstructed)), 1e-6),
            0.0,
            1.0,
        )

    if (
        config.scanner.scan_normalize
        and float(config.scanner.scan_normalize_strength) > 0.0
    ):
        positive_linear = normalize_scan_rgb(
            positive_linear,
            black_percentile=config.scanner.scan_black_percentile,
            white_percentile=config.scanner.scan_white_percentile,
            strength=config.scanner.scan_normalize_strength,
            mode=config.scanner.scan_normalize_mode,
        )

    positive_linear = np.clip(positive_linear, 0.0, 1.0).astype(
        np.float32,
        copy=False,
    )
    output_srgb = linear_to_srgb(positive_linear)
    if positive_no_grain is None:
        positive_no_grain = positive_linear
    return ScannedPositive(
        negative_linear=negative_linear.astype(np.float32),
        negative_base_balanced=negative_base_balanced,
        positive_raw=positive_raw,
        negative_channel_reconstructed=negative_channel_reconstructed,
        scanner_raw=scanner_raw.astype(np.float32),
        negative_total_density=np.asarray(negative_total_density, dtype=np.float32),
        positive_linear=positive_linear,
        output_srgb=output_srgb,
        positive_no_grain=np.clip(positive_no_grain, 0.0, 1.0).astype(
            np.float32,
            copy=False,
        ),
        interpreter_key=str(config.scanner.interpreter_key),
        input_polarity=str(config.scanner.input_polarity),
        output_polarity=str(config.scanner.output_polarity),
        metadata=metadata,
    )


def _scan_output_tile_rows(
    config: DarkroomConfig,
    image: np.ndarray,
) -> int | None:
    configured_rows = int(config.processing.scan_tile_rows)
    if configured_rows <= 0:
        return None
    height, width = image.shape[:2]
    megapixels = height * width / 1_000_000.0
    if megapixels < float(config.processing.scan_tile_threshold_megapixels):
        return None
    rows_for_eight_megapixels = max(1, int(8_000_000 / max(width, 1)))
    return max(1, min(configured_rows, rows_for_eight_megapixels, height))


def _normalize_encode_output_tiled(
    positive_linear: np.ndarray,
    config: DarkroomConfig,
    tile_rows: int,
) -> np.ndarray:
    """Measure once globally, then apply the frozen calibration to every tile.

    Tiling is only a memory/execution strategy.  It must never turn base,
    black/white, normalization, exposure, or white-balance estimation into a
    per-tile operation.  Future automatic estimators belong before this apply
    pass and must likewise produce one immutable full-image calibration.
    """
    height = positive_linear.shape[0]
    if (
        config.scanner.scan_normalize
        and float(config.scanner.scan_normalize_strength) > 0.0
    ):
        global_black, global_white = scan_normalization_range(
            positive_linear,
            black_percentile=config.scanner.scan_black_percentile,
            white_percentile=config.scanner.scan_white_percentile,
            mode=config.scanner.scan_normalize_mode,
        )
        for start in range(0, height, tile_rows):
            stop = min(start + tile_rows, height)
            apply_scan_normalization_range(
                positive_linear[start:stop],
                global_black,
                global_white,
                strength=config.scanner.scan_normalize_strength,
                mode=config.scanner.scan_normalize_mode,
                consume_input=True,
            )
    else:
        np.clip(positive_linear, 0.0, 1.0, out=positive_linear)

    for start in range(0, height, tile_rows):
        stop = min(start + tile_rows, height)
        linear_to_srgb(
            positive_linear[start:stop],
            consume_input=True,
        )
    return positive_linear


def _scan_negative_output_tiled(
    medium: DevelopedNegative,
    config: DarkroomConfig,
    tile_rows: int,
) -> np.ndarray:
    """Observe row tiles using one oracle base anchor fixed for the full frame."""
    height, width = medium.frame_shape[:2]
    known_base = _clear_base_scanner_sample(config, medium).reshape(3)
    illuminant = transmission_illuminant_rgb(config.scanner)
    border = (
        scanner_raw_export_border_width(
            medium.frame_shape,
            config.output.scanner_raw_border_percent,
            config.output.scanner_raw_border_min_px,
        )
        if config.scanner.include_clear_base_border
        else 0
    )
    positive_linear = np.empty(
        (height + border * 2, width + border * 2, 3),
        dtype=np.float32,
    )
    inner_output = positive_linear[
        border : border + height,
        border : border + width,
    ]
    if border > 0:
        clear_raw = known_base.reshape(1, 1, 3).copy()
        if config.scanner.remove_base_mask:
            clear_raw = balance_negative_base(
                clear_raw,
                known_base_transmittance_rgb=known_base,
                consume_input=True,
            )
        clear_density = invert_negative_image(clear_raw, consume_input=True)
        clear_density = reconstruct_negative_channels(
            clear_density,
            config.scanner,
            consume_input=True,
        )
        clear_render = render_positive_scan(
            clear_density,
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
            consume_input=True,
        )
        positive_linear[...] = clear_render.reshape(1, 1, 3)

    for start in range(0, height, tile_rows):
        stop = min(start + tile_rows, height)
        tile_output = inner_output[start:stop]
        total_density = _medium_optical_density_rgb(
            medium,
            config.film,
            slice(start, stop),
        )
        scanner_raw = capture_optical_density(
            total_density,
            config.scanner,
            illuminant_rgb=illuminant,
            _out=tile_output,
        )
        del total_density
        if config.scanner.remove_base_mask:
            base_balanced = balance_negative_base(
                scanner_raw,
                known_base_transmittance_rgb=known_base,
                consume_input=True,
            )
            del scanner_raw
        else:
            base_balanced = scanner_raw
        positive_raw = invert_negative_image(base_balanced, consume_input=True)
        del base_balanced
        reconstructed = reconstruct_negative_channels(
            positive_raw,
            config.scanner,
            consume_input=True,
        )
        del positive_raw
        rendered = render_positive_scan(
            reconstructed,
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
            consume_input=True,
        )
        del reconstructed
        if not np.shares_memory(rendered, tile_output):
            tile_output[...] = rendered
        del rendered, tile_output
    return _normalize_encode_output_tiled(positive_linear, config, tile_rows)


def _scan_positive_output_tiled(
    medium: DevelopedNegative,
    config: DarkroomConfig,
    tile_rows: int,
) -> np.ndarray:
    """Observe a positive transparency in exact bounded tiles."""
    height, width = medium.frame_shape[:2]
    illuminant = transmission_illuminant_rgb(config.scanner)
    clear_base_raw = _clear_base_scanner_sample(config, medium).reshape(3)
    known_base = (
        clear_base_raw
        if config.scanner.remove_base_mask
        else None
    )
    border = (
        scanner_raw_export_border_width(
            medium.frame_shape,
            config.output.scanner_raw_border_percent,
            config.output.scanner_raw_border_min_px,
        )
        if config.scanner.include_clear_base_border
        else 0
    )
    positive_linear = np.empty(
        (height + border * 2, width + border * 2, 3),
        dtype=np.float32,
    )
    inner_output = positive_linear[
        border : border + height,
        border : border + width,
    ]
    if border > 0:
        border_raw = clear_base_raw.reshape(1, 1, 3).copy()
        if config.scanner.remove_base_mask:
            border_raw = balance_negative_base(
                border_raw,
                known_base_transmittance_rgb=known_base,
                consume_input=True,
            )
        border_render = render_positive_transparency_scan(
            border_raw,
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
            consume_input=True,
        )
        positive_linear[...] = border_render.reshape(1, 1, 3)
    for start in range(0, height, tile_rows):
        stop = min(start + tile_rows, height)
        tile_output = inner_output[start:stop]
        total_density = _medium_optical_density_rgb(
            medium,
            config.film,
            slice(start, stop),
        )
        scanner_raw = capture_optical_density(
            total_density,
            config.scanner,
            illuminant_rgb=illuminant,
            _out=None if reference_execution_enabled() else tile_output,
        )
        del total_density
        if config.scanner.remove_base_mask:
            scanner_raw = balance_negative_base(
                scanner_raw,
                known_base_transmittance_rgb=known_base,
                consume_input=True,
            )
        rendered = render_positive_transparency_scan(
            scanner_raw,
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
            consume_input=True,
        )
        if not np.shares_memory(rendered, tile_output):
            tile_output[...] = rendered
        del rendered, scanner_raw, tile_output
    return _normalize_encode_output_tiled(positive_linear, config, tile_rows)


def _scan_negative_output_only(
    medium: DevelopedNegative,
    config: DarkroomConfig,
) -> np.ndarray:
    """Render a negative without retaining Inspector-only scan stages."""
    _validate_developed_medium_arrays(medium)
    auto_bw = _developed_medium_is_monochrome(medium)
    if auto_bw:
        config.mode = "bw_negative"
        _force_bw_negative(config, include_scanner=True)
    _validate_scan_runtime(config)

    tile_rows = _scan_output_tile_rows(
        config,
        medium.optical_density_rgb
        if medium.optical_density_rgb is not None
        else medium.density_grain,
    )
    if tile_rows is not None and config.enable_subtractive:
        return _scan_negative_output_tiled(medium, config, tile_rows)

    known_base = _clear_base_scanner_sample(config, medium).reshape(3)
    total_density = _medium_optical_density_rgb(medium, config.film)
    total_density = _optical_density_with_optional_reference_border(
        total_density,
        config,
        medium,
    )
    scanner_raw = capture_optical_density(
        total_density,
        config.scanner,
        illuminant_rgb=transmission_illuminant_rgb(config.scanner),
    )
    del total_density

    legacy_mapping = (
        config.enable_subtractive
        and str(config.scanner.scan_method).lower() == "legacy_density_mapping"
    )
    if legacy_mapping:
        positive_linear = scanner_raw_to_positive_rgb(
            scanner_raw,
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
            base_percentile=config.scanner.scan_base_percentile,
            known_base_transmittance_rgb=known_base,
        )
        del scanner_raw
    else:
        if config.scanner.remove_base_mask:
            base_balanced = balance_negative_base(
                scanner_raw,
                base_percentile=config.scanner.scan_base_percentile,
                known_base_transmittance_rgb=known_base,
                consume_input=True,
            )
            del scanner_raw
        else:
            base_balanced = scanner_raw
        positive_raw = invert_negative_image(base_balanced, consume_input=True)
        del base_balanced
        reconstructed = reconstruct_negative_channels(
            positive_raw,
            config.scanner,
            # ``positive_raw`` is a private output-only buffer with no later
            # observer. Transfer it through identity channel reconstruction
            # in production; reference topology retains the former copy.
            consume_input=not reference_execution_enabled(),
        )
        del positive_raw
        if config.enable_subtractive:
            positive_linear = render_positive_scan(
                reconstructed,
                config.scanner,
                print_contrast=config.look.print_contrast,
                print_exposure_ev=config.look.print_exposure_ev,
                consume_input=True,
            )
        else:
            positive_linear = np.clip(
                reconstructed / max(float(np.max(reconstructed)), 1e-6),
                0.0,
                1.0,
            )
        del reconstructed

    if config.scanner.scan_normalize:
        positive_linear = normalize_scan_rgb(
            positive_linear,
            black_percentile=config.scanner.scan_black_percentile,
            white_percentile=config.scanner.scan_white_percentile,
            strength=config.scanner.scan_normalize_strength,
            mode=config.scanner.scan_normalize_mode,
            consume_input=True,
        )
    return linear_to_srgb(positive_linear, consume_input=True)


def _scan_positive_output_only(
    medium: DevelopedNegative,
    config: DarkroomConfig,
) -> np.ndarray:
    """Render a positive transparency without retaining duplicate raw stages."""
    _validate_developed_medium_arrays(medium)
    _validate_scan_runtime(config)
    tile_rows = _scan_output_tile_rows(
        config,
        medium.optical_density_rgb
        if medium.optical_density_rgb is not None
        else medium.density_grain,
    )
    if tile_rows is not None:
        return _scan_positive_output_tiled(medium, config, tile_rows)
    total_density = _medium_optical_density_rgb(medium, config.film)
    total_density = _optical_density_with_optional_reference_border(
        total_density,
        config,
        medium,
    )
    scanner_raw = capture_optical_density(
        total_density,
        config.scanner,
        illuminant_rgb=transmission_illuminant_rgb(config.scanner),
    )
    del total_density
    if config.scanner.remove_base_mask:
        scanner_raw = balance_negative_base(
            scanner_raw,
            known_base_transmittance_rgb=_clear_base_scanner_sample(
                config,
                medium,
            ).reshape(3),
            consume_input=True,
        )
    positive_linear = render_positive_transparency_scan(
        scanner_raw,
        config.scanner,
        print_contrast=config.look.print_contrast,
        print_exposure_ev=config.look.print_exposure_ev,
        consume_input=True,
    )
    del scanner_raw
    if config.scanner.scan_normalize:
        positive_linear = normalize_scan_rgb(
            positive_linear,
            black_percentile=config.scanner.scan_black_percentile,
            white_percentile=config.scanner.scan_white_percentile,
            strength=config.scanner.scan_normalize_strength,
            mode=config.scanner.scan_normalize_mode,
            # The non-tiled output-only positive scanner owns this render
            # buffer and has no Inspector consumer for its pre-normalized
            # values. Transfer it through the established normalization math;
            # the developer reference topology retains the former full RGB
            # result allocation for exact same-version A/B.
            consume_input=not reference_execution_enabled(),
        )
    return linear_to_srgb(positive_linear, consume_input=True)


def _scan_medium_output_only(
    medium: DevelopedNegative,
    config: DarkroomConfig,
    interpretation_mode: str,
) -> tuple[np.ndarray, DarkroomConfig]:
    """Resolve observation exactly like the full scanner but return only pixels."""
    normalized = str(interpretation_mode or "auto").strip().lower()
    if normalized in {"", "auto"}:
        resolved, prepared = _resolve_scan_interpretation_config(medium, None, False)
        runtime = _runtime_config(resolved, prepared=prepared)
        _align_interpreter_to_medium(runtime, medium)
        _validate_interpreter_compatibility(runtime, medium)
    else:
        runtime = _runtime_config(config)
        configure_scan_interpretation(runtime, normalized)

    if bool(runtime.scanner.invert_transmission):
        return _scan_negative_output_only(medium, runtime), runtime
    return _scan_positive_output_only(medium, runtime), runtime


def scan_medium_output(
    medium: DevelopedNegative,
    config: DarkroomConfig,
    mode: str = "auto",
) -> ScanOutput:
    """Observe a developed medium without retaining Inspector-only arrays.

    This is the exact production scanner used by the full workflow, exposed for
    standalone NPZ rescans.  ``scan_medium_direct`` remains available when a
    caller explicitly needs scanner raw, base-balanced, inversion, and channel
    reconstruction stages.
    """
    output_srgb, runtime = _scan_medium_output_only(medium, config, mode)
    requested = str(mode or "auto").strip().lower()
    if requested in {"", "auto"}:
        resolved = (
            "positive"
            if str(runtime.scanner.input_polarity).strip().lower() == "positive"
            else "negative"
        )
    else:
        resolved = (
            "negative"
            if bool(runtime.scanner.invert_transmission)
            else "direct"
        )
    return ScanOutput(
        output_srgb=output_srgb,
        interpreter_key=str(runtime.scanner.interpreter_key),
        input_polarity=str(runtime.scanner.input_polarity),
        output_polarity=str(runtime.scanner.output_polarity),
        view_mode="display",
        metadata={
            "runtime_config": runtime,
            "stage": "scanned_positive",
            "scan_source": "developed_optical_medium",
            "execution_profile": "output_only_exact_v1",
            "interpretation_selection": (
                "automatic_medium_contract"
                if requested in {"", "auto"}
                else "manual_direct"
            ),
            "requested_interpretation": requested or "auto",
            "resolved_interpretation": resolved,
            "recorded_medium_polarity": str(medium.image_polarity),
            "remove_base_mask": bool(runtime.scanner.remove_base_mask),
            "invert_transmission": bool(runtime.scanner.invert_transmission),
            "clear_base_border": bool(
                runtime.scanner.include_clear_base_border
            ),
        },
    )


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
    """读取、处理并保存一张图像文件。preview=True 时才使用 preview_long_edge。"""
    config = config or DarkroomConfig()
    execution_mode = resolve_execution_mode(config, scaled_override=preview)
    # Persist the effective, mutually exclusive mode in runtime metadata while
    # leaving the caller's reusable configuration untouched.
    if (
        str(config.processing.execution_mode).strip().lower() != execution_mode
        or bool(config.fast_mode) != (execution_mode == "reduced_fast")
    ):
        config = copy.deepcopy(config)
        config.processing.execution_mode = execution_mode
        config.fast_mode = execution_mode == "reduced_fast"
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
            execution_mode == "scaled_fast"
            and input_path.suffix.lower() in {".jpg", ".jpeg"}
        ),
    )
    warn_outside_comfort_zone(memory_estimate)
    enforce_memory_budget(
        memory_estimate,
        config.processing.memory_budget_mb,
        config.processing.memory_budget_policy,
    )
    if execution_mode == "scaled_fast":
        image = load_image(input_path, decode_long_edge=long_edge)
    else:
        image = load_image(input_path)
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
                execution_mode == "scaled_fast",
            )
    return output_path


install_default_media_pipelines(
    negative_develop=_develop_silver_halide_pipeline,
    negative_scan=_scan_negative_pipeline,
    positive_transparency_develop=_develop_silver_halide_pipeline,
    positive_transparency_scan=_scan_positive_transparency_pipeline,
)
