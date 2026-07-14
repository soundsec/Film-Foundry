"""Experimental slide / light-table test window for Film Foundry."""

from __future__ import annotations

from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, ttk

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import numpy as np
from PIL import Image, ImageTk

from half_frame_darkroom.core.atomic_io import atomic_path_set
from half_frame_darkroom.core.engine import develop_negative, save_developed_medium_materials, scan_negative
from half_frame_darkroom.core.io_utils import assert_unique_output_stems, load_image, save_image
from half_frame_darkroom.core.preview import resize_to_long_edge
from half_frame_darkroom.model.config import DarkroomConfig, merge_config_presets
from film_foundry.tools.paths import app_root, resource_root


PROJECT_ROOT = app_root()
RESOURCE_ROOT = resource_root()
PRESET_DIR = RESOURCE_ROOT / "half_frame_darkroom" / "presets"
FILM_PRESET = PRESET_DIR / "film" / "experimental_slide_transparency.json"
SCANNER_PRESET = PRESET_DIR / "scanner" / "experimental_light_table_scan.json"
DEFAULT_INPUT_DIR = PROJECT_ROOT / "input_images"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "slide_tests"
APP_TITLE = "Film Foundry - Slide Light Table Tester"
IMAGE_PATTERNS = ("*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff", "*.webp", "*.bmp")


