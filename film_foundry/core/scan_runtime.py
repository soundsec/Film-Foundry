"""扫描编排。

公开入口从 engine 再导出。形成和落盘不在这里。
需要的材料光学和片基由 engine 上的观察函数提供，本文件不导入 engine。
"""

from __future__ import annotations

from pathlib import Path
from film_foundry.core.spectral_optics import spectral_capture_record

import numpy as np

from film_foundry.core.color import linear_to_srgb
from film_foundry.core.electronic_negative import (
    scanner_raw_export_border_width,
    scanner_raw_with_reference_border,
)
from film_foundry.core.execution_topology import reference_execution_enabled
from film_foundry.core.scan_output import ScanOutputPlan
from film_foundry.core.media import config_medium_process
from film_foundry.core.media_registry import get_scan_pipeline
from film_foundry.core.runtime_validate import (
    _validate_developed_medium_arrays,
    _validate_rgb_image_array,
    _validate_scan_runtime,
)
from film_foundry.core.scanner import (
    apply_scan_normalization_range,
    balance_negative_base,
    estimate_negative_base_transmittance,
    invert_negative_image,
    negative_scanner_compensation_matrix,
    normalize_scan_rgb,
    reconstruct_negative_channels,
    render_positive_scan,
    render_positive_transparency_scan,
    scanner_raw_to_positive_rgb,
    scanner_color_response_record,
    scanner_light_exposure_record,
    apply_scanner_tone_curve,
    transmission_illuminant_rgb,
)
from film_foundry.core.sidecar import transmission_raw_source_kind
from film_foundry.core.frame_identity import FRAME_IDENTITY_KEY, frame_identity_from_mapping
from film_foundry.core.states import DevelopedNegative, ScanOutput, ScannedPositive
from film_foundry.core.subtractive import density_to_positive_rgb
from film_foundry.model.config import DarkroomConfig


# The formation engine composes this internal runtime with its optical/config
# services. Keep this finite contract explicit; never import the engine here or
# copy its namespace. Callbacks retain the historical engine patch points.
_ENGINE_SERVICE_NAMES = (
    '_align_interpreter_to_medium',
    '_apply_final_medium_observation',
    '_clear_base_optical_density_rgb',
    '_clear_base_scanner_sample',
    '_clip01',
    '_config_from_developed_medium_identity',
    '_medium_optical_density_rgb',
    '_optical_density_with_optional_reference_border',
    '_resolve_scan_interpretation_config',
    '_runtime_config',
    '_validate_interpreter_compatibility',
    '_with_scan_interpretation',
    'apply_optical_observation_snapshot',
    'capture_optical_density',
    'scan_normalization_range',
    '_density_is_monochrome',
    '_developed_medium_is_monochrome',
    '_force_bw_negative',
)
_engine_services = {}


def _encode_scan_output(image, scanner, *, consume_input=False):
    """Final display encoding and independent tone adjustment after normalization."""
    encoded = linear_to_srgb(image, consume_input=consume_input)
    return apply_scanner_tone_curve(encoded, scanner, consume_input=True)


def _bind_engine_services(services):
    if set(services) != set(_ENGINE_SERVICE_NAMES):
        raise ValueError("Incomplete scan runtime service contract")
    if not all(callable(service) for service in services.values()):
        raise TypeError("Scan runtime services must be callable")
    global _engine_services
    _engine_services = dict(services)


def _align_interpreter_to_medium(*args, **kwargs):
    return _engine_services['_align_interpreter_to_medium'](*args, **kwargs)

def _apply_final_medium_observation(*args, **kwargs):
    return _engine_services['_apply_final_medium_observation'](*args, **kwargs)

def _clear_base_optical_density_rgb(*args, **kwargs):
    return _engine_services['_clear_base_optical_density_rgb'](*args, **kwargs)

def _clear_base_scanner_sample(*args, **kwargs):
    return _engine_services['_clear_base_scanner_sample'](*args, **kwargs)

def _clip01(*args, **kwargs):
    return _engine_services['_clip01'](*args, **kwargs)

def _config_from_developed_medium_identity(*args, **kwargs):
    return _engine_services['_config_from_developed_medium_identity'](*args, **kwargs)

