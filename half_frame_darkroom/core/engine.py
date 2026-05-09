"""Single-image Film Foundry processing engine."""

from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path

import numpy as np

from half_frame_darkroom.core.color import linear_to_srgb, luminance, srgb_to_linear
from half_frame_darkroom.core.density_grain import apply_density_grain
from half_frame_darkroom.core.halation import apply_halation
from half_frame_darkroom.core.io_utils import load_image, save_image
from half_frame_darkroom.core.mtf import apply_emulsion_mtf
from half_frame_darkroom.core.preview import negative_visual_preview, resize_to_long_edge
from half_frame_darkroom.core.scanner import (
    balance_negative_base,
    invert_negative_image,
    negative_total_density_rgb,
    normalize_scan_rgb,
    render_negative_image,
    render_positive_scan,
    scan_negative_raw,
    scanner_raw_to_positive_rgb,
)
from half_frame_darkroom.core.sensitometry import exposure_to_density
from half_frame_darkroom.core.states import DevelopedNegative, ScannedPositive
from half_frame_darkroom.core.subtractive import density_to_positive_rgb
from half_frame_darkroom.model.config import DarkroomConfig


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


def _force_bw_negative(config: DarkroomConfig) -> None:
    """黑白负片模式：把三层参数同步成单一银盐密度响应，避免彩色染料色偏。"""
    if str(config.mode).lower() != "bw_negative":
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
    config.scanner.print_reference_density = tuple([float(np.mean(config.scanner.print_reference_density))] * 3)
    config.film.granularity_sigma = tuple([float(np.mean(config.film.granularity_sigma))] * 3)
    config.film.film_base_density_rgb = tuple([float(np.mean(config.film.film_base_density_rgb))] * 3)
    config.scanner.scanner_light_color = (1.0, 1.0, 1.0)
    config.scanner.scanner_response_matrix = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
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


def _runtime_config(config: DarkroomConfig | None, prepared: bool = False) -> DarkroomConfig:
    runtime = copy.deepcopy(config or DarkroomConfig())
    if not prepared:
        _force_bw_negative(runtime)
        _apply_look_strength(runtime)
    return runtime


def develop_negative(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
    *,
    _prepared_config: bool = False,
) -> DevelopedNegative:
    """把输入 sRGB 图像冲洗成 CMY 底片密度状态。"""
    config = _runtime_config(config, prepared=_prepared_config)
    if rng is None:
        rng, _ = _rng_for_input(None, config)

    linear = srgb_to_linear(image_srgb)
    linear = linear * (2.0 ** float(config.look.exposure_ev))
    if str(config.mode).lower() == "bw_negative":
        linear = np.repeat(luminance(linear)[..., None], 3, axis=-1)
    linear_input = np.clip(linear, 0.0, 1.0)

    if config.enable_mtf:
        after_mtf = apply_emulsion_mtf(linear, config.film)
    else:
        after_mtf = linear

    if config.enable_halation:
        after_halation = apply_halation(after_mtf, config.film, fast=config.fast_mode)
    else:
        after_halation = after_mtf

    density_cmy = exposure_to_density(after_halation, config.film, config.chemistry)
    if config.enable_grain:
        density_grain = apply_density_grain(
            density_cmy,
            config.film,
            config.chemistry,
            rng=rng,
            fast=config.fast_mode,
        )
    else:
        density_grain = density_cmy
    if str(config.mode).lower() == "bw_negative":
        density_grain = np.repeat(density_grain.mean(axis=-1, keepdims=True), 3, axis=-1)

    return DevelopedNegative(
        linear_input=linear_input.astype(np.float32),
        after_mtf=np.clip(after_mtf, 0.0, 1.0).astype(np.float32),
        after_halation=np.clip(after_halation, 0.0, 1.0).astype(np.float32),
        density_cmy=density_cmy.astype(np.float32),
        density_grain=density_grain.astype(np.float32),
        metadata={"runtime_config": config, "stage": "developed_negative"},
    )


