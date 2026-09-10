"""Film Foundry 的 IDE 运行入口。

用法：在 PyCharm、VS Code、Spyder 等 IDE 中打开本文件，修改下面
“用户常用设置”，然后点击 IDE 的 Run/运行按钮。

现在支持三种阶段模式：
- full：输入图片 -> 冲洗负片/反转正片 -> 对应扫描观察 -> 保存最终图
- develop：输入图片 -> 只冲洗并保存介质密度 .npz
- scan：读取已冲洗的 .npz 或透射 raw -> 只测试扫描/观看效果
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from film_foundry.core.engine import process_file
from film_foundry.core.io_utils import (
    SUPPORTED_EXTENSIONS,
    assert_unique_output_stems,
    iter_images,
    output_target_is_file,
    save_image_bundle,
)
from film_foundry.model.config import DarkroomConfig, merge_config_presets
from film_foundry.tools.paths import app_root
from film_foundry.app import workflow as _workflow

PROJECT_ROOT = app_root()


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

# 胶片材料预设。
# 它描述材料本体；scan 模式下不会使用它重新解释已经冲洗好的底片。
FILM_PRESET_NAME = "clear_modern_negative"

# 冲洗流程预设。
# 它描述这一次暗房流程：药水类型、时间、温度、浓度、搅拌、定影/显定一体状态。
DEVELOP_PRESET_NAME = "standard_color_negative"

# 扫描/输出预设只决定底片如何被解释成正像；develop 模式下不会使用它。
SCANNER_PRESET_NAME = "neutral_scan"


# =========================
# 尺寸与速度
# =========================

# full/develop 阶段是否按目标长边缩放。可与 FAST_MODE、QUALITY_MODE 叠加；
# scan 阶段不重新缩放底片。
RUN_AS_PREVIEW = False

# 正式渲染长边。None 表示保留输入图片原始尺寸。
RENDER_LONG_EDGE = None

# 缩放目标长边。只有 RUN_AS_PREVIEW=True 时生效。
PREVIEW_LONG_EDGE = 1600

# 降阶快速模型；不会因 RUN_AS_PREVIEW=True 而关闭。
FAST_MODE = True

# 内部处理质量：draft / standard / high。
# draft 更快；standard 平衡；high 保留更多内部细节。该轴与上面两项独立。
QUALITY_MODE = "standard"
# None follows QUALITY_MODE. Set an explicit pixel edge only for manual tuning.
HALATION_WORK_LONG_EDGE = None
GRAIN_WORK_LONG_EDGE = None


# =========================
# Film / Develop 参数
# 这些会影响“底片如何形成”。如果你已经有 .npz 底片，只跑 scan 模式时，
# 修改这些参数通常不会重新改变底片密度。
# =========================

EXPOSURE_EV = -0.2
NEGATIVE_CONTRAST = 1.05
HALATION_MULTIPLIER = 0.90
HALATION_SENSITIVITY = 0.00
GRAIN_MULTIPLIER = 0.85
GRAIN_SIZE_MULTIPLIER = 1.00
FILM_DYE_SELECTIVITY = 1.00
MATERIAL_DEGRADATION = None
EMULSION_MTF_STRENGTH = 0.25
DIGITAL_ARTIFACT_SUPPRESSION = 0.15
HALATION_EDGE_COMPENSATION = 0.35

DEVELOPER_TYPE = None
FIXER_TYPE = None
FRAME_SIZE = None
DEVELOP_TIME_MIN = None
DEVELOPER_CONCENTRATION = None
AGITATION = None
PROCESS_MODE = None
COMPENSATION = None
PUSH_STOPS = None
TEMPERATURE_C = None
DEVELOPER_EXHAUSTION = None
FIXER_EXHAUSTION = None
SILVER_RETENTION = None
SILVER_PLATING = None
LIGHT_LEAK_STRENGTH = None
CHEMICAL_STAIN = None
UNEVEN_DEVELOPMENT = None
PROCESS_VARIATION = 0.0


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

# develop 阶段额外保存 scanner raw：扫描器看到的 16-bit linear TIFF。
# 边框是未曝光片基区域，方便用户在外部软件里取样去橙罩。
SAVE_SCANNER_RAW = True
SCANNER_RAW_BORDER_PERCENT = 0.04
SCANNER_RAW_BORDER_MIN_PX = 32

# 材料包导出：不改变最终成片，只额外输出透明片基/分色制版素材。
EXPORT_LAYER_PACK = False
EXPORT_TRANSPARENT_PLATE = True
EXPORT_PLATE_SET = False


# =========================
# 输出设置
# =========================

OUTPUT_FORMAT = "jpg"
BIT_DEPTH = 8
QUALITY = 95
ANTI_BANDING_STRENGTH = 0.18


# =========================
# 内部运行逻辑
# =========================

PRESET_DIR = _workflow.PRESET_DIR
USER_PRESET_DIR = _workflow.USER_PRESET_DIR
NEGATIVE_SUFFIX = _workflow.NEGATIVE_SUFFIX
POSITIVE_SUFFIX = _workflow.POSITIVE_SUFFIX
_output_path_for = _workflow.output_path_for
_iter_negative_files = _workflow.iter_developed_medium_files


def _preset_path(value: str | Path, kind: str) -> Path:
    path = Path(value)
    if path.exists():
        return path

    user_path = USER_PRESET_DIR / kind / f"{value}.json"
    if user_path.exists():
        return user_path

    bundled_path = PRESET_DIR / kind / f"{value}.json"
    if bundled_path.exists():
        return bundled_path

    raise FileNotFoundError(f"Preset not found: {value} ({kind})")


FILM_PRESET_PATH: Path | None = None
DEVELOP_PRESET_PATH: Path | None = None
SCANNER_PRESET_PATH: Path | None = None


def _load_config() -> DarkroomConfig:
    mode = str(PIPELINE_MODE).strip().lower()
    film_preset_path = _preset_path(FILM_PRESET_NAME, "film") if mode in {"full", "develop"} else None
    develop_preset_path = _preset_path(DEVELOP_PRESET_NAME, "develop") if mode in {"full", "develop"} else None
    scanner_preset_path = _preset_path(SCANNER_PRESET_NAME, "scanner") if mode in {"full", "scan"} else None
    film_config = DarkroomConfig.from_json(film_preset_path) if film_preset_path is not None else None
    develop_config = DarkroomConfig.from_json(develop_preset_path) if develop_preset_path is not None else None
    scanner_config = DarkroomConfig.from_json(scanner_preset_path) if scanner_preset_path is not None else None
    config = merge_config_presets(film_config, scanner_config, develop_config=develop_config)
    config.random_seed = RANDOM_SEED
    config.seed_strategy = str(SEED_STRATEGY)
    config.fast_mode = bool(FAST_MODE)
    config.processing.execution_mode = "reduced_fast" if FAST_MODE else "quality"
    config.processing.quality_mode = str(QUALITY_MODE)
    config.processing.halation_work_long_edge = HALATION_WORK_LONG_EDGE
    config.processing.grain_work_long_edge = GRAIN_WORK_LONG_EDGE
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
    if MATERIAL_DEGRADATION is not None:
        config.film.material_degradation = float(MATERIAL_DEGRADATION)
    config.look.halation_multiplier = float(HALATION_MULTIPLIER)
    config.look.halation_sensitivity = float(HALATION_SENSITIVITY)
    config.look.grain_multiplier = float(GRAIN_MULTIPLIER)
    config.look.grain_size_multiplier = float(GRAIN_SIZE_MULTIPLIER)
    config.look.emulsion_mtf_strength = float(EMULSION_MTF_STRENGTH)
    config.look.digital_artifact_suppression = float(DIGITAL_ARTIFACT_SUPPRESSION)
    config.look.halation_edge_compensation = float(HALATION_EDGE_COMPENSATION)

    config.scanner.print_color_bias = tuple(float(v) for v in PRINT_COLOR_BIAS)
    config.scanner.print_color_shift = tuple(float(v) for v in PRINT_COLOR_SHIFT)
    config.scanner.highlight_color_bias = tuple(float(v) for v in HIGHLIGHT_COLOR_BIAS)

    if DEVELOPER_TYPE is not None:
        config.chemistry.developer_type = str(DEVELOPER_TYPE)
        config.chemistry.developer_name = str(DEVELOPER_TYPE).replace("_", " ").title()
    if FIXER_TYPE is not None:
        config.chemistry.fixer_type = str(FIXER_TYPE)
        config.chemistry.fixer_name = str(FIXER_TYPE).replace("_", " ").title()
    if FRAME_SIZE is not None:
        config.chemistry.frame_size = str(FRAME_SIZE)
    if DEVELOP_TIME_MIN is not None:
        config.chemistry.time_min = float(DEVELOP_TIME_MIN)
    if DEVELOPER_CONCENTRATION is not None:
        config.chemistry.concentration = float(DEVELOPER_CONCENTRATION)
    if AGITATION is not None:
        config.chemistry.agitation = float(AGITATION)
    if PROCESS_MODE is not None:
        config.chemistry.process_mode = str(PROCESS_MODE)
    if COMPENSATION is not None:
        config.chemistry.compensation = float(COMPENSATION)
    if PUSH_STOPS is not None:
        config.chemistry.push_stops = float(PUSH_STOPS)
    if TEMPERATURE_C is not None:
        config.chemistry.temperature_c = float(TEMPERATURE_C)
    if DEVELOPER_EXHAUSTION is not None:
        config.chemistry.developer_exhaustion = float(DEVELOPER_EXHAUSTION)
    if FIXER_EXHAUSTION is not None:
        config.chemistry.fixer_exhaustion = float(FIXER_EXHAUSTION)
    if SILVER_RETENTION is not None:
        config.chemistry.silver_retention = float(SILVER_RETENTION)
    if SILVER_PLATING is not None:
        config.chemistry.silver_plating = float(SILVER_PLATING)
    if LIGHT_LEAK_STRENGTH is not None:
        config.chemistry.light_leak_strength = float(LIGHT_LEAK_STRENGTH)
    if CHEMICAL_STAIN is not None:
        config.chemistry.chemical_stain = float(CHEMICAL_STAIN)
    if UNEVEN_DEVELOPMENT is not None:
        config.chemistry.uneven_development = float(UNEVEN_DEVELOPMENT)
    config.chemistry.process_variation = float(PROCESS_VARIATION)
    if OUTPUT_FORMAT is not None:
        config.output.format = str(OUTPUT_FORMAT)
    if BIT_DEPTH is not None:
        config.output.bit_depth = int(BIT_DEPTH)
    if QUALITY is not None:
        config.output.quality = int(QUALITY)
    config.output.anti_banding_strength = float(ANTI_BANDING_STRENGTH)
    return config


def _ensure_batch_output_target(items: list[Path], output_root: Path, label: str) -> bool:
    if len(items) > 1 and output_target_is_file(output_root):
        print(f"{label} output is a single file, but {len(items)} input files were found.")
        print(f"Please set it to a folder to avoid overwriting results: {output_root}")
        return False
    try:
        assert_unique_output_stems(items, label)
    except ValueError as exc:
        print(exc)
        return False
    return True


def _print_input_help() -> None:
    supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
    print("没有找到输入图片。")
    print(f"当前 INPUT_PATH: {INPUT_PATH}")
    print("请把图片放进该文件夹，或修改 film_foundry/tools/run_darkroom.py 顶部的 INPUT_PATH。")
    print(f"支持的扩展名: {supported}")


def _run_full(config: DarkroomConfig) -> None:
    input_paths = iter_images(INPUT_PATH)
    if not input_paths:
        _print_input_help()
        return
    if not _ensure_batch_output_target(input_paths, OUTPUT_PATH, "Final image"):
        return
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for input_path in input_paths:
        try:
            output_path = _output_path_for(input_path, OUTPUT_PATH, config.output.format)
            process_file(input_path, output_path, config, preview=RUN_AS_PREVIEW)
            print(f"已保存最终图: {output_path}")
            completed += 1
        except Exception as exc:
            failures.append((input_path, exc))
            print(f"[完整流程失败] {input_path}: {exc}", file=sys.stderr)
    _raise_batch_failures("完整流程", completed, failures)


def _run_develop(config: DarkroomConfig) -> None:
    input_paths = iter_images(INPUT_PATH)
    if not input_paths:
        _print_input_help()
        return
    if not _ensure_batch_output_target(input_paths, NEGATIVE_PATH, "Developed negative"):
        return
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for input_path in input_paths:
        try:
            _medium, negative_path, _paths = _workflow.develop_image_file(
                input_path,
                NEGATIVE_PATH,
                config,
                scaled_override=RUN_AS_PREVIEW,
            )
            print(f"已保存冲洗底片: {negative_path}")
            completed += 1
        except Exception as exc:
            failures.append((input_path, exc))
            print(f"[冲洗失败] {input_path}: {exc}", file=sys.stderr)
    _raise_batch_failures("冲洗", completed, failures)


def _run_scan(config: DarkroomConfig) -> None:
    negative_paths = _iter_negative_files(NEGATIVE_PATH)
    if not negative_paths:
        print("没有找到可扫描的 .npz 冲洗介质或透射 raw。")
        print(f"当前 NEGATIVE_PATH: {NEGATIVE_PATH}")
        print('请先把 PIPELINE_MODE 设为 "develop" 运行一次，或把 NEGATIVE_PATH 指向已有介质文件。')
        return
    if not _ensure_batch_output_target(negative_paths, OUTPUT_PATH, "Scanned image"):
        return
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for negative_path in negative_paths:
        try:
            scanned, scan_source, source_path, _preview = _workflow.observe_medium_file(
                negative_path,
                config,
            )
            output_path = _output_path_for(negative_path, OUTPUT_PATH, config.output.format)
            if SAVE_SIDECAR:
                sidecar = {
                        "kind": "ScannedPositive",
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "negative_path": str(negative_path),
                        "scan_source": scan_source,
                        "scan_source_path": str(source_path),
                        "output_path": str(output_path),
                        "note": "这个结果由已冲洗介质重新观察得到，没有重新运行 film/develop 阶段。",
                        "config": asdict(config),
                    }
            else:
                sidecar = None
            save_image_bundle(
                scanned.output_srgb,
                output_path,
                config.output,
                sidecar,
                protected_paths=(negative_path, source_path),
            )
            print(f"已保存扫描正像: {output_path}")
            completed += 1
        except Exception as exc:
            failures.append((negative_path, exc))
            print(f"[扫描失败] {negative_path}: {exc}", file=sys.stderr)
    _raise_batch_failures("扫描", completed, failures)


def _raise_batch_failures(
    operation: str,
    completed: int,
    failures: list[tuple[Path, Exception]],
) -> None:
    if not failures:
        return
    first_path, first_error = failures[0]
    raise RuntimeError(
        f"{operation}批处理完成 {completed} 个，失败 {len(failures)} 个；"
        f"首个错误：{first_path}: {first_error}"
    )


def main() -> None:
    mode = str(PIPELINE_MODE).strip().lower()
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