def _medium_optical_density_rgb(*args, **kwargs):
    return _engine_services['_medium_optical_density_rgb'](*args, **kwargs)

def _optical_density_with_optional_reference_border(*args, **kwargs):
    return _engine_services['_optical_density_with_optional_reference_border'](*args, **kwargs)

def _resolve_scan_interpretation_config(*args, **kwargs):
    return _engine_services['_resolve_scan_interpretation_config'](*args, **kwargs)

def _runtime_config(*args, **kwargs):
    return _engine_services['_runtime_config'](*args, **kwargs)

def _validate_interpreter_compatibility(*args, **kwargs):
    return _engine_services['_validate_interpreter_compatibility'](*args, **kwargs)

def _with_scan_interpretation(*args, **kwargs):
    return _engine_services['_with_scan_interpretation'](*args, **kwargs)

def apply_optical_observation_snapshot(*args, **kwargs):
    return _engine_services['apply_optical_observation_snapshot'](*args, **kwargs)

def capture_optical_density(*args, **kwargs):
    return _engine_services['capture_optical_density'](*args, **kwargs)

def scan_normalization_range(*args, **kwargs):
    return _engine_services['scan_normalization_range'](*args, **kwargs)

def _density_is_monochrome(*args, **kwargs):
    return _engine_services['_density_is_monochrome'](*args, **kwargs)


def _developed_medium_is_monochrome(*args, **kwargs):
    return _engine_services['_developed_medium_is_monochrome'](*args, **kwargs)


def _force_bw_negative(*args, **kwargs):
    return _engine_services['_force_bw_negative'](*args, **kwargs)


def _copy_frame_identity(medium: DevelopedNegative, metadata: dict) -> None:
    identity = frame_identity_from_mapping(getattr(medium, "metadata", None))
    if identity is not None:
        metadata[FRAME_IDENTITY_KEY] = dict(identity)


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
    scanned.metadata["scanner_color_response"] = scanner_color_response_record(
        runtime.scanner
    )
    scanned.metadata["optical_capture"] = spectral_capture_record(runtime.scanner)
    scanned.metadata["scanner_light_exposure"] = scanner_light_exposure_record(runtime.scanner)
    _copy_frame_identity(negative, scanned.metadata)
    return scanned