def scan_negative(
    negative: DevelopedNegative,
    config: DarkroomConfig | None = None,
    *,
    _prepared_config: bool = False,
) -> ScannedPositive:
    """把已冲洗底片解释成可观看正像。"""
    if config is None:
        stored = negative.metadata.get("runtime_config")
        config = copy.deepcopy(stored) if isinstance(stored, DarkroomConfig) else DarkroomConfig()
        prepared = isinstance(stored, DarkroomConfig)
    else:
        stored = negative.metadata.get("runtime_config")
        if isinstance(stored, DarkroomConfig) and not _prepared_config:
            config = _with_scan_interpretation(stored, config)
        prepared = _prepared_config
    auto_bw = _density_is_monochrome(negative.density_grain)
    if auto_bw:
        config.mode = "bw_negative"
    config = _runtime_config(config, prepared=prepared)
    if auto_bw and prepared:
        _force_bw_negative(config)

    positive_no_grain = density_to_positive_rgb(
        negative.density_cmy,
        config.film,
        config.scanner,
        print_contrast=config.look.print_contrast,
        print_exposure_ev=config.look.print_exposure_ev,
    )
    negative_linear = render_negative_image(negative.density_grain, config.film, config.scanner)
    scanner_raw = negative_linear
    negative_total_density = negative_total_density_rgb(negative.density_grain, config.film)
    return _scan_scanner_raw_array(
        scanner_raw,
        config,
        positive_no_grain=np.clip(positive_no_grain, 0.0, 1.0).astype(np.float32),
        negative_total_density=negative_total_density,
        metadata={"runtime_config": config, "stage": "scanned_positive", "scan_source": "density_negative"},
        _prepared_config=True,
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
        },
        _prepared_config=True,
    )


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
    scanner_raw = np.clip(np.asarray(scanner_raw, dtype=np.float32), 1e-6, 1.0)
    if _density_is_monochrome(scanner_raw, tolerance=1e-4):
        config.mode = "bw_negative"
        _force_bw_negative(config)
    negative_linear = scanner_raw
    if negative_total_density is None:
        negative_total_density = (-np.log10(scanner_raw)).astype(np.float32)

    negative_base_balanced = balance_negative_base(
        negative_linear,
        base_percentile=config.scanner.scan_base_percentile,
        base_samples=base_samples,
    )
    positive_raw = invert_negative_image(negative_base_balanced)

    if config.enable_subtractive:
        if str(config.scanner.scan_method).lower() == "legacy_density_mapping":
            positive_linear = scanner_raw_to_positive_rgb(
                scanner_raw,
                config.scanner,
                print_contrast=config.look.print_contrast,
                print_exposure_ev=config.look.print_exposure_ev,
                base_percentile=config.scanner.scan_base_percentile,
                base_samples=base_samples,
            )
        else:
            positive_linear = render_positive_scan(
                positive_raw,
                config.scanner,
                print_contrast=config.look.print_contrast,
                print_exposure_ev=config.look.print_exposure_ev,
            )
    else:
        positive_linear = np.clip(positive_raw / max(float(np.max(positive_raw)), 1e-6), 0.0, 1.0)

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
        scanner_raw=scanner_raw.astype(np.float32),
        negative_total_density=np.asarray(negative_total_density, dtype=np.float32),
        positive_linear=positive_linear,
        output_srgb=output_srgb,
        positive_no_grain=np.clip(positive_no_grain, 0.0, 1.0).astype(np.float32),
        metadata=metadata or {"runtime_config": config, "stage": "scanned_positive"},
    )


def process_array_with_stages(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """处理数组并返回关键中间结果，供 debug 输出使用。"""
    config = _runtime_config(config)
    if rng is None:
        rng, _ = _rng_for_input(None, config)
    negative = develop_negative(image_srgb, config, rng=rng, _prepared_config=True)
    scanned = scan_negative(negative, config, _prepared_config=True)
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
        "10_positive_linear": scanned.positive_linear,
        "11_output_srgb": scanned.output_srgb,
    }
    return scanned.output_srgb, stages


def process_array(
    image_srgb: np.ndarray,
    config: DarkroomConfig | None = None,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """处理一张已经归一化到 [0, 1] 的 sRGB 图像数组。"""
    output, _ = process_array_with_stages(image_srgb, config, rng=rng)
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
    sidecar = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "preview": preview,
        "resolved_seed": resolved_seed,
        "config": asdict(config),
    }
    sidecar_path = output_path.with_suffix(output_path.suffix + ".json")
    with sidecar_path.open("w", encoding="utf-8") as handle:
        json.dump(sidecar, handle, ensure_ascii=False, indent=2)


def process_file(
    input_path: str | Path,
    output_path: str | Path,
    config: DarkroomConfig | None = None,
    preview: bool = False,
) -> Path:
    """读取、处理并保存一张图像文件。preview=True 时才使用 preview_long_edge。"""
    config = config or DarkroomConfig()
    input_path = Path(input_path)
    output_path = Path(output_path)
    image = load_image(input_path)
    long_edge = config.output.preview_long_edge if preview else config.output.render_long_edge
    image = resize_to_long_edge(image, long_edge)
    rng, resolved_seed = _rng_for_input(input_path, config)
    result, stages = process_array_with_stages(image, config, rng=rng)
    save_image(result, output_path, config.output)

    if config.debug_output:
        _save_debug_outputs(output_path, image, stages, config)
    if config.save_sidecar:
        _save_sidecar(input_path, output_path, config, resolved_seed, preview)
    return output_path