def _display_image(image: np.ndarray, max_size: tuple[int, int] = (420, 320)) -> ImageTk.PhotoImage:
    array = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
    pil = Image.fromarray((array * 255.0 + 0.5).astype(np.uint8), mode="RGB")
    pil.thumbnail(max_size, Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(pil)


def _tone_warning(image: np.ndarray, label: str) -> str:
    array = np.asarray(image, dtype=np.float32)
    near_black = float(np.mean(array <= 0.003) * 100.0)
    near_white = float(np.mean(array >= 0.997) * 100.0)
    return f"{label} 黑 {near_black:.1f}% / 白 {near_white:.1f}%"


class SlideLightTableTester:
    def __init__(self) -> None:
        DEFAULT_INPUT_DIR.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1180x800")
        self.root.minsize(980, 660)

        self.input_path = tk.StringVar(value=str(DEFAULT_INPUT_DIR))
        self.output_dir = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.light_table_ev = tk.DoubleVar(value=0.0)
        self.light_table_temperature_k = tk.DoubleVar(value=5400.0)
        self.color_control_strength = tk.DoubleVar(value=0.25)
        self.projection_white_softness = tk.DoubleVar(value=0.22)
        self.projection_black_adaptation = tk.DoubleVar(value=0.10)
        self.positive_midtone_density = tk.DoubleVar(value=0.20)
        self.positive_shadow_toe = tk.DoubleVar(value=0.24)
        self.positive_shadow_toe_width = tk.DoubleVar(value=0.26)
        self.positive_highlight_shoulder = tk.DoubleVar(value=0.42)
        self.positive_highlight_shoulder_width = tk.DoubleVar(value=0.24)
        self.print_exposure_ev = tk.DoubleVar(value=0.0)
        self.print_contrast = tk.DoubleVar(value=1.04)
        self.status = tk.StringVar(value="选择图片后预览。灯台 EV / 色温作用在正片透射前；扫描 EV 是后段解释。")
        self.input_photo: ImageTk.PhotoImage | None = None
        self.raw_photo: ImageTk.PhotoImage | None = None
        self.result_photo: ImageTk.PhotoImage | None = None
        self.last_result: np.ndarray | None = None

        self._build()

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)

        header = ttk.LabelFrame(shell, text="正片灯台测试 / Slide Light Table Test", padding=10)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="实验材料").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Label(header, text=f"{FILM_PRESET.name} + {SCANNER_PRESET.name}").grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(header, text="输入图片").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(header, textvariable=self.input_path).grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Button(header, text="选文件", command=self._choose_file).grid(row=1, column=2, padx=(0, 6))
        ttk.Button(header, text="选文件夹", command=self._choose_folder).grid(row=1, column=3)
        ttk.Label(header, text="输出文件夹").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(header, textvariable=self.output_dir).grid(row=2, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Button(header, text="选择", command=self._choose_output_dir).grid(row=2, column=2, columnspan=2, sticky="ew")

        controls = ttk.LabelFrame(shell, text="正片材料、灯台与扫描解释", padding=10)
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)
        self._section_label(controls, "材料侧密度 / Material Density", 0)
        self._slider(controls, "中间调密度", self.positive_midtone_density, 0.0, 1.0, 1, 0)
        self._slider(controls, "暗部 toe", self.positive_shadow_toe, 0.0, 1.0, 1, 2)
        self._slider(controls, "暗部 toe 宽度", self.positive_shadow_toe_width, 0.02, 0.80, 2, 0)
        self._slider(controls, "高光肩部", self.positive_highlight_shoulder, 0.0, 1.0, 2, 2)
        self._slider(controls, "高光肩部宽度", self.positive_highlight_shoulder_width, 0.02, 0.80, 3, 0)
        self._section_label(controls, "灯台 / Light Table", 4)
        self._slider(controls, "灯台亮度 EV", self.light_table_ev, -3.0, 3.0, 5, 0)
        self._slider(controls, "灯台色温 K", self.light_table_temperature_k, 2800.0, 9000.0, 5, 2)
        self._slider(controls, "正片滤色控制强度", self.color_control_strength, 0.0, 1.0, 6, 0)
        self._slider(controls, "扫描/解释 EV", self.print_exposure_ev, -2.0, 2.0, 6, 2)
        self._slider(controls, "扫描/解释反差", self.print_contrast, 0.60, 1.80, 7, 0)
        self._slider(controls, "白点软滚降", self.projection_white_softness, 0.0, 0.75, 7, 2)
        self._slider(controls, "黑位适应", self.projection_black_adaptation, 0.0, 0.75, 8, 0)

        actions = ttk.Frame(shell)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="预览", command=self._preview).pack(side="left")
        ttk.Button(actions, text="保存结果", command=self._save_result).pack(side="left", padx=(8, 0))
        ttk.Label(actions, textvariable=self.status).pack(side="left", padx=(12, 0), fill="x", expand=True)

        preview = ttk.Frame(shell)
        preview.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        preview.columnconfigure(0, weight=1)
        preview.columnconfigure(1, weight=1)
        preview.columnconfigure(2, weight=1)
        preview.rowconfigure(1, weight=1)
        ttk.Label(preview, text="输入").grid(row=0, column=0, sticky="w")
        ttk.Label(preview, text="灯台 raw / 电子正片透射").grid(row=0, column=1, sticky="w")
        ttk.Label(preview, text="正片灯台结果").grid(row=0, column=2, sticky="w")
        self.input_label = ttk.Label(preview, anchor="center")
        self.input_label.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.raw_label = ttk.Label(preview, anchor="center")
        self.raw_label.grid(row=1, column=1, sticky="nsew", padx=6)
        self.result_label = ttk.Label(preview, anchor="center")
        self.result_label.grid(row=1, column=2, sticky="nsew", padx=(6, 0))

    def _section_label(self, parent: ttk.Frame, text: str, row: int) -> None:
        ttk.Label(parent, text=text, font=("", 10, "bold")).grid(row=row, column=0, columnspan=5, sticky="w", pady=(6, 3))

    def _slider(self, parent: ttk.Frame, label: str, var: tk.DoubleVar, min_value: float, max_value: float, row: int, column: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", pady=4)
        ttk.Scale(parent, from_=min_value, to=max_value, variable=var).grid(row=row, column=column + 1, sticky="ew", padx=(8, 6), pady=4)
        text = tk.StringVar(value=f"{var.get():.3f}")
        var.trace_add("write", lambda *_: text.set(f"{var.get():.3f}"))
        ttk.Label(parent, textvariable=text, width=8).grid(row=row, column=column + 2, sticky="e", pady=4)

    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=str(DEFAULT_INPUT_DIR),
            filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.webp *.bmp"), ("All files", "*.*")],
        )
        if path:
            self.input_path.set(path)

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(initialdir=str(DEFAULT_INPUT_DIR))
        if path:
            self.input_path.set(path)

    def _choose_output_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=str(DEFAULT_OUTPUT_DIR))
        if path:
            self.output_dir.set(path)

    def _first_input_image(self) -> Path:
        images = self._input_images()
        if not images:
            raise FileNotFoundError(f"没有找到输入图片：{Path(self.input_path.get())}")
        return images[0]

    def _input_images(self) -> list[Path]:
        path = Path(self.input_path.get())
        if path.is_file():
            return [path]
        images: list[Path] = []
        for suffix in IMAGE_PATTERNS:
            images.extend(sorted(path.glob(suffix)))
        return sorted(set(images))

    def _config(self) -> DarkroomConfig:
        config = merge_config_presets(
            DarkroomConfig.from_json(FILM_PRESET),
            DarkroomConfig.from_json(SCANNER_PRESET),
            None,
        )
        config.scanner.light_table_ev = float(self.light_table_ev.get())
        config.scanner.light_table_temperature_k = float(self.light_table_temperature_k.get())
        config.scanner.positive_scan_color_control_strength = float(self.color_control_strength.get())
        config.scanner.projection_white_softness = float(self.projection_white_softness.get())
        config.scanner.projection_black_adaptation = float(self.projection_black_adaptation.get())
        config.film.positive_midtone_density = float(self.positive_midtone_density.get())
        config.film.positive_shadow_toe = float(self.positive_shadow_toe.get())
        config.film.positive_shadow_toe_width = float(self.positive_shadow_toe_width.get())
        config.film.positive_highlight_shoulder = float(self.positive_highlight_shoulder.get())
        config.film.positive_highlight_shoulder_width = float(self.positive_highlight_shoulder_width.get())
        config.look.print_exposure_ev = float(self.print_exposure_ev.get())
        config.look.print_contrast = float(self.print_contrast.get())
        config.output.format = "png"
        config.output.bit_depth = 8
        config.output.watermark_metadata = False
        config.output.render_long_edge = 1600
        return config

    def _render_path(self, input_path: Path) -> tuple[Path, np.ndarray, np.ndarray, np.ndarray, object, DarkroomConfig]:
        image = load_image(input_path)
        preview_input = resize_to_long_edge(image, 900)
        config = self._config()
        work = resize_to_long_edge(image, 1600)
        slide = develop_negative(work, config)
        scanned = scan_negative(slide, config)
        return input_path, preview_input, scanned.scanner_raw, scanned.output_srgb, slide, config

    def _render(self) -> tuple[Path, np.ndarray, np.ndarray, np.ndarray, object, DarkroomConfig]:
        return self._render_path(self._first_input_image())

    def _preview(self) -> None:
        try:
            input_path, input_image, light_table_raw, result, _slide, _config = self._render()
            self.last_result = result
            self.input_photo = _display_image(input_image)
            self.raw_photo = _display_image(light_table_raw)
            self.result_photo = _display_image(result)
            self.input_label.configure(image=self.input_photo)
            self.raw_label.configure(image=self.raw_photo)
            self.result_label.configure(image=self.result_photo)
            self.status.set(
                f"预览完成：{input_path.name}；"
                f"{_tone_warning(light_table_raw, 'raw')}；{_tone_warning(result, 'final')}"
            )
        except Exception as exc:
            self.status.set(f"预览失败：{exc}")

    def _save_result(self) -> None:
        try:
            input_paths = self._input_images()
            if not input_paths:
                raise FileNotFoundError(f"没有找到输入图片：{Path(self.input_path.get())}")
            assert_unique_output_stems(input_paths, "Slide light-table workflow")
            output_dir = Path(self.output_dir.get())
            output_dir.mkdir(parents=True, exist_ok=True)
            last_output_path: Path | None = None
            last_material_path = ""
            for index, input_path in enumerate(input_paths, start=1):
                self.status.set(f"保存中 {index}/{len(input_paths)}：{input_path.name}")
                self.root.update_idletasks()
                _input_path, _input_image, _light_table_raw, result, slide, config = self._render_path(input_path)
                output_path = output_dir / f"{input_path.stem}.slide_light_table.png"
                with atomic_path_set((output_path,)):
                    save_image(result, output_path, config.output)
                    material_paths = save_developed_medium_materials(
                        input_path,
                        output_path,
                        slide,
                        config,
                    )
                last_output_path = output_path
                last_material_path = material_paths.get("positive_path", "")
            if len(input_paths) == 1:
                self.status.set(f"已保存：{last_output_path}；电子正片：{last_material_path}")
            else:
                self.status.set(f"批量保存完成：{len(input_paths)} 张；最后电子正片：{last_material_path}")
        except Exception as exc:
            self.status.set(f"保存失败：{exc}")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    SlideLightTableTester().run()