scan_medium = scan_negative


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
        scanned.metadata["scanner_color_response"] = scanner_color_response_record(
            runtime.scanner
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
    scanned.metadata["scanner_color_response"] = scanner_color_response_record(
        runtime.scanner
    )
    scanned.metadata["optical_capture"] = spectral_capture_record(runtime.scanner)
    scanned.metadata["scanner_light_exposure"] = scanner_light_exposure_record(runtime.scanner)
    _copy_frame_identity(medium, scanned.metadata)
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
    negative_total_density = _medium_optical_density_rgb(negative, config.film, scanner=config.scanner)
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
    if positive_no_grain is None:
        positive_no_grain_payload = None
    elif reference_execution_enabled():
        positive_no_grain_payload = np.clip(
            positive_no_grain,
            0.0,
            1.0,
        ).astype(np.float32, copy=False)
    else:
        # This compatibility preview was created solely for the returned
        # Inspector state, so its final range clamp may consume the private
        # buffer instead of allocating another full RGB copy.
        np.clip(positive_no_grain, 0.0, 1.0, out=positive_no_grain)
        positive_no_grain_payload = positive_no_grain
    return _scan_scanner_raw_array(
        scanner_raw,
        config,
        known_base_transmittance_rgb=known_base,
        positive_no_grain=positive_no_grain_payload,
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
        consume_scanner_raw=not reference_execution_enabled(),
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
    negative_total_density = _medium_optical_density_rgb(negative, config.film, scanner=config.scanner)
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
    if reference_execution_enabled():
        positive_linear = render_positive_transparency_scan(
            interpreted_raw,
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
        )
    else:
        # interpreted_raw remains a public Inspector stage.  A single private
        # copy becomes the positive render and is transferred through every
        # observation-only mapping without retaining expression temporaries.
        positive_linear = render_positive_transparency_scan(
            interpreted_raw.copy(),
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
            consume_input=True,
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
            consume_input=not reference_execution_enabled(),
        )
    if reference_execution_enabled():
        positive_linear = np.clip(positive_linear, 0.0, 1.0).astype(
            np.float32,
            copy=False,
        )
        output_srgb = _encode_scan_output(positive_linear, config.scanner)
        negative_linear_snapshot = scanner_raw.astype(np.float32)
        negative_base_balanced_snapshot = interpreted_raw.astype(np.float32)
        positive_raw_snapshot = interpreted_raw.astype(np.float32)
        reconstructed_snapshot = interpreted_raw.astype(np.float32)
        scanner_raw_snapshot = scanner_raw.astype(np.float32)
    else:
        np.clip(positive_linear, 0.0, 1.0, out=positive_linear)
        output_srgb = _encode_scan_output(
            positive_linear.copy(),
            config.scanner,
            consume_input=True,
        )
        # Preserve the established independently mutable diagnostic fields.
        # Transfer scanner_raw to one field and create exactly the remaining
        # snapshots instead of copying all five names at the return boundary.
        scanner_raw_snapshot = np.asarray(scanner_raw, dtype=np.float32)
        negative_linear_snapshot = scanner_raw_snapshot.copy()
        negative_base_balanced_snapshot = np.asarray(
            interpreted_raw,
            dtype=np.float32,
        ).copy()
        positive_raw_snapshot = np.asarray(
            interpreted_raw,
            dtype=np.float32,
        ).copy()
        reconstructed_snapshot = np.asarray(
            interpreted_raw,
            dtype=np.float32,
        ).copy()
    return ScannedPositive(
        negative_linear=negative_linear_snapshot,
        negative_base_balanced=negative_base_balanced_snapshot,
        positive_raw=positive_raw_snapshot,
        negative_channel_reconstructed=reconstructed_snapshot,
        scanner_raw=scanner_raw_snapshot,
        negative_total_density=negative_total_density,
        positive_linear=positive_linear,
        output_srgb=output_srgb,
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
    if config.scanner.spectral_observation_profile is not None:
        raise ValueError("Spectral observation requires final material components, not captured RGB scanner raw.")
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
    if config.scanner.spectral_observation_profile is not None:
        raise ValueError("Spectral observation requires final material components, not captured RGB scanner raw.")
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
            output_srgb=_encode_scan_output(positive_linear, runtime.scanner),
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
    consume_scanner_raw: bool = False,
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
    metadata.setdefault(
        "scanner_color_response",
        scanner_color_response_record(config.scanner),
    )
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
    scanner_raw = np.asarray(scanner_raw, dtype=np.float32)
    can_consume_scanner_raw = bool(
        consume_scanner_raw
        and scanner_raw.dtype == np.float32
        and scanner_raw.flags.writeable
        and not reference_execution_enabled()
    )
    if can_consume_scanner_raw:
        np.clip(scanner_raw, 1e-6, 1.0, out=scanner_raw)
    else:
        scanner_raw = np.clip(scanner_raw, 1e-6, 1.0)
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
            if reference_execution_enabled():
                positive_linear = render_positive_scan(
                    negative_channel_reconstructed,
                    config.scanner,
                    print_contrast=config.look.print_contrast,
                    print_exposure_ev=config.look.print_exposure_ev,
                )
            else:
                # The reconstructed channels remain an Inspector diagnostic.
                # One explicit private copy becomes positive_linear and is
                # transferred through the bit-exact consuming render path,
                # avoiding the legacy chain of full RGB expression buffers.
                positive_linear = render_positive_scan(
                    negative_channel_reconstructed.copy(),
                    config.scanner,
                    print_contrast=config.look.print_contrast,
                    print_exposure_ev=config.look.print_exposure_ev,
                    consume_input=True,
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
            consume_input=not reference_execution_enabled(),
        )

    if reference_execution_enabled():
        positive_linear = np.clip(positive_linear, 0.0, 1.0).astype(
            np.float32,
            copy=False,
        )
        output_srgb = _encode_scan_output(positive_linear, config.scanner)
    else:
        np.clip(positive_linear, 0.0, 1.0, out=positive_linear)
        # positive_linear is retained as an Inspector stage.  Its explicit
        # copy is the final output buffer and can be consumed by the bit-exact
        # sRGB encoder, avoiding the encoder's second private RGB expression.
        output_srgb = _encode_scan_output(
            positive_linear.copy(),
            config.scanner,
            consume_input=True,
        )
    if positive_no_grain is None:
        positive_no_grain = positive_linear
    if reference_execution_enabled():
        negative_linear_snapshot = negative_linear.astype(np.float32)
        scanner_raw_snapshot = scanner_raw.astype(np.float32)
    else:
        # These two public names describe the same physical sensor-stage
        # sample, but the established full-state object exposes independently
        # mutable snapshots.  Transfer the private raw buffer to one field and
        # make exactly one final copy for the other; this preserves mutation
        # isolation without the legacy two-copy return boundary.
        negative_linear_snapshot = np.asarray(negative_linear, dtype=np.float32)
        scanner_raw_snapshot = negative_linear_snapshot.copy()
    if reference_execution_enabled():
        positive_no_grain_snapshot = np.clip(
            positive_no_grain,
            0.0,
            1.0,
        ).astype(np.float32, copy=False)
    else:
        positive_no_grain_array = np.asarray(
            positive_no_grain,
            dtype=np.float32,
        )
        if positive_no_grain_array.flags.writeable:
            np.clip(
                positive_no_grain_array,
                0.0,
                1.0,
                out=positive_no_grain_array,
            )
            positive_no_grain_snapshot = positive_no_grain_array
        else:
            positive_no_grain_snapshot = np.clip(
                positive_no_grain_array,
                0.0,
                1.0,
            ).astype(np.float32, copy=False)
    return ScannedPositive(
        negative_linear=negative_linear_snapshot,
        negative_base_balanced=negative_base_balanced,
        positive_raw=positive_raw,
        negative_channel_reconstructed=negative_channel_reconstructed,
        scanner_raw=scanner_raw_snapshot,
        negative_total_density=np.asarray(negative_total_density, dtype=np.float32),
        positive_linear=positive_linear,
        output_srgb=output_srgb,
        positive_no_grain=positive_no_grain_snapshot,
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
    global_black = global_white = None
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
    if not reference_execution_enabled():
        plan = ScanOutputPlan.prepare(
            config.scanner, black=global_black, white=global_white,
        )
        for start in range(0, height, tile_rows):
            stop = min(start + tile_rows, height)
            tile = positive_linear[start:stop]
            encoded = plan.apply_cpu(tile, consume_input=True)
            if not np.shares_memory(encoded, tile):
                tile[...] = encoded
        return positive_linear

    # Keep the original separate frame passes available for exact A/B and rollback.
    if global_black is not None:
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
        _encode_scan_output(
            positive_linear[start:stop],
            config.scanner,
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
            scanner=config.scanner,
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
            scanner=config.scanner,
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
    total_density = _medium_optical_density_rgb(medium, config.film, scanner=config.scanner)
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
    return _encode_scan_output(positive_linear, config.scanner, consume_input=True)


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
    total_density = _medium_optical_density_rgb(medium, config.film, scanner=config.scanner)
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
    return _encode_scan_output(positive_linear, config.scanner, consume_input=True)


def _scan_medium_output_only(
    medium: DevelopedNegative,
    config: DarkroomConfig,
    interpretation_mode: str,
) -> tuple[np.ndarray, DarkroomConfig]:
    """Resolve observation exactly like the full scanner but return only pixels."""
    normalized = str(interpretation_mode or "auto").strip().lower()
    if normalized in {"", "auto"}:
        resolved, prepared = _resolve_scan_interpretation_config(medium, config, False)
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
    output = ScanOutput(
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
            "scanner_color_response": scanner_color_response_record(runtime.scanner),
        },
    )
    output.metadata["optical_capture"] = spectral_capture_record(runtime.scanner)
    output.metadata["scanner_light_exposure"] = scanner_light_exposure_record(runtime.scanner)
    _copy_frame_identity(medium, output.metadata)
    return output


