"""生成 preset 曲线预览图。

这个脚本不处理照片，只把每个 preset 的两条关键曲线画出来：
- H-D 曲线：相对曝光量 -> 底片染料密度
- 扫描映射：raw positive density -> 正像亮度

直接运行即可，输出会保存到 outputs/preset_curves。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from half_frame_darkroom.core.scanner import render_positive_scan
from half_frame_darkroom.core.sensitometry import hd_density_curve
from half_frame_darkroom.model.config import DarkroomConfig, merge_config_presets


PROJECT_ROOT = Path(__file__).resolve().parent
PRESET_DIR = PROJECT_ROOT / "half_frame_darkroom" / "presets"
FILM_PRESET_DIR = PRESET_DIR / "film"
SCANNER_PRESET_DIR = PRESET_DIR / "scanner"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "preset_curves"

WIDTH = 760
HEIGHT = 460
PADDING_LEFT = 64
PADDING_RIGHT = 28
PADDING_TOP = 42
PADDING_BOTTOM = 54
CHANNEL_COLORS = ((220, 70, 70), (70, 170, 85), (70, 110, 220))


def _plot_area() -> tuple[int, int, int, int]:
    return (
        PADDING_LEFT,
        PADDING_TOP,
        WIDTH - PADDING_RIGHT,
        HEIGHT - PADDING_BOTTOM,
    )


def _draw_axes(draw: ImageDraw.ImageDraw, title: str, x_label: str, y_label: str) -> None:
    x0, y0, x1, y1 = _plot_area()
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=(252, 250, 246), outline=(220, 216, 206))
    draw.rectangle((x0, y0, x1, y1), outline=(92, 88, 78), width=1)
    for i in range(1, 5):
        x = x0 + (x1 - x0) * i / 5
        y = y0 + (y1 - y0) * i / 5
        draw.line((x, y0, x, y1), fill=(226, 222, 212))
        draw.line((x0, y, x1, y), fill=(226, 222, 212))
    draw.text((24, 14), title, fill=(38, 35, 30))
    draw.text((x0, HEIGHT - 34), x_label, fill=(70, 66, 58))
    draw.text((16, y0 - 2), y_label, fill=(70, 66, 58))


def _points(x_values: np.ndarray, y_values: np.ndarray, x_min: float, x_max: float, y_min: float, y_max: float) -> list[tuple[int, int]]:
    x0, y0, x1, y1 = _plot_area()
    x = x0 + (np.asarray(x_values) - x_min) / max(x_max - x_min, 1e-6) * (x1 - x0)
    y = y1 - (np.asarray(y_values) - y_min) / max(y_max - y_min, 1e-6) * (y1 - y0)
    pts = np.stack([x, y], axis=-1)
    return [(int(round(px)), int(round(py))) for px, py in pts]


def draw_hd_curve(config: DarkroomConfig, output_path: Path) -> None:
    log_e = np.linspace(-3.0, 1.0, 420, dtype=np.float32)
    exposure = np.repeat((10.0 ** log_e)[:, None], 3, axis=1)
    density = hd_density_curve(exposure, config.film, config.chemistry)
    y_max = max(float(np.max(density)) * 1.08, 0.5)

    image = Image.new("RGB", (WIDTH, HEIGHT), (252, 250, 246))
    draw = ImageDraw.Draw(image)
    _draw_axes(draw, f"{config.film.name} - H-D density", "log10(relative exposure)", "density")
    for channel, color in enumerate(CHANNEL_COLORS):
        draw.line(_points(log_e, density[:, channel], -3.0, 1.0, 0.0, y_max), fill=color, width=3)
    draw.text((PADDING_LEFT, PADDING_TOP + 10), "red / green / blue layer display", fill=(92, 88, 78))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def draw_scan_curve(config: DarkroomConfig, output_path: Path) -> None:
    raw_d = np.linspace(0.0, 2.6, 420, dtype=np.float32)
    positive_raw = np.repeat(raw_d[:, None, None], 3, axis=2)
    mapped = render_positive_scan(
        positive_raw,
        config.scanner,
        print_contrast=config.look.print_contrast,
        print_exposure_ev=config.look.print_exposure_ev,
    )

    image = Image.new("RGB", (WIDTH, HEIGHT), (252, 250, 246))
    draw = ImageDraw.Draw(image)
    _draw_axes(draw, f"{config.film.name} - scan/render", "raw positive density", "positive linear")
    for channel, color in enumerate(CHANNEL_COLORS):
        draw.line(_points(raw_d, mapped[:, 0, channel], 0.0, 2.6, 0.0, 1.0), fill=color, width=3)
    draw.text((PADDING_LEFT, PADDING_TOP + 10), f"mapping={config.scanner.print_mapping_mode}, sat={config.scanner.scan_saturation:.2f}", fill=(92, 88, 78))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def main() -> None:
    film_presets = sorted(FILM_PRESET_DIR.glob("*.json"))
    scanner_presets = sorted(SCANNER_PRESET_DIR.glob("*.json"))
    example_presets = sorted(PRESET_DIR.glob("*.json"))
    if not film_presets and not scanner_presets and not example_presets:
        raise FileNotFoundError(f"No presets found in {PRESET_DIR}")

    for preset in film_presets:
        config = merge_config_presets(DarkroomConfig.from_json(preset), None)
        stem = f"film_{preset.stem}"
        draw_hd_curve(config, OUTPUT_DIR / f"{stem}_hd_curve.png")
        print(f"saved film curve for {preset.stem}")

    neutral_film = DarkroomConfig.from_json(FILM_PRESET_DIR / "clear_modern_negative.json") if (FILM_PRESET_DIR / "clear_modern_negative.json").exists() else DarkroomConfig()
    for preset in scanner_presets:
        config = merge_config_presets(neutral_film, DarkroomConfig.from_json(preset))
        stem = f"scanner_{preset.stem}"
        draw_scan_curve(config, OUTPUT_DIR / f"{stem}_scan_curve.png")
        print(f"saved scanner curve for {preset.stem}")

    for preset in example_presets:
        config = DarkroomConfig.from_json(preset)
        stem = f"example_{preset.stem}"
        draw_hd_curve(config, OUTPUT_DIR / f"{stem}_hd_curve.png")
        draw_scan_curve(config, OUTPUT_DIR / f"{stem}_scan_curve.png")
        print(f"saved example full-config curves for {preset.stem}")
    print(f"done: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
