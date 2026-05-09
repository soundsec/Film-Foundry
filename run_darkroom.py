"""Film Foundry / Electronic Negative Factory 的 IDE 运行入口。

用法：在 PyCharm、VS Code、Spyder 等 IDE 中打开本文件，修改下面
“用户常用设置”，然后点击 IDE 的 Run/运行按钮。

现在支持三种阶段模式：
- full：输入图片 -> 冲洗底片 -> 扫描正像 -> 保存最终图
- develop：输入图片 -> 只冲洗并保存底片密度 .npz
- scan：读取已冲洗的 .npz 底片 -> 只测试扫描/打印效果
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

import numpy as np

from half_frame_darkroom.core.engine import develop_negative, process_file, scan_negative, scan_scanner_raw, seed_from_path
from half_frame_darkroom.core.electronic_negative import (
    export_layer_pack,
    export_plate_set,
    export_transparent_plate_set,
    load_linear_rgb_tiff,
    save_linear_rgb_tiff,
    scanner_raw_with_clear_border,
    split_scanner_raw_border,
)
from half_frame_darkroom.core.io_utils import SUPPORTED_EXTENSIONS, iter_images, load_image, save_image
from half_frame_darkroom.core.negative_io import load_developed_negative_npz
from half_frame_darkroom.core.preview import negative_visual_preview, resize_to_long_edge
from half_frame_darkroom.core.states import DevelopedNegative
from half_frame_darkroom.model.config import DarkroomConfig, merge_config_presets

PROJECT_ROOT = Path(__file__).resolve().parent


# =========================
# 用户常用设置
# =========================

# 运行阶段：
# "full"    ：正常一键出图，等同以前的用法。
# "develop" ：只冲洗，保存可复用的底片密度 .npz。
# "scan"    ：只扫描，读取已经保存的 .npz，方便反复测试扫描颜色和影调。
PIPELINE_MODE = "full"

# 输入路径：full/develop 模式下可以是一张图片，也可以是图片文件夹。
INPUT_PATH = PROJECT_ROOT / "input_images"

# 输出路径：full/scan 模式保存最终图；develop 模式保存 .npz 底片。
OUTPUT_PATH = PROJECT_ROOT / "outputs"

# 已冲洗底片路径：
# develop 模式：如果是文件夹，底片会保存到这里。
# scan 模式：可以指向一个 .npz 文件，也可以指向装有 .npz 的文件夹。
NEGATIVE_PATH = PROJECT_ROOT / "outputs" / "negatives"

# 胶片预设。
# 胶片/冲洗预设只决定电子负片如何形成；scan 模式下不会使用它。
FILM_PRESET_NAME = "clear_modern_negative"

# 扫描/输出预设只决定底片如何被解释成正像；develop 模式下不会使用它。
SCANNER_PRESET_NAME = "neutral_scan"


# =========================
# 尺寸与速度
# =========================

# full/develop 阶段是否按预览尺寸运行。scan 阶段不重新缩放底片。
RUN_AS_PREVIEW = False

# 正式渲染长边。None 表示保留输入图片原始尺寸。
RENDER_LONG_EDGE = None

# 预览长边。只有 RUN_AS_PREVIEW=True 时生效。
PREVIEW_LONG_EDGE = 1600

# 快速模式会用较低中间尺寸计算光晕/颗粒，适合反复试参数。
FAST_MODE = True


# =========================
# Film / Develop 参数
# 这些会影响“底片如何形成”。如果你已经有 .npz 底片，只跑 scan 模式时，
# 修改这些参数通常不会重新改变底片密度。
# =========================

EXPOSURE_EV = -0.2
NEGATIVE_CONTRAST = 1.05
HALATION_MULTIPLIER = 0.90
GRAIN_MULTIPLIER = 0.85
GRAIN_SIZE_MULTIPLIER = 1.00
FILM_DYE_SELECTIVITY = 1.00
EMULSION_MTF_STRENGTH = 0.25
DIGITAL_ARTIFACT_SUPPRESSION = 0.15
HALATION_EDGE_COMPENSATION = 0.35

PUSH_STOPS = None
TEMPERATURE_C = None
DEVELOPER_EXHAUSTION = None


# =========================
# Scan / Render 参数
# 这些最适合在 scan 模式中反复测试。
# =========================

PRINT_CONTRAST = 1.12
PRINT_EXPOSURE_EV = 0.0
SCAN_SATURATION = 1.00

# 兼容旧变量名：新项目中更推荐使用 FILM_DYE_SELECTIVITY / SCAN_SATURATION。
SATURATION_MULTIPLIER = SCAN_SATURATION

# 扫描仪定黑白点。默认 luma 模式会保留胶片色罩；rgb 模式更像自动白平衡。
SCAN_NORMALIZE = True
SCAN_NORMALIZE_STRENGTH = 0.15
SCAN_NORMALIZE_MODE = "luma"
SCAN_BLACK_PERCENTILE = 0.3
SCAN_WHITE_PERCENTILE = 99.7

# log 域打印/扫描滤色。暖调可提高 R、降低 B。
PRINT_COLOR_SHIFT = (0.06, 0.00, -0.08)

# RGB 乘法增益，默认保持中性；优先用 PRINT_COLOR_SHIFT 调色。
PRINT_COLOR_BIAS = (1.0, 1.0, 1.0)

# 高光偏色。想要“白色显绿”可以轻微提高 G、降低 B。
HIGHLIGHT_COLOR_BIAS = (1.00, 1.04, 0.94)


# =========================
# 随机与模块开关
# =========================

# random：每次运行随机；fixed：完全复现；path：同一输入稳定、不同输入不同。
SEED_STRATEGY = "random"
RANDOM_SEED = None

ENABLE_MTF = True
ENABLE_HALATION = True
ENABLE_GRAIN = True
ENABLE_SUBTRACTIVE = True

# full 模式下可保存中间图；develop/scan 分阶段模式会保存自己的 sidecar。
DEBUG_OUTPUT = False
COMPARISON_GRID = False
SAVE_SIDECAR = True

# develop 阶段额外保存电子负片：扫描器看到的 16-bit linear TIFF。
# 边框是未曝光片基区域，方便用户在外部软件里取样去橙罩。
SAVE_SCANNER_RAW = True
SCANNER_RAW_BORDER_PERCENT = 0.04
SCANNER_RAW_BORDER_MIN_PX = 32

# 材料包导出：不改变最终成片，只额外输出透明片基/分色制版素材。
EXPORT_LAYER_PACK = False
EXPORT_TRANSPARENT_PLATE = True
EXPORT_PLATE_SET = True


# =========================
# 输出设置
# =========================

OUTPUT_FORMAT = "jpg"
BIT_DEPTH = 8
QUALITY = 95


# =========================
# 内部运行逻辑
# =========================

PRESET_DIR = PROJECT_ROOT / "half_frame_darkroom" / "presets"
FILM_PRESET_PATH = PRESET_DIR / "film" / f"{FILM_PRESET_NAME}.json"
SCANNER_PRESET_PATH = PRESET_DIR / "scanner" / f"{SCANNER_PRESET_NAME}.json"
NEGATIVE_SUFFIX = ".darkroom_negative.npz"


def _load_config() -> DarkroomConfig:
    mode = str(PIPELINE_MODE).strip().lower()
    film_config = DarkroomConfig.from_json(FILM_PRESET_PATH) if mode in {"full", "develop"} else None
    scanner_config = DarkroomConfig.from_json(SCANNER_PRESET_PATH) if mode in {"full", "scan"} else None
    config = merge_config_presets(film_config, scanner_config)
    config.random_seed = RANDOM_SEED
    config.seed_strategy = str(SEED_STRATEGY)
    config.fast_mode = bool(FAST_MODE)
    config.look.exposure_ev = float(EXPOSURE_EV)
    config.look.print_contrast = float(PRINT_CONTRAST)
    config.look.print_exposure_ev = float(PRINT_EXPOSURE_EV)
    config.output.render_long_edge = RENDER_LONG_EDGE
    config.output.preview_long_edge = PREVIEW_LONG_EDGE
    config.enable_mtf = bool(ENABLE_MTF)
    config.enable_halation = bool(ENABLE_HALATION)
    config.enable_grain = bool(ENABLE_GRAIN)
    config.enable_subtractive = bool(ENABLE_SUBTRACTIVE)
    config.debug_output = bool(DEBUG_OUTPUT)
    config.comparison_grid = bool(COMPARISON_GRID)
    config.save_sidecar = bool(SAVE_SIDECAR)
    config.output.save_scanner_raw = bool(SAVE_SCANNER_RAW)
    config.output.scanner_raw_border_percent = float(SCANNER_RAW_BORDER_PERCENT)
    config.output.scanner_raw_border_min_px = int(SCANNER_RAW_BORDER_MIN_PX)
    config.output.export_layer_pack = bool(EXPORT_LAYER_PACK)
    config.output.export_transparent_plate = bool(EXPORT_TRANSPARENT_PLATE)
    config.output.export_plate_set = bool(EXPORT_PLATE_SET)

    config.scanner.scan_normalize = bool(SCAN_NORMALIZE)
    config.scanner.scan_normalize_strength = float(SCAN_NORMALIZE_STRENGTH)
    config.scanner.scan_normalize_mode = str(SCAN_NORMALIZE_MODE)
    config.scanner.scan_black_percentile = float(SCAN_BLACK_PERCENTILE)
    config.scanner.scan_white_percentile = float(SCAN_WHITE_PERCENTILE)
    config.scanner.scan_saturation = float(SCAN_SATURATION)

    config.look.negative_contrast = float(NEGATIVE_CONTRAST)
    config.look.saturation_multiplier = float(FILM_DYE_SELECTIVITY)
    config.look.halation_multiplier = float(HALATION_MULTIPLIER)
    config.look.grain_multiplier = float(GRAIN_MULTIPLIER)
    config.look.grain_size_multiplier = float(GRAIN_SIZE_MULTIPLIER)
    config.look.emulsion_mtf_strength = float(EMULSION_MTF_STRENGTH)
    config.look.digital_artifact_suppression = float(DIGITAL_ARTIFACT_SUPPRESSION)
    config.look.halation_edge_compensation = float(HALATION_EDGE_COMPENSATION)

    config.scanner.print_color_bias = tuple(float(v) for v in PRINT_COLOR_BIAS)
    config.scanner.print_color_shift = tuple(float(v) for v in PRINT_COLOR_SHIFT)
    config.scanner.highlight_color_bias = tuple(float(v) for v in HIGHLIGHT_COLOR_BIAS)

    if PUSH_STOPS is not None:
        config.chemistry.push_stops = float(PUSH_STOPS)
    if TEMPERATURE_C is not None:
        config.chemistry.temperature_c = float(TEMPERATURE_C)
    if DEVELOPER_EXHAUSTION is not None:
        config.chemistry.developer_exhaustion = float(DEVELOPER_EXHAUSTION)
    if OUTPUT_FORMAT is not None:
        config.output.format = str(OUTPUT_FORMAT)
    if BIT_DEPTH is not None:
        config.output.bit_depth = int(BIT_DEPTH)
    if QUALITY is not None:
        config.output.quality = int(QUALITY)
    return config


def _scale_dye_selectivity(matrix, selectivity: float):
    selectivity = max(0.0, float(selectivity))
    rows = []
    for row in matrix:
        neutral = sum(float(v) for v in row) / 3.0
        rows.append(tuple(max(0.0, neutral + (float(v) - neutral) * selectivity) for v in row))
    return tuple(rows)


def _output_path_for(input_path: Path, output_root: Path, output_format: str) -> Path:
    if output_root.suffix:
        return output_root
    suffix = "." + output_format.lower().lstrip(".")
    return output_root / f"{input_path.stem}_darkroom{suffix}"


def _negative_path_for(input_path: Path, negative_root: Path) -> Path:
    if negative_root.suffix.lower() == ".npz":
        return negative_root
    return negative_root / f"{input_path.stem}{NEGATIVE_SUFFIX}"


def _scanner_raw_path_for_negative(negative_path: Path) -> Path:
    return negative_path.with_suffix(".scanner_raw.tiff")


def _is_scanner_raw_tiff(path: Path) -> bool:
    return path.suffix.lower() in {".tif", ".tiff"} and ".scanner_raw" in path.stem


def _resolved_seed_for(path: Path, config: DarkroomConfig) -> int | None:
    strategy = str(config.seed_strategy).lower()
    if strategy == "fixed":
        return 0 if config.random_seed is None else int(config.random_seed)
    if strategy == "path":
        return seed_from_path(path, 0 if config.random_seed is None else int(config.random_seed))
    return None


def _rng_for_develop(path: Path, config: DarkroomConfig) -> np.random.Generator:
    seed = _resolved_seed_for(path, config)
    return np.random.default_rng(seed)


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _save_negative(negative: DevelopedNegative, path: Path, input_path: Path, config: DarkroomConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        density_cmy=negative.density_cmy.astype(np.float32),
        density_grain=negative.density_grain.astype(np.float32),
    )
    preview_path = path.with_suffix(".negative_visual.png")
    save_image(negative_visual_preview(negative.density_grain, config.film), preview_path, config.output)
    scanner_raw_path = None
    if config.output.save_scanner_raw:
        scanner_raw = scanner_raw_with_clear_border(
            negative.density_grain,
            config.film,
            config.scanner,
            border_percent=config.output.scanner_raw_border_percent,
            border_min_px=config.output.scanner_raw_border_min_px,
        )
        scanner_raw_path = path.with_suffix(".scanner_raw.tiff")
        save_linear_rgb_tiff(scanner_raw, scanner_raw_path)
        if SAVE_SIDECAR:
            _save_json(
                scanner_raw_path.with_suffix(scanner_raw_path.suffix + ".json"),
                {
                    "kind": "ScannerRawNegative",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "input_path": str(input_path),
                    "negative_path": str(path),
                    "scanner_raw_path": str(scanner_raw_path),
                    "encoding": "16-bit linear RGB TIFF, no sRGB gamma",
                    "border": {
                        "meaning": "unexposed clear film base for mask removal",
                        "percent": config.output.scanner_raw_border_percent,
                        "min_px": config.output.scanner_raw_border_min_px,
                    },
                    "config": asdict(config),
                },
            )
    material_dir = path.with_suffix("")
    material_paths: dict[str, str] = {}
    if config.output.export_layer_pack:
        material_paths.update(
            export_layer_pack(
                negative,
                config.film,
                material_dir.parent / f"{material_dir.name}_layer_pack",
                source_negative_path=path,
                scanner_raw_path=scanner_raw_path,
                orange_preview_path=preview_path,
                metadata={
                    "input_path": str(input_path),
                    "negative_path": str(path),
                    "config": asdict(config),
                },
            )
        )
    else:
        if config.output.export_transparent_plate:
            material_paths.update(
                export_transparent_plate_set(
                    negative.density_grain,
                    config.film,
                    material_dir.parent / f"{material_dir.name}_transparent_plate",
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
                    material_dir.parent / f"{material_dir.name}_plate_set",
                )
            )
    if SAVE_SIDECAR:
        _save_json(
            path.with_suffix(path.suffix + ".json"),
            {
                "kind": "DevelopedNegative",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "input_path": str(input_path),
                "negative_path": str(path),
                "negative_visual_preview": str(preview_path),
                "scanner_raw_path": str(scanner_raw_path) if scanner_raw_path is not None else None,
                "material_exports": material_paths,
                "resolved_seed": _resolved_seed_for(input_path, config),
                "note": "这个 .npz 保存的是已冲洗底片密度。scan 模式会读取它反复测试扫描解释。",
                "config": asdict(config),
            },
        )


def _load_negative(path: Path) -> DevelopedNegative:
    return load_developed_negative_npz(path)


def _scan_from_file(path: Path, config: DarkroomConfig):
    if _is_scanner_raw_tiff(path):
        scanner_raw = load_linear_rgb_tiff(path)
        inner, border_samples = split_scanner_raw_border(
            scanner_raw,
            border_percent=config.output.scanner_raw_border_percent,
            border_min_px=config.output.scanner_raw_border_min_px,
        )
        return scan_scanner_raw(inner, config, base_samples=border_samples, source_path=path), "scanner_raw_tiff", path

    scanner_raw_path = _scanner_raw_path_for_negative(path)
    if scanner_raw_path.exists():
        scanner_raw = load_linear_rgb_tiff(scanner_raw_path)
        inner, border_samples = split_scanner_raw_border(
            scanner_raw,
            border_percent=config.output.scanner_raw_border_percent,
            border_min_px=config.output.scanner_raw_border_min_px,
        )
        return scan_scanner_raw(
            inner,
            config,
            base_samples=border_samples,
            source_path=scanner_raw_path,
        ), "scanner_raw_tiff", scanner_raw_path

    if path.suffix.lower() != ".npz":
        raise ValueError(f"不支持的底片文件：{path}。请选择 .npz 或 .scanner_raw.tiff，不要选择 sidecar .json。")
    negative = _load_negative(path)
    return scan_negative(negative, config), "density_npz", path


def _iter_negative_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".npz" or _is_scanner_raw_tiff(path) else []
    if path.is_dir():
        npz_paths = sorted(path.glob(f"*{NEGATIVE_SUFFIX}"))
        raw_paths = sorted(item for item in path.glob("*.scanner_raw.tif*") if _is_scanner_raw_tiff(item))
        npz_raw_paths = {_scanner_raw_path_for_negative(item).resolve() for item in npz_paths}
        return npz_paths + [item for item in raw_paths if item.resolve() not in npz_raw_paths]
    return []


def _print_input_help() -> None:
    supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
    print("没有找到输入图片。")
    print(f"当前 INPUT_PATH: {INPUT_PATH}")
    print("请把图片放进该文件夹，或修改 run_darkroom.py 顶部的 INPUT_PATH。")
    print(f"支持的扩展名: {supported}")


def _run_full(config: DarkroomConfig) -> None:
    input_paths = iter_images(INPUT_PATH)
    if not input_paths:
        _print_input_help()
        return
    for input_path in input_paths:
        output_path = _output_path_for(input_path, OUTPUT_PATH, config.output.format)
        process_file(input_path, output_path, config, preview=RUN_AS_PREVIEW)
        print(f"已保存最终图: {output_path}")


def _run_develop(config: DarkroomConfig) -> None:
    input_paths = iter_images(INPUT_PATH)
    if not input_paths:
        _print_input_help()
        return
    for input_path in input_paths:
        image = load_image(input_path)
        long_edge = config.output.preview_long_edge if RUN_AS_PREVIEW else config.output.render_long_edge
        image = resize_to_long_edge(image, long_edge)
        negative = develop_negative(image, config, rng=_rng_for_develop(input_path, config))
        negative_path = _negative_path_for(input_path, NEGATIVE_PATH)
        _save_negative(negative, negative_path, input_path, config)
        print(f"已保存冲洗底片: {negative_path}")


def _run_scan(config: DarkroomConfig) -> None:
    negative_paths = _iter_negative_files(NEGATIVE_PATH)
    if not negative_paths:
        print("没有找到可扫描的 .npz 底片文件。")
        print(f"当前 NEGATIVE_PATH: {NEGATIVE_PATH}")
        print('请先把 PIPELINE_MODE 设为 "develop" 运行一次，或把 NEGATIVE_PATH 指向已有 .npz。')
        return
    for negative_path in negative_paths:
        scanned, scan_source, source_path = _scan_from_file(negative_path, config)
        output_path = _output_path_for(negative_path.with_suffix(""), OUTPUT_PATH, config.output.format)
        save_image(scanned.output_srgb, output_path, config.output)
        if SAVE_SIDECAR:
            _save_json(
                output_path.with_suffix(output_path.suffix + ".json"),
                {
                    "kind": "ScannedPositive",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "negative_path": str(negative_path),
                    "scan_source": scan_source,
                    "scan_source_path": str(source_path),
                    "output_path": str(output_path),
                    "note": "这个结果由已冲洗底片 .npz 重新扫描得到，没有重新运行 film/develop 阶段。",
                    "config": asdict(config),
                },
            )
        print(f"已保存扫描正像: {output_path}")


def main() -> None:
    mode = str(PIPELINE_MODE).strip().lower()
    if mode in {"full", "develop"} and not FILM_PRESET_PATH.exists():
        raise FileNotFoundError(f"Film preset not found: {FILM_PRESET_PATH}")
    if mode in {"full", "scan"} and not SCANNER_PRESET_PATH.exists():
        raise FileNotFoundError(f"Scanner preset not found: {SCANNER_PRESET_PATH}")
    config = _load_config()
    if mode == "full":
        _run_full(config)
    elif mode == "develop":
        _run_develop(config)
    elif mode == "scan":
        _run_scan(config)
    else:
        raise ValueError('PIPELINE_MODE 只能是 "full"、"develop" 或 "scan"。')
    print("处理完成。")


if __name__ == "__main__":
    main()
