"""Single-image Film Foundry processing engine."""

from __future__ import annotations

import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np

from half_frame_darkroom.core.accidents import (
    apply_density_accidents,
    apply_light_leak_to_exposure,
    apply_uneven_development_to_latent_proxy,
)
from half_frame_darkroom.core.atomic_io import atomic_path_set, atomic_savez, atomic_write_json
from half_frame_darkroom.core.color import linear_to_srgb, luminance, srgb_to_linear
from half_frame_darkroom.core.density_grain import apply_density_grain
from half_frame_darkroom.core.halation import apply_halation
from half_frame_darkroom.core.io_utils import load_image, probe_image_dimensions, save_image
from half_frame_darkroom.core.electronic_negative import (
    export_layer_pack,
    export_plate_set,
    export_transparent_plate_set,
    save_linear_rgb_tiff,
    scanner_raw_export_border_width,
    scanner_raw_with_clear_border,
)
from half_frame_darkroom.core.execution import (
    processing_long_edge,
    resolve_execution_mode,
    uses_reduced_implementation,
)
from half_frame_darkroom.core.film_process.integration import (
    develop_bw_negative_reduced,
    develop_bw_reversal_reduced,
    develop_color_negative_reduced,
    develop_color_reversal_reduced,
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
from half_frame_darkroom.core.preview import negative_visual_preview, resize_to_long_edge
from half_frame_darkroom.core.resource_planning import (
    enforce_memory_budget,
    estimate_pipeline_memory,
    warn_outside_comfort_zone,
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
    invert_negative_image,
    light_table_illuminant_rgb,
    negative_backlight_illuminant_rgb,
    negative_material_compensation_matrix,
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
)
from half_frame_darkroom.core.sensitometry import exposure_to_density
from half_frame_darkroom.core.sidecar import (
    developed_negative_sidecar,
    final_positive_sidecar,
    layer_pack_metadata,
    scanner_raw_sidecar,
    transmission_raw_source_kind,
)
from half_frame_darkroom.core.states import DevelopedNegative, ScannedPositive, developed_medium_metadata
from half_frame_darkroom.core.subtractive import density_to_positive_rgb
from half_frame_darkroom.model.config import DarkroomConfig


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
    return np.random.default_rng(), None


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
            "dye": bool(np.any(density > 1e-6)),
            "residual_halide": False,
            "bleached_halide": False,
            "auxiliary_remaining": 0.0,
        },
        "optical_observation": {
            "dye_absorption_matrix": [
                [float(value) for value in row]
                for row in config.film.dye_absorption_matrix
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


def _clear_base_scanner_sample(config: DarkroomConfig) -> np.ndarray:
    """Return the known clear-base scanner sample for generated negatives."""
    clear_density = np.asarray(config.film.density_min, dtype=np.float32).reshape(1, 1, 3)
    return render_negative_image(clear_density, config.film, config.scanner).reshape(1, 1, 3)


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
        return config
    runtime = copy.deepcopy(config or DarkroomConfig())
    if not prepared:
        _force_bw_negative(runtime)
        _apply_look_strength(runtime)
    return runtime


def _validate_rgb_image_array(image: np.ndarray, label: str = "input image") -> np.ndarray:
    array = np.asarray(image)
    if array.ndim != 3 or array.shape[-1] != 3:
        raise ValueError(f"{label} must have shape HxWx3, got {array.shape}.")
    if array.shape[0] <= 0 or array.shape[1] <= 0:
        raise ValueError(f"{label} must have non-zero width and height, got {array.shape}.")
    if not np.isfinite(array).all():
        raise ValueError(f"{label} must contain only finite numeric values.")
    return array


def _validate_developed_medium_arrays(medium: DevelopedNegative) -> None:
    density_cmy = _validate_rgb_image_array(medium.density_cmy, "developed medium density_cmy")
    density_grain = _validate_rgb_image_array(medium.density_grain, "developed medium density_grain")
    if density_cmy.shape != density_grain.shape:
        raise ValueError(
            "developed medium density_cmy and density_grain must have the same shape; "
            f"got {density_cmy.shape} and {density_grain.shape}"
        )


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


def _validate_develop_runtime(config: DarkroomConfig) -> None:
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
        "film.color_matrix": (config.film.color_matrix, (3, 3)),
        "film.hd_gamma": (config.film.hd_gamma, (3,)),
        "film.density_min": (config.film.density_min, (3,)),
        "film.density_max": (config.film.density_max, (3,)),
        "film.log_exposure_toe": (config.film.log_exposure_toe, (3,)),
        "film.log_exposure_shoulder": (config.film.log_exposure_shoulder, (3,)),
        "film.layer_sensitivity_matrix": (config.film.layer_sensitivity_matrix, (3, 3)),
        "film.dye_absorption_matrix": (config.film.dye_absorption_matrix, (3, 3)),
        "film.film_base_density_rgb": (config.film.film_base_density_rgb, (3,)),
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

    d_min = np.asarray(config.film.density_min, dtype=np.float64)
    d_max = np.asarray(config.film.density_max, dtype=np.float64)
    if np.any(d_max <= d_min):
        raise ValueError("film.density_max must be greater than film.density_min in every layer.")
    if len(config.film.grain_scales) == 0 or len(config.film.grain_scales) != len(config.film.grain_scale_weights):
        raise ValueError("film.grain_scales and film.grain_scale_weights must be non-empty and have equal length.")
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
    if str(config.processing.history_storage_policy).strip().lower() not in {
        "full", "cold_fp16"
    }:
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
    arrays = {
        "linear_input": medium.linear_input,
        "after_mtf": medium.after_mtf,
        "after_halation": medium.after_halation,
        "density_cmy": medium.density_cmy,
        "density_grain": medium.density_grain,
    }
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
        "film.film_base_density_rgb": (config.film.film_base_density_rgb, (3,)),
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
            {"negative_inversion", "positive_transparency", "legacy_density_mapping"},
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
) -> None:
    material_contract = process_result.final_medium.contract()
    payload["representation"] = representation
    payload["components"] = material_contract["components"]
    payload["material_pool_optical_observation"] = material_contract["optical_observation"]
    payload["pool_totals"] = process_result.state.totals()
    payload["process_trace"] = [
        {
            "label": report.label,
            "action": report.action,
            "reacted_amount": float(report.reacted_amount),
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
    stain_delta = float(np.clip(rng.normal(0.0, 0.012 * strength), -0.04, 0.05))
    uneven_delta = float(np.clip(rng.normal(0.0, 0.018 * strength), -0.05, 0.07))

    chem.time_min = float(np.clip(float(chem.time_min) * time_factor, 0.01, 240.0))
    chem.temperature_c = float(np.clip(float(chem.temperature_c) + temperature_delta, 0.0, 100.0))
    chem.concentration = float(np.clip(float(chem.concentration) * concentration_factor, 0.01, 10.0))
    chem.agitation = float(np.clip(float(chem.agitation) * agitation_factor, 0.0, 10.0))
    chem.developer_exhaustion = _clip01(float(chem.developer_exhaustion) + exhaustion_delta)
    chem.fixer_exhaustion = _clip01(float(chem.fixer_exhaustion) + fixer_delta)
    chem.chemical_stain = _clip01(float(chem.chemical_stain) + stain_delta)
    chem.uneven_development = _clip01(float(chem.uneven_development) + uneven_delta)

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
    """Auto-select the default interpreter when no scan config was explicit."""
    compatible = tuple(str(value) for value in medium.compatible_interpreters)
    if not compatible:
        return
    key = compatible[0]
    config.scanner.interpreter_key = key
    config.scanner.target_medium_process = str(medium.medium_process)
    config.scanner.input_polarity = str(medium.image_polarity)
    config.scanner.output_polarity = "positive"
    if key == "positive_transparency_scan":
        config.scanner.scan_method = "positive_transparency"
    elif key == "negative_scan" and str(config.scanner.scan_method).lower() == "positive_transparency":
        config.scanner.scan_method = "negative_inversion"


def _validate_interpreter_compatibility(config: DarkroomConfig, medium: DevelopedNegative) -> None:
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


def _material_pool_tile_rows(
    config: DarkroomConfig,
    image: np.ndarray,
) -> int | None:
    """Resolve exact row tiling for large pointwise material transitions."""
    configured_rows = int(config.processing.material_tile_rows)
    if configured_rows <= 0:
        return None
    height, width = image.shape[:2]
    megapixels = height * width / 1_000_000.0
    if megapixels < float(config.processing.material_tile_threshold_megapixels):
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


def _develop_reduced_material_tiled(
    image: np.ndarray,
    config: DarkroomConfig,
    program_kind: str,
    tile_rows: int,
) -> tuple[np.ndarray, dict[str, object]]:
    """Run exact pointwise pool transitions in bounded row tiles."""
    height, width = image.shape[:2]
    density = np.empty((height, width, 3), dtype=np.float32)
    audit: dict[str, object] | None = None
    neutral_silver_sum = np.zeros(3, dtype=np.float64)
    neutral_halide_sum = np.zeros(3, dtype=np.float64)
    neutral_pixels = 0
    material_is_mono = is_monochrome_material(config)
    is_positive = str(developed_medium_contract(config)["image_polarity"]) == "positive"

    for start in range(0, height, tile_rows):
        stop = min(start + tile_rows, height)
        tile_image = image[start:stop]
        if program_kind == "bw_negative":
            reduced = develop_bw_negative_reduced(tile_image, config.film, config.chemistry)
            tile_density = reduced.density_rgb
            representation = (
                "reduced_bw_silver_pool_v1"
                if material_is_mono
                else "reduced_color_material_bw_silver_pool_v1"
            )
        elif program_kind == "bw_reversal":
            reduced = develop_bw_reversal_reduced(tile_image, config.film, config.chemistry)
            tile_density = reduced.density_rgb
            representation = (
                "reduced_bw_reversal_pool_v1"
                if material_is_mono
                else "reduced_color_material_bw_reversal_pool_v1"
            )
        elif program_kind == "color_negative":
            reduced = develop_color_negative_reduced(tile_image, config.film, config.chemistry)
            tile_density = reduced.density_cmy
            representation = "reduced_color_coupler_pool_v1"
        elif program_kind == "color_reversal":
            reduced = develop_color_reversal_reduced(tile_image, config.film, config.chemistry)
            tile_density = reduced.density_cmy
            representation = "reduced_color_reversal_pool_v1"
        else:
            raise ValueError(f"Unsupported tiled material program: {program_kind}")

        density[start:stop] = tile_density
        tile_audit: dict[str, object] = {}
        _attach_reduced_process_contract(
            tile_audit,
            reduced.process_result,
            representation,
            reduced.effective_development,
            reduced.compatibility,
        )
        audit = _merge_reduced_audit_payload(audit, tile_audit)
        if hasattr(reduced, "silver_density_rgb"):
            neutral_silver_sum += np.sum(
                reduced.silver_density_rgb, axis=(0, 1), dtype=np.float64
            )
            neutral_halide_sum += np.sum(
                reduced.residual_halide_density_rgb, axis=(0, 1), dtype=np.float64
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
        }
    audit["execution"] = {
        "material_pool_tiling": "exact_row_tiles_v1",
        "tile_rows": int(tile_rows),
        "tile_count": int((height + tile_rows - 1) // tile_rows),
    }
    return density, audit


def develop_negative(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
    *,
    _prepared_config: bool = False,
) -> DevelopedNegative:
    """Dispatch development through the registered media pipeline."""
    image_srgb = _validate_rgb_image_array(image_srgb)
    if config is not None and not _prepared_config:
        _validate_develop_runtime(config)
    runtime = _runtime_config(config, prepared=_prepared_config)
    _validate_develop_runtime(runtime)
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

    linear = srgb_to_linear(image_srgb)
    linear = linear * (2.0 ** float(config.look.exposure_ev))
    if is_monochrome_material(config):
        linear = np.repeat(luminance(linear)[..., None], 3, axis=-1)
    linear_input = np.clip(linear, 0.0, 1.0)

    if config.enable_mtf:
        after_mtf = apply_emulsion_mtf(linear, config.film)
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
    if config.enable_halation:
        after_halation = apply_halation(
            after_light_leak,
            config.film,
            fast=uses_reduced_implementation(config),
            work_long_edge=_processing_work_long_edge(config, "halation"),
        )
    else:
        after_halation = after_light_leak
    del after_light_leak
    has_light_leak_map = light_leak_map is not None
    del light_leak_map

    after_accidents, uneven_development_map = apply_uneven_development_to_latent_proxy(
        after_halation,
        config.chemistry,
        rng=rng,
        fast=uses_reduced_implementation(config),
        work_long_edge=_processing_work_long_edge(config, "grain"),
    )
    cold_history = (
        str(config.processing.history_storage_policy).strip().lower() == "cold_fp16"
    )
    history_dtype = np.float16 if cold_history else np.float32
    if cold_history:
        # Both arrays have completed their last formation-side read. Compact
        # them before the material pools are allocated, not after development.
        linear_input = np.asarray(linear_input, dtype=np.float16)
        after_mtf_history = np.asarray(after_mtf, dtype=np.float16)
    else:
        after_mtf_history = after_mtf
    del after_mtf, after_halation
    reduced_bw = None
    reduced_color = None
    reduced_process_payload: dict[str, object] | None = None
    tile_rows = _material_pool_tile_rows(config, after_accidents)
    tiled_program_kind: str | None = None
    if _uses_reduced_bw_negative(config):
        tiled_program_kind = "bw_negative"
    elif _uses_reduced_color_negative(config):
        tiled_program_kind = "color_negative"
    elif _uses_reduced_bw_reversal(config):
        tiled_program_kind = "bw_reversal"
    elif _uses_reduced_color_reversal(config):
        tiled_program_kind = "color_reversal"

    if tile_rows is not None and tiled_program_kind is not None:
        density_cmy, reduced_process_payload = _develop_reduced_material_tiled(
            after_accidents,
            config,
            tiled_program_kind,
            tile_rows,
        )
    elif _uses_reduced_bw_negative(config):
        reduced_bw = develop_bw_negative_reduced(after_accidents, config.film, config.chemistry)
        density_cmy = reduced_bw.density_rgb
    elif _uses_reduced_color_negative(config):
        reduced_color = develop_color_negative_reduced(
            after_accidents,
            config.film,
            config.chemistry,
        )
        density_cmy = reduced_color.density_cmy
    elif _uses_reduced_bw_reversal(config):
        reduced_bw = develop_bw_reversal_reduced(
            after_accidents,
            config.film,
            config.chemistry,
        )
        density_cmy = reduced_bw.density_rgb
    elif _uses_reduced_color_reversal(config):
        reduced_color = develop_color_reversal_reduced(
            after_accidents,
            config.film,
            config.chemistry,
        )
        density_cmy = reduced_color.density_cmy
    else:
        density_cmy = exposure_to_density(after_accidents, config.film, config.chemistry)
        if str(developed_medium_contract(config)["image_polarity"]) == "positive":
            density_cmy = _positive_transparency_density_from_negative_proxy(density_cmy, config)
    formed_density_cmy = density_cmy
    after_halation_history = np.asarray(after_accidents, dtype=history_dtype)
    del after_accidents

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
        }
        reduced_color = None

    processed_density_cmy, accident_maps = apply_density_accidents(
        density_cmy,
        config.chemistry,
        rng=rng,
        film=config.film,
        fast=uses_reduced_implementation(config),
        work_long_edge=_processing_work_long_edge(config, "grain"),
    )
    if uneven_development_map is not None:
        accident_maps["uneven_development"] = uneven_development_map
    accident_map_names = tuple(sorted(accident_maps))
    has_silver_plating_map = "silver_plating" in accident_maps
    del accident_maps, uneven_development_map
    if config.enable_grain:
        density_grain = apply_density_grain(
            processed_density_cmy,
            config.film,
            config.chemistry,
            rng=rng,
            fast=uses_reduced_implementation(config),
            work_long_edge=_processing_work_long_edge(config, "grain"),
        )
    else:
        density_grain = processed_density_cmy
    del processed_density_cmy
    if is_monochrome_material(config):
        density_grain = np.repeat(density_grain.mean(axis=-1, keepdims=True), 3, axis=-1)

    medium_contract = developed_medium_contract(config)
    is_positive = str(medium_contract["image_polarity"]) == "positive"
    final_medium_contract = _legacy_final_medium_contract(density_grain, config, medium_contract)
    if reduced_process_payload is not None:
        final_medium_contract.update(reduced_process_payload)
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
    return DevelopedNegative(
        linear_input=np.asarray(linear_input, dtype=history_dtype),
        after_mtf=np.clip(after_mtf_history, 0.0, 1.0).astype(history_dtype),
        after_halation=np.clip(after_halation_history, 0.0, 1.0).astype(history_dtype),
        density_cmy=formed_density_cmy.astype(np.float32),
        density_grain=density_grain.astype(np.float32),
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
            "has_light_leak_map": has_light_leak_map,
            "accident_maps": accident_map_names,
            "accident_stages": {
                "light_leak": "pre_latent_exposure",
                "uneven_development": "development_formation",
                "chemical_stain": "post_process_pre_grain",
                "silver_plating": "post_process_surface_deposit_pre_grain",
            },
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

    density_cmy = d_min + np.clip(positive_norm, 0.0, 1.0) * density_range

    saturation = max(float(getattr(config.film, "positive_dye_saturation", 1.0)), 0.0)
    if abs(saturation - 1.0) > 1e-6:
        neutral = density_cmy.mean(axis=-1, keepdims=True)
        density_cmy = neutral + (density_cmy - neutral) * saturation
    return np.clip(density_cmy, d_min, d_max).astype(np.float32)


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
        "reversal": "positive",
        "slide": "positive",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"auto", "negative", "positive"}:
        raise ValueError(f"Unsupported scan interpretation mode: {mode}")
    config.scanner.interpretation_mode = normalized
    if normalized == "negative":
        config.scanner.interpreter_key = "negative_scan"
        config.scanner.target_medium_process = "negative"
        config.scanner.input_polarity = "negative"
        config.scanner.output_polarity = "positive"
        if str(config.scanner.scan_method).lower() == "positive_transparency":
            config.scanner.scan_method = "negative_inversion"
    elif normalized == "positive":
        config.scanner.interpreter_key = "positive_transparency_scan"
        config.scanner.target_medium_process = "positive"
        config.scanner.input_polarity = "positive"
        config.scanner.output_polarity = "positive"
        config.scanner.scan_method = "positive_transparency"
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
    if selected == "negative":
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
    auto_bw = _density_is_monochrome(negative.density_grain)
    if auto_bw:
        config.mode = "bw_negative"
    config = _runtime_config(config, prepared=prepared)
    if auto_bw:
        _force_bw_negative(config, include_scanner=True)
    _validate_scan_runtime(config)

    base_samples = _clear_base_scanner_sample(config)
    positive_no_grain = density_to_positive_rgb(
        negative.density_cmy,
        config.film,
        config.scanner,
        print_contrast=config.look.print_contrast,
        print_exposure_ev=config.look.print_exposure_ev,
        base_samples=base_samples,
    )
    negative_total_density = negative_total_density_rgb(negative.density_grain, config.film)
    negative_linear = capture_optical_density(
        negative_total_density,
        config.scanner,
        illuminant_rgb=negative_backlight_illuminant_rgb(config.scanner),
    )
    scanner_raw = negative_linear
    return _scan_scanner_raw_array(
        scanner_raw,
        config,
        base_samples=base_samples,
        positive_no_grain=np.clip(positive_no_grain, 0.0, 1.0).astype(np.float32),
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
    negative_total_density = negative_total_density_rgb(negative.density_grain, config.film)
    scanner_raw = capture_optical_density(
        negative_total_density,
        config.scanner,
        illuminant_rgb=light_table_illuminant_rgb(config.scanner),
    )
    positive_linear = render_positive_transparency_scan(
        scanner_raw,
        config.scanner,
        print_contrast=config.look.print_contrast,
        print_exposure_ev=config.look.print_exposure_ev,
    )
    if config.scanner.scan_normalize:
        positive_linear = normalize_scan_rgb(
            positive_linear,
            black_percentile=config.scanner.scan_black_percentile,
            white_percentile=config.scanner.scan_white_percentile,
            strength=config.scanner.scan_normalize_strength,
            mode=config.scanner.scan_normalize_mode,
        )
    positive_linear = np.clip(positive_linear, 0.0, 1.0).astype(np.float32)
    return ScannedPositive(
        negative_linear=scanner_raw.astype(np.float32),
        negative_base_balanced=scanner_raw.astype(np.float32),
        positive_raw=scanner_raw.astype(np.float32),
        negative_channel_reconstructed=scanner_raw.astype(np.float32),
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
    source_path: str | Path | None = None,
    _prepared_config: bool = False,
) -> ScannedPositive:
    """把电子负片 scanner raw 直接解释成正像，适合 scan-only 快速重扫。"""
    config = _runtime_config(config, prepared=_prepared_config)
    return _scan_scanner_raw_array(
        scanner_raw,
        config,
        base_samples=base_samples,
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
    if selected == "negative":
        scanned = scan_scanner_raw(
            scanner_raw,
            runtime,
            base_samples=base_samples,
            source_path=source_path,
            _prepared_config=True,
        )
    else:
        raw = np.clip(np.asarray(scanner_raw, dtype=np.float32), 1e-6, 1.0)
        positive_linear = render_positive_transparency_scan(
            raw,
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
        positive_linear = np.clip(positive_linear, 0.0, 1.0).astype(np.float32)
        scanned = ScannedPositive(
            negative_linear=raw,
            negative_base_balanced=raw,
            positive_raw=raw,
            negative_channel_reconstructed=raw,
            scanner_raw=raw,
            negative_total_density=(-np.log10(raw)).astype(np.float32),
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
    metadata.setdefault(
        "base_balance_source",
        "explicit_clear_base_samples" if base_samples is not None else "image_percentile_fallback",
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
    if base_samples is not None:
        base = np.clip(np.asarray(base_samples, dtype=np.float32), 1e-6, 1.0).reshape(-1, 3)
        base_median = np.median(base, axis=0)
        metadata["base_anchor_rgb"] = [float(value) for value in base_median]
        metadata["base_saturated_channels"] = [
            bool(value >= 1.0 - 1e-6) for value in base_median
        ]
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
        compensation_matrix = negative_material_compensation_matrix(
            config.film.dye_absorption_matrix,
            strength=config.scanner.negative_channel_compensation_strength,
        )
        metadata["negative_material_compensation_matrix"] = [
            [float(value) for value in row] for row in compensation_matrix
        ]
    negative_linear = scanner_raw
    if negative_total_density is None:
        negative_total_density = (-np.log10(scanner_raw)).astype(np.float32)

    negative_base_balanced = balance_negative_base(
        negative_linear,
        base_percentile=config.scanner.scan_base_percentile,
        base_samples=base_samples,
    )
    positive_raw = invert_negative_image(negative_base_balanced)
    negative_channel_reconstructed = reconstruct_negative_channels(
        positive_raw,
        config.scanner,
        dye_absorption_matrix=config.film.dye_absorption_matrix,
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
                dye_absorption_matrix=config.film.dye_absorption_matrix,
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

    if config.scanner.scan_normalize:
        positive_linear = normalize_scan_rgb(
            positive_linear,
            black_percentile=config.scanner.scan_black_percentile,
            white_percentile=config.scanner.scan_white_percentile,
            strength=config.scanner.scan_normalize_strength,
            mode=config.scanner.scan_normalize_mode,
        )

    positive_linear = np.clip(positive_linear, 0.0, 1.0).astype(np.float32)
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
        positive_no_grain=np.clip(positive_no_grain, 0.0, 1.0).astype(np.float32),
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
    """Apply one global normalization, then encode bounded row tiles in place."""
    height = positive_linear.shape[0]
    if config.scanner.scan_normalize:
        black, white = scan_normalization_range(
            positive_linear,
            black_percentile=config.scanner.scan_black_percentile,
            white_percentile=config.scanner.scan_white_percentile,
            mode=config.scanner.scan_normalize_mode,
        )
        for start in range(0, height, tile_rows):
            stop = min(start + tile_rows, height)
            positive_linear[start:stop] = apply_scan_normalization_range(
                positive_linear[start:stop],
                black,
                white,
                strength=config.scanner.scan_normalize_strength,
            )
    else:
        np.clip(positive_linear, 0.0, 1.0, out=positive_linear)

    for start in range(0, height, tile_rows):
        stop = min(start + tile_rows, height)
        positive_linear[start:stop] = linear_to_srgb(positive_linear[start:stop])
    return positive_linear


def _scan_negative_output_tiled(
    medium: DevelopedNegative,
    config: DarkroomConfig,
    tile_rows: int,
) -> np.ndarray:
    """Observe a generated negative in exact bounded tiles using one base anchor."""
    height, width = medium.density_grain.shape[:2]
    positive_linear = np.empty((height, width, 3), dtype=np.float32)
    base_samples = _clear_base_scanner_sample(config)
    samples = np.clip(np.asarray(base_samples, dtype=np.float32), 1e-6, 1.0).reshape(-1, 3)
    base = np.percentile(samples, 50.0, axis=0).astype(np.float32).reshape(1, 1, 3)
    illuminant = negative_backlight_illuminant_rgb(config.scanner)

    for start in range(0, height, tile_rows):
        stop = min(start + tile_rows, height)
        density_tile = medium.density_grain[start:stop]
        total_density = negative_total_density_rgb(density_tile, config.film)
        scanner_raw = capture_optical_density(
            total_density,
            config.scanner,
            illuminant_rgb=illuminant,
        )
        base_balanced = np.clip(scanner_raw / np.maximum(base, 1e-6), 1e-6, 1.0).astype(
            np.float32
        )
        positive_raw = invert_negative_image(base_balanced)
        reconstructed = reconstruct_negative_channels(
            positive_raw,
            config.scanner,
            dye_absorption_matrix=config.film.dye_absorption_matrix,
        )
        positive_linear[start:stop] = render_positive_scan(
            reconstructed,
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
        )
        del total_density, scanner_raw, base_balanced, positive_raw, reconstructed
    return _normalize_encode_output_tiled(positive_linear, config, tile_rows)


def _scan_positive_output_tiled(
    medium: DevelopedNegative,
    config: DarkroomConfig,
    tile_rows: int,
) -> np.ndarray:
    """Observe a positive transparency in exact bounded tiles."""
    height, width = medium.density_grain.shape[:2]
    positive_linear = np.empty((height, width, 3), dtype=np.float32)
    illuminant = light_table_illuminant_rgb(config.scanner)
    for start in range(0, height, tile_rows):
        stop = min(start + tile_rows, height)
        total_density = negative_total_density_rgb(
            medium.density_grain[start:stop], config.film
        )
        scanner_raw = capture_optical_density(
            total_density,
            config.scanner,
            illuminant_rgb=illuminant,
        )
        positive_linear[start:stop] = render_positive_transparency_scan(
            scanner_raw,
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
        )
        del total_density, scanner_raw
    return _normalize_encode_output_tiled(positive_linear, config, tile_rows)


def _scan_negative_output_only(
    medium: DevelopedNegative,
    config: DarkroomConfig,
) -> np.ndarray:
    """Render a negative without retaining Inspector-only scan stages."""
    _validate_developed_medium_arrays(medium)
    auto_bw = _density_is_monochrome(medium.density_grain)
    if auto_bw:
        config.mode = "bw_negative"
        _force_bw_negative(config, include_scanner=True)
    _validate_scan_runtime(config)

    tile_rows = _scan_output_tile_rows(config, medium.density_grain)
    if tile_rows is not None and config.enable_subtractive:
        return _scan_negative_output_tiled(medium, config, tile_rows)

    base_samples = _clear_base_scanner_sample(config)
    total_density = negative_total_density_rgb(medium.density_grain, config.film)
    scanner_raw = capture_optical_density(
        total_density,
        config.scanner,
        illuminant_rgb=negative_backlight_illuminant_rgb(config.scanner),
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
            base_samples=base_samples,
            dye_absorption_matrix=config.film.dye_absorption_matrix,
        )
        del scanner_raw
    else:
        base_balanced = balance_negative_base(
            scanner_raw,
            base_percentile=config.scanner.scan_base_percentile,
            base_samples=base_samples,
        )
        del scanner_raw
        positive_raw = invert_negative_image(base_balanced)
        del base_balanced
        reconstructed = reconstruct_negative_channels(
            positive_raw,
            config.scanner,
            dye_absorption_matrix=config.film.dye_absorption_matrix,
        )
        del positive_raw
        if config.enable_subtractive:
            positive_linear = render_positive_scan(
                reconstructed,
                config.scanner,
                print_contrast=config.look.print_contrast,
                print_exposure_ev=config.look.print_exposure_ev,
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
        )
    return linear_to_srgb(np.clip(positive_linear, 0.0, 1.0).astype(np.float32))


def _scan_positive_output_only(
    medium: DevelopedNegative,
    config: DarkroomConfig,
) -> np.ndarray:
    """Render a positive transparency without retaining duplicate raw stages."""
    _validate_developed_medium_arrays(medium)
    _validate_scan_runtime(config)
    tile_rows = _scan_output_tile_rows(config, medium.density_grain)
    if tile_rows is not None:
        return _scan_positive_output_tiled(medium, config, tile_rows)
    total_density = negative_total_density_rgb(medium.density_grain, config.film)
    scanner_raw = capture_optical_density(
        total_density,
        config.scanner,
        illuminant_rgb=light_table_illuminant_rgb(config.scanner),
    )
    del total_density
    positive_linear = render_positive_transparency_scan(
        scanner_raw,
        config.scanner,
        print_contrast=config.look.print_contrast,
        print_exposure_ev=config.look.print_exposure_ev,
    )
    del scanner_raw
    if config.scanner.scan_normalize:
        positive_linear = normalize_scan_rgb(
            positive_linear,
            black_percentile=config.scanner.scan_black_percentile,
            white_percentile=config.scanner.scan_white_percentile,
            strength=config.scanner.scan_normalize_strength,
            mode=config.scanner.scan_normalize_mode,
        )
    return linear_to_srgb(np.clip(positive_linear, 0.0, 1.0).astype(np.float32))


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

    if str(runtime.scanner.interpreter_key).lower() == "positive_transparency_scan":
        return _scan_positive_output_only(medium, runtime), runtime
    return _scan_negative_output_only(medium, runtime), runtime


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
) -> tuple[np.ndarray, DarkroomConfig, DevelopedNegative]:
    """Production path: form the full medium, retain only the observed output."""
    config = _runtime_config(config)
    config.processing.history_storage_policy = (
        "full" if retain_development_stages else "cold_fp16"
    )
    if rng is None:
        rng, _ = _rng_for_input(None, config)
    medium = develop_negative(image_srgb, config, rng=rng, _prepared_config=True)
    runtime_config = medium.metadata.get("runtime_config", config)
    if not isinstance(runtime_config, DarkroomConfig):
        runtime_config = config
    if not retain_development_stages:
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
    output, scan_runtime = _scan_medium_output_only(
        medium,
        runtime_config,
        str(config.scanner.interpretation_mode),
    )
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
    output, _, _ = _process_output_with_runtime(image_srgb, config, rng=rng)
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
        positive_transmission_raw = render_transparency_image(negative.density_grain, config.film, config.scanner)
        preview_linear = positive_transmission_raw
        save_image(linear_to_srgb(np.clip(preview_linear, 0.0, 1.0)), preview_path, config.output)
        del preview_linear
        paths["positive_visual_preview"] = str(preview_path)
    else:
        save_image(negative_visual_preview(negative.density_grain, config.film), preview_path, config.output)
        paths["negative_visual_preview"] = str(preview_path)

    scanner_raw_path: Path | None = None
    scanner_raw_border_px = 0
    if config.output.save_scanner_raw:
        if is_positive:
            scanner_raw = positive_transmission_raw
            if scanner_raw is None:
                scanner_raw = render_transparency_image(negative.density_grain, config.film, config.scanner)
            scanner_raw_path = negative_path.with_suffix(".light_table_raw.tiff")
        else:
            scanner_raw_border_px = scanner_raw_export_border_width(
                negative.density_grain.shape,
                config.output.scanner_raw_border_percent,
                config.output.scanner_raw_border_min_px,
            )
            scanner_raw = scanner_raw_with_clear_border(
                negative.density_grain,
                config.film,
                config.scanner,
                border_percent=config.output.scanner_raw_border_percent,
                border_min_px=config.output.scanner_raw_border_min_px,
            )
            if config.output.watermark_scanner_raw_border:
                scanner_raw = apply_scanner_raw_border_watermark(
                    scanner_raw,
                    provenance,
                    border_width=scanner_raw_border_px,
                )
            scanner_raw_path = negative_path.with_suffix(".scanner_raw.tiff")
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
