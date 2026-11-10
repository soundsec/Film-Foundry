"""观察侧对已形成介质的只读衔接。

这里恢复材料光学快照、拼扫描解释，并准备片基参考。
不形成潜影，不写回介质。公开名字仍挂在 engine 上，扫描编排按那些名字调用。
材料与观察的阶段边界见公开文档《统一胶片冲洗模型》。
"""

from __future__ import annotations

import copy
from dataclasses import replace

import numpy as np

from film_foundry.core.electronic_negative import scanner_raw_export_border_width
from film_foundry.core.scanner import (
    negative_total_density_rgb,
    transmission_illuminant_rgb,
)
from film_foundry.core.spectral_optics import spectral_observation_density
from film_foundry.core.states import DevelopedNegative
from film_foundry.model.config import DarkroomConfig


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
    from film_foundry.core import engine as _host
    apply_optical_observation_snapshot = _host.apply_optical_observation_snapshot
    return apply_optical_observation_snapshot(
        config,
        _final_medium_observation_payload(medium),
    )


def _clear_base_scanner_sample(
    config: DarkroomConfig,
    medium: DevelopedNegative | None = None,
) -> np.ndarray:
    """Return the known clear-base scanner sample for generated negatives."""
    from film_foundry.core import engine as _host
    capture_optical_density = _host.capture_optical_density
    _clear_base_optical_density_rgb = _host._clear_base_optical_density_rgb
    if config.scanner.spectral_observation_profile is not None:
        clear_density = spectral_observation_density(
            None if medium is None else medium.spectral_medium,
            config.scanner.spectral_observation_profile, clear_base=True,
        )
    else:
        clear_density = _clear_base_optical_density_rgb(config, medium).reshape(1, 1, 3)
    return capture_optical_density(
        clear_density,
        config.scanner,
        # A nominal-exposure reference removes support density, without also
        # dividing away the user's lamp exposure change.
        illuminant_rgb=transmission_illuminant_rgb(replace(config.scanner, transmission_light_ev=0.0)),
    ).reshape(1, 1, 3)


def _clear_base_optical_density_rgb(
    config: DarkroomConfig,
    medium: DevelopedNegative | None = None,
) -> np.ndarray:
    if config.scanner.spectral_observation_profile is not None:
        return spectral_observation_density(None if medium is None else medium.spectral_medium,
            config.scanner.spectral_observation_profile, clear_base=True).reshape(3)
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
    from film_foundry.core import engine as _host
    _clear_base_optical_density_rgb = _host._clear_base_optical_density_rgb
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
    from film_foundry.core import engine as _host
    _config_from_developed_medium_identity = _host._config_from_developed_medium_identity
    _apply_final_medium_observation = _host._apply_final_medium_observation
    _with_scan_interpretation = _host._with_scan_interpretation
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



def _medium_optical_density_rgb(
    medium: DevelopedNegative,
    film,
    row_slice: slice | None = None,
    scanner=None,
) -> np.ndarray:
    """Return authoritative RGB density, falling back to the portable CMY master."""
    if scanner is not None and scanner.spectral_observation_profile is not None:
        from .observation_reuse import observe_density
        return observe_density(medium.spectral_medium, scanner.spectral_observation_profile,
                               row_slice, spectral_observation_density)
    if medium.optical_density_rgb is not None:
        optical = np.asarray(medium.optical_density_rgb, dtype=np.float32)
        return optical if row_slice is None else optical[row_slice]
    density = medium.density_grain if row_slice is None else medium.density_grain[row_slice]
    return negative_total_density_rgb(density, film)


def _resolve_scan_interpretation_config(
    negative: DevelopedNegative,
    config: DarkroomConfig | None,
    prepared_config: bool,
) -> tuple[DarkroomConfig, bool]:
    """Resolve scan-time config without changing the developed negative identity."""
    from film_foundry.core import engine as _host
    _apply_final_medium_observation = _host._apply_final_medium_observation
    _with_scan_interpretation = _host._with_scan_interpretation
    _config_from_developed_medium_identity = _host._config_from_developed_medium_identity
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
    # Automatic endpoints share one luminance range. Independent RGB ranges
    # would silently white-balance a deliberately coloured material or scan.
    # Strength, percentiles and the user's enable switch remain explicit.
    config.scanner.scan_normalize_mode = "luma"
    if key == "positive_transparency_scan":
        config.scanner.remove_base_mask = False
        config.scanner.invert_transmission = False
        config.scanner.scan_method = "positive_transparency"
    elif key == "negative_scan":
        config.scanner.remove_base_mask = True
        config.scanner.invert_transmission = True
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


