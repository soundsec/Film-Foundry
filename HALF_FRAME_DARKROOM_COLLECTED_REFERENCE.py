"""Film Foundry / Electronic Negative Factory 单文件汇总参考版。

用途
====
这个文件用于发给其他开发者或助手快速理解当前项目。它不是主工程替代品，
而是“项目地图 + 当前接口 + 使用方式 + 设计约束”的汇总说明。

当前项目名
==========
公开名称：
    Film Foundry / Electronic Negative Factory

旧名称：
    Half-Frame Darkroom

兼容说明：
    主实现包名目前仍是 half_frame_darkroom，以免旧脚本、preset、sidecar 断裂。
    新增了 film_foundry 兼容别名包。

依赖项
======
    numpy>=1.24
    opencv-python>=4.8
    Pillow>=10.0

推荐 Windows / Anaconda 运行：
    python run_darkroom_gui.py
    python run_darkroom.py
    python run_foundry_cli.py --help

核心定位
========
Film Foundry 不是传统 LUT 滤镜软件，也不是严格光谱级胶片化学仿真器。
它是一条物理启发式图像材料生成管线：

    普通 sRGB 图像
    -> 近似线性曝光代理
    -> 乳剂 MTF / 数字锐化伪影抑制
    -> PSF halation / 光子散射
    -> RGB 到三层感光曝光
    -> H-D 曲线形成 CMY 染料密度
    -> 密度域颗粒
    -> 电子负片 / 扫描器 raw / 分色制版 / 正像扫描输出

重要边界：
    普通 JPEG/PNG/TIFF 输入通常已经过 ISP、tone mapping、锐化、降噪和压缩。
    sRGB-to-linear 只建立近似线性工作空间，不代表真实场景辐照度。

主工程路径
==========
    half_frame_darkroom/model/config.py
        FilmStockConfig          胶片本体
        ChemistryConfig          显影条件
        ScannerConfig            扫描/打印解释
        LookAdjustConfig         GUI/CLI 微调滑块
        OutputConfig             文件输出与材料导出
        DarkroomConfig           总配置

    half_frame_darkroom/core/color.py
        srgb_to_linear, linear_to_srgb, luminance

    half_frame_darkroom/core/mtf.py
        apply_emulsion_mtf

    half_frame_darkroom/core/halation.py
        soft threshold, PSF halation, high-light energy leakage

    half_frame_darkroom/core/sensitometry.py
        rgb_exposure_to_layer_exposure, hd_density_curve, exposure_to_density

    half_frame_darkroom/core/density_grain.py
        apply_density_grain

    half_frame_darkroom/core/scanner.py
        density -> transmittance -> scanner raw -> base removal -> positive scan

    half_frame_darkroom/core/electronic_negative.py
        scanner_raw.tiff、透明片基、CMY plate、Layer Pack 导出

    half_frame_darkroom/core/engine.py
        develop_negative, scan_negative, scan_scanner_raw, process_file

    run_darkroom.py
        Windows + IDE 直接运行脚本。通过编辑顶部变量控制流程。

    run_darkroom_gui.py
        Tkinter 调参 GUI。支持 full / develop / scan 三种阶段模式。

    run_foundry_cli.py
        命令行友好入口。支持 full / develop / scan 子命令。

核心数据对象
============
DevelopedNegative：
    linear_input
    after_mtf
    after_halation
    density_cmy
    density_grain
    metadata

ScannedPositive：
    negative_linear
    negative_base_balanced
    positive_raw
    scanner_raw
    negative_total_density
    positive_linear
    output_srgb
    positive_no_grain
    metadata

三阶段工作流
============
1. full
    输入图像 -> 冲洗底片 -> 扫描正像 -> 保存最终图

2. develop
    输入图像 -> 保存可复用底片母版和材料：
        xxx.darkroom_negative.npz
        xxx.darkroom_negative.negative_visual.png
        xxx.darkroom_negative.scanner_raw.tiff
        xxx.darkroom_negative.scanner_raw.tiff.json
        可选 transparent plate / plate set / layer pack

3. scan
    读取已冲洗底片重新扫描。优先级：
        如果输入是 .scanner_raw.tiff：
            直接读取电子负片图像，使用边框估计片基色，裁掉边框后扫描。
        如果输入是 .npz 且旁边有同名 .scanner_raw.tiff：
            优先读取同名电子负片 TIFF。
        否则：
            读取 .npz 中 density_grain，重新生成 scanner raw 后扫描。

电子负片材料包
==============
当前已实现“不引入新系统”的材料导出：

透明片基：
    negative_transparent.png
    negative_transparent_16bit.tiff
    density_alpha.png

分色 / 制版母版：
    cyan_plate.png
    magenta_plate.png
    yellow_plate.png
    density_plate.png
    grain_layer.png
    halation_layer.png

Layer Pack：
    electronic_negative.npz
    scanner_raw_linear_16bit.tiff
    negative_visual_orange_base.png
    negative_transparent.png
    negative_transparent_16bit.tiff
    density_alpha.png
    cyan_plate.png
    magenta_plate.png
    yellow_plate.png
    density_plate.png
    grain_layer.png
    halation_layer.png
    sidecar.json

暂未实现，建议后续单独设计：
    Negative Stacking
    Contact Print
    Halftone / Risograph / Manga / Woodcut interpreters

命令行示例
==========
免安装运行：
    python run_foundry_cli.py full input_images outputs --film-preset clear_modern_negative --scanner-preset neutral_scan --preview --fast
    python run_foundry_cli.py develop input_images outputs\negatives --film-preset warm_consumer_negative --layer-pack --seed-strategy path
    python run_foundry_cli.py scan outputs\negatives outputs\rescans --scanner-preset rich_color_scan --print-contrast 1.25

安装后入口：
    film-foundry full input_images outputs
    film-foundry develop input_images outputs\\negatives --layer-pack
    film-foundry scan outputs\\negatives outputs\\rescans

旧式兼容：
    python -m half_frame_darkroom.app.cli input_images outputs
会被自动解释为：
    python -m half_frame_darkroom.app.cli full input_images outputs

配置分层
========
FilmStockConfig：胶片本体
    halation PSF
    emulsion MTF
    H-D density curve
    layer sensitivity matrix
    dye absorption matrix
    film_base_density_rgb
    granularity sigma

ChemistryConfig：显影条件
    push_stops
    temperature_c
    developer_exhaustion

ScannerConfig：扫描/打印解释
    scanner_light_color
    scanner_response_matrix
    scan_method
    scan_base_percentile
    print_reference_density
    print_gamma
    print_mapping_mode
    print_color_shift
    print_color_bias
    highlight_color_bias
    scan_saturation
    scan_normalize
    scan_normalize_strength
    scan_normalize_mode
    scan_black_percentile
    scan_white_percentile

LookAdjustConfig：GUI/CLI 微调
    exposure_ev
    negative_contrast
    print_contrast
    print_exposure_ev
    saturation_multiplier
    halation_multiplier
    grain_multiplier
    grain_size_multiplier
    look_strength
    emulsion_mtf_strength
    digital_artifact_suppression
    halation_edge_compensation

OutputConfig：输出与材料导出
    format
    quality
    bit_depth
    render_long_edge
    preview_long_edge
    save_scanner_raw
    scanner_raw_border_percent
    scanner_raw_border_min_px
    export_layer_pack
    export_transparent_plate
    export_plate_set

核心方程
========
H-D 密度：
    D = Dmin + gamma * (softplus(log10(E) - toe)
                        - softplus(log10(E) - shoulder))

密度域颗粒：
    sigma_D = granularity_sigma * sqrt(max(D - Dmin, 0))

Halation PSF：
    PSF_halation(r) = A exp(-r^2 / 2 sigma^2) + B exp(-r / R)

底片透射：
    D_total = D_base + A_dye * D_cmy
    T_rgb = 10^(-D_total)

扫描：
    scanner_raw = scanner_response(scanner_light * T_rgb)
    base_balanced = scanner_raw / estimated_clear_base
    positive_raw_density = -log10(base_balanced)
    positive_linear = render_positive_scan(positive_raw_density)

实现注意
========
1. 不要把 density debug 图当成真实负片外观。
   density 高 -> debug 显示亮，但真实透射观看应是 T = 10^-D。

2. negative_visual_orange_base 是给人看的负片预览。
   scanner_raw.tiff 是给扫描/外部软件使用的线性电子负片。

3. 透明片基 negative_transparent 的 alpha 表示沉积/遮挡强度。
   它适合导入 Photoshop、Krita、Affinity 等做图层素材。

4. .npz 是最核心的底片密度母版。
   .scanner_raw.tiff 是更接近真实扫描过程的电子负片图像。

5. scan normalize 默认使用 luma 模式，避免 RGB 分通道自动白平衡洗掉胶片偏色。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


PROJECT_NAME = "Film Foundry / Electronic Negative Factory"
LEGACY_PACKAGE = "half_frame_darkroom"
ALIAS_PACKAGE = "film_foundry"


def project_paths(root: str | Path = ".") -> dict[str, str]:
    """返回当前主工程关键路径，方便其他助手快速定位文件。"""
    root = Path(root)
    return {
        "config": str(root / "half_frame_darkroom" / "model" / "config.py"),
        "engine": str(root / "half_frame_darkroom" / "core" / "engine.py"),
        "scanner": str(root / "half_frame_darkroom" / "core" / "scanner.py"),
        "electronic_negative": str(root / "half_frame_darkroom" / "core" / "electronic_negative.py"),
        "ide_script": str(root / "run_darkroom.py"),
        "gui": str(root / "run_darkroom_gui.py"),
        "cli": str(root / "run_foundry_cli.py"),
        "preset_curve_preview": str(root / "run_preset_curve_preview.py"),
        "terminology_guide": str(root / "docs" / "TERMINOLOGY.md"),
        "preset_guide": str(root / "docs" / "PRESET_GUIDE.md"),
        "presets": str(root / "half_frame_darkroom" / "presets"),
        "full_config_examples": str(root / "half_frame_darkroom" / "presets"),
        "film_presets": str(root / "half_frame_darkroom" / "presets" / "film"),
        "scanner_presets": str(root / "half_frame_darkroom" / "presets" / "scanner"),
    }


def command_examples(python_exe: str = "python") -> list[str]:
    """返回 Windows/Anaconda 下最常用的命令行示例。"""
    return [
        f'{python_exe} run_foundry_cli.py full input_images outputs --film-preset clear_modern_negative --scanner-preset neutral_scan --preview --fast',
        f'{python_exe} run_preset_curve_preview.py',
        f'{python_exe} run_foundry_cli.py develop input_images outputs\\negatives --film-preset warm_consumer_negative --layer-pack --seed-strategy path',
        f'{python_exe} run_foundry_cli.py scan outputs\\negatives outputs\\rescans --scanner-preset rich_color_scan --print-contrast 1.25',
    ]


def import_smoke_test() -> dict[str, Any]:
    """轻量导入检查；运行本文件时可确认主工程入口仍可导入。"""
    from half_frame_darkroom.core.engine import develop_negative, process_file, scan_negative, scan_scanner_raw
    from half_frame_darkroom.core.electronic_negative import (
        export_layer_pack,
        export_plate_set,
        export_transparent_plate_set,
        load_linear_rgb_tiff,
        save_linear_rgb_tiff,
    )
    from half_frame_darkroom.model.config import (
        ChemistryConfig,
        DarkroomConfig,
        FilmStockConfig,
        LookAdjustConfig,
        OutputConfig,
        ScannerConfig,
    )

    return {
        "project": PROJECT_NAME,
        "configs": [
            FilmStockConfig.__name__,
            ChemistryConfig.__name__,
            ScannerConfig.__name__,
            LookAdjustConfig.__name__,
            OutputConfig.__name__,
            DarkroomConfig.__name__,
        ],
        "engine_functions": [
            develop_negative.__name__,
            scan_negative.__name__,
            scan_scanner_raw.__name__,
            process_file.__name__,
        ],
        "material_exports": [
            export_transparent_plate_set.__name__,
            export_plate_set.__name__,
            export_layer_pack.__name__,
            save_linear_rgb_tiff.__name__,
            load_linear_rgb_tiff.__name__,
        ],
    }


if __name__ == "__main__":
    import json

    payload = {
        "summary": import_smoke_test(),
        "paths": project_paths(Path(__file__).resolve().parent),
        "commands": command_examples(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
