"""Film Foundry launcher.

Run this file directly in an IDE to open the main workflow, preset editors, or
common working folders.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import ttk


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


PROJECT_ROOT = app_root()
APP_TITLE = "Film Foundry Launcher"
INPUT_DIR = PROJECT_ROOT / "input_images"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
NEGATIVE_DIR = OUTPUT_DIR / "negatives"
USER_PRESET_DIR = PROJECT_ROOT / "user_presets"


TOOLS = (
    (
        "main",
        "主控制台",
        "完整流程 / 只冲洗 / 只扫描。用于调用胶片材料、冲洗流程和扫描 preset。",
        "run_darkroom_gui.py",
    ),
    (
        "film",
        "胶片材料编辑器",
        "编辑 H-D 曲线、片基密度、染料吸收、颗粒基准、halation/MTF。",
        "run_film_material_editor.py",
    ),
    (
        "develop",
        "冲洗流程编辑器",
        "编辑显影液/定影/单浴、时间、温度、浓度、搅拌、疲劳、残银。",
        "run_develop_process_editor.py",
    ),
    (
        "scanner",
        "扫描解释编辑器",
        "编辑去片基、反相/打印曲线、滤色、饱和、高光偏色、黑白点。",
        "run_scanner_render_editor.py",
    ),
)

FOLDERS = (
    ("输入图片", INPUT_DIR),
    ("输出结果", OUTPUT_DIR),
    ("电子负片", NEGATIVE_DIR),
    ("用户预设", USER_PRESET_DIR),
)


def ensure_user_dirs() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    NEGATIVE_DIR.mkdir(parents=True, exist_ok=True)
    (USER_PRESET_DIR / "film").mkdir(parents=True, exist_ok=True)
    (USER_PRESET_DIR / "develop").mkdir(parents=True, exist_ok=True)
    (USER_PRESET_DIR / "scanner").mkdir(parents=True, exist_ok=True)


class FoundryLauncher:
    def __init__(self) -> None:
        ensure_user_dirs()
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("720x560")
        self.root.minsize(600, 460)
        self.status = tk.StringVar(value="选择一个工作台，或打开常用文件夹。")
        self._build()

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=16)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)

        title = ttk.Label(shell, text="Film Foundry / Electronic Negative Factory", font=("", 14, "bold"))
        title.grid(row=0, column=0, sticky="w", pady=(0, 4))
        subtitle = ttk.Label(shell, text="主流程负责调用 preset；外部编辑器负责制造材料、流程和扫描解释。")
        subtitle.grid(row=1, column=0, sticky="w", pady=(0, 14))

        for row, (tool_id, name, description, script_name) in enumerate(TOOLS, start=2):
            card = ttk.Frame(shell, padding=(0, 8))
            card.grid(row=row, column=0, sticky="ew")
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=name, font=("", 10, "bold")).grid(row=0, column=0, sticky="w")
            ttk.Label(card, text=description, wraplength=500).grid(row=1, column=0, sticky="w", pady=(2, 0))
            ttk.Button(card, text="打开", command=lambda tool=tool_id, script=script_name: self._open_tool(tool, script)).grid(
                row=0,
                column=1,
                rowspan=2,
                sticky="e",
                padx=(12, 0),
            )

        ttk.Separator(shell).grid(row=6, column=0, sticky="ew", pady=(12, 8))
        folder_frame = ttk.LabelFrame(shell, text="常用文件夹", padding=10)
        folder_frame.grid(row=7, column=0, sticky="ew", pady=(0, 10))
        for index, (label, path) in enumerate(FOLDERS):
            ttk.Button(folder_frame, text=label, command=lambda target=path: self._open_folder(target)).grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(0 if index == 0 else 6, 0),
            )
            folder_frame.columnconfigure(index, weight=1)

        ttk.Label(shell, textvariable=self.status, wraplength=660).grid(row=8, column=0, sticky="ew")

    def _open_tool(self, tool_id: str, script_name: str) -> None:
        script_path = PROJECT_ROOT / script_name
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--tool", tool_id]
        elif script_path.exists():
            command = [sys.executable, str(script_path)]
        else:
            self.status.set(f"找不到脚本：{script_path}")
            return
        try:
            subprocess.Popen(command, cwd=str(PROJECT_ROOT))
            self.status.set(f"已打开：{script_name}")
        except Exception as exc:
            self.status.set(f"打开失败：{exc}")

    def _open_folder(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.status.set(f"已打开文件夹：{path}")
        except Exception as exc:
            self.status.set(f"打开文件夹失败：{exc}")

    def run(self) -> None:
        self.root.mainloop()


def run_tool(tool_id: str) -> None:
    if tool_id == "main":
        from run_darkroom_gui import DarkroomPanel

        DarkroomPanel().run()
    elif tool_id == "film":
        from run_film_material_editor import FilmMaterialEditor

        FilmMaterialEditor().run()
    elif tool_id == "develop":
        from run_develop_process_editor import DevelopProcessEditor

        DevelopProcessEditor().run()
    elif tool_id == "scanner":
        from run_scanner_render_editor import ScannerRenderEditor

        ScannerRenderEditor().run()
    else:
        FoundryLauncher().run()


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--tool":
        run_tool(sys.argv[2])
    else:
        FoundryLauncher().run()


if __name__ == "__main__":
    main()
