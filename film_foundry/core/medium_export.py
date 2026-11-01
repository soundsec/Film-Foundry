"""已形成介质的落盘。

只负责 NPZ、观察原始文件和附属材料的写出与回滚。
形成顺序仍在 engine。调用方继续从 engine 导入；测试替换 engine 上的同名对象仍然有效。
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import numpy as np

from film_foundry.core.atomic_io import atomic_path_set, atomic_savez, atomic_write_json
from film_foundry.core.color import linear_to_srgb
from film_foundry.core.electronic_negative import (
    save_linear_rgb_tiff,
    scanner_raw_export_border_width,
    scanner_raw_with_clear_border,
    scanner_raw_with_reference_border,
)
from film_foundry.core.io_utils import save_image
from film_foundry.core.preview import negative_visual_preview, optical_density_visual_preview
from film_foundry.core.provenance import (
    apply_scanner_raw_border_watermark,
    payload_with_config,
    provenance_npz_array,
    provenance_payload,
)
from film_foundry.core.scanner import transmission_illuminant_rgb
from film_foundry.contracts.spectral_optics import SPECTRAL_POST_PROCESS_ARRAY_KEY
from film_foundry.core.sidecar import (
    developed_negative_sidecar,
    final_positive_sidecar,
    layer_pack_metadata,
    scanner_raw_sidecar,
)
from film_foundry.core.states import DevelopedNegative, developed_medium_metadata
from film_foundry.model.config import DarkroomConfig


# 介质文件名属于数据内核，engine 再导出同一对象。
NEGATIVE_MATERIAL_DIR_NAME = "negatives"
POSITIVE_MATERIAL_DIR_NAME = "positives"
NEGATIVE_SUFFIX = ".darkroom_negative.npz"
POSITIVE_SUFFIX = ".darkroom_positive.npz"


def _save_debug_outputs(
    output_path: Path,
    image_srgb: np.ndarray,
    stages: dict[str, np.ndarray],
    config: DarkroomConfig,
) -> None:
    from film_foundry.core import engine as _host
    _density_preview = _host._density_preview
    _rgb_density_preview = _host._rgb_density_preview
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
    from film_foundry.core import engine as _host
    _config_for_developed_medium_export = _host._config_for_developed_medium_export
    _validate_develop_runtime = _host._validate_develop_runtime
    _validate_output_runtime = _host._validate_output_runtime
    _validate_developed_medium_state = _host._validate_developed_medium_state
    _validate_scan_runtime = _host._validate_scan_runtime
    capture_optical_density = _host.capture_optical_density
    export_transparent_plate_set = _host.export_transparent_plate_set
    export_layer_pack = _host.export_layer_pack
    export_plate_set = _host.export_plate_set
    _medium_optical_density_rgb = _host._medium_optical_density_rgb
    _clear_base_scanner_sample = _host._clear_base_scanner_sample
    _clear_base_optical_density_rgb = _host._clear_base_optical_density_rgb
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
    if negative.spectral_medium is not None:
        npz_payload["spectral_component_amounts"] = negative.spectral_medium.component_amounts
        if negative.spectral_medium.post_process_density_rgb is not None:
            npz_payload[SPECTRAL_POST_PROCESS_ARRAY_KEY] = negative.spectral_medium.post_process_density_rgb
    if negative.clear_base_optical_density_rgb is not None:
        npz_payload["clear_base_optical_density_rgb"] = np.asarray(
            negative.clear_base_optical_density_rgb,
            dtype=np.float32,
        )
    if config.output.watermark_negative_material:
        npz_payload["film_foundry_provenance"] = provenance_npz_array(payload_with_config(provenance, asdict(config)))
    atomic_savez(
        negative_path,
        compression_level=1,
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
            _medium_optical_density_rgb(negative, config.film, scanner=config.scanner),
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
                    _medium_optical_density_rgb(negative, config.film, scanner=config.scanner),
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
                optical_density_rgb=_medium_optical_density_rgb(negative, config.film, scanner=config.scanner),
                clear_base_optical_density_rgb=_clear_base_optical_density_rgb(config, negative),
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


