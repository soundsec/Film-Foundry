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

from half_frame_darkroom.core.media_registry import registered_media_toolkits
from half_frame_darkroom.ui.i18n import current_language, language_from_label, language_label, language_options, set_language, tr
from film_foundry.tools.paths import resource_root


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


PROJECT_ROOT = app_root()
INPUT_DIR = PROJECT_ROOT / "input_images"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
NEGATIVE_DIR = OUTPUT_DIR / "negatives"
USER_PRESET_DIR = PROJECT_ROOT / "user_presets"


def _windows_extended_path(path: Path) -> str:
    """Return a Win32 extended path without changing the represented file."""
    value = str(path.resolve())
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def configure_frozen_tcl_libraries() -> None:
    """Keep bundled Tcl/Tk readable from Windows user special folders.

    Tcl 8.6 can incorrectly normalize paths below folders such as Desktop or
    AppData on some Windows installations.  The Win32 extended-path spelling
    bypasses that lossy normalization.  Limit the workaround to frozen builds;
    source runs continue to use the selected Python environment unchanged.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    frozen_root_value = getattr(sys, "_MEIPASS", "")
    if not frozen_root_value:
        return
    frozen_root = Path(frozen_root_value)
    libraries = (
        ("TCL_LIBRARY", frozen_root / "_tcl_data", "init.tcl"),
        ("TK_LIBRARY", frozen_root / "_tk_data", "tk.tcl"),
    )
    for variable, library, marker in libraries:
        if (library / marker).is_file():
            os.environ[variable] = _windows_extended_path(library)


TOOLS = (
    ("main", "launcher.tool.main.name", "launcher.tool.main.desc", "film_foundry.tools.run_darkroom_gui"),
    ("film", "launcher.tool.film.name", "launcher.tool.film.desc", "film_foundry.tools.run_film_material_editor"),
    ("develop", "launcher.tool.develop.name", "launcher.tool.develop.desc", "film_foundry.tools.run_develop_process_editor"),
    ("scanner", "launcher.tool.scanner.name", "launcher.tool.scanner.desc", "film_foundry.tools.run_scanner_render_editor"),
)

FOLDERS = (
    ("launcher.folder.input", INPUT_DIR),
    ("launcher.folder.output", OUTPUT_DIR),
    ("launcher.folder.negative", NEGATIVE_DIR),
    ("launcher.folder.presets", USER_PRESET_DIR),
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
        self.root.title(tr("launcher.title"))
        self.root.geometry("780x760")
        self.root.minsize(680, 560)
        self.language = tk.StringVar(value=language_label(current_language()))
        self.status = tk.StringVar(value=tr("launcher.status.ready"))
        self._build()

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=16)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)

        title = ttk.Label(shell, text=tr("launcher.brand"), font=("", 14, "bold"))
        title.grid(row=0, column=0, sticky="w", pady=(0, 4))
        subtitle = ttk.Label(shell, text=tr("launcher.subtitle"))
        subtitle.grid(row=1, column=0, sticky="w", pady=(0, 10))

        language_row = ttk.Frame(shell)
        language_row.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(language_row, text=tr("label.language")).pack(side="left", padx=(0, 8))
        self.language.set(language_label(current_language()))
        language_combo = ttk.Combobox(language_row, textvariable=self.language, values=language_options(), width=14, state="readonly")
        language_combo.pack(side="left")
        language_combo.bind("<<ComboboxSelected>>", self._change_language)

        row = 3
        for tool_id, name_key, description_key, module_name in TOOLS:
            card = ttk.Frame(shell, padding=(0, 8))
            card.grid(row=row, column=0, sticky="ew")
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=tr(name_key), font=("", 10, "bold")).grid(row=0, column=0, sticky="w")
            ttk.Label(card, text=tr(description_key), wraplength=500).grid(row=1, column=0, sticky="w", pady=(2, 0))
            ttk.Button(card, text=tr("launcher.open"), command=lambda tool=tool_id, module=module_name: self._open_tool(tool, module)).grid(
                row=0,
                column=1,
                rowspan=2,
                sticky="e",
                padx=(12, 0),
            )
            row += 1

        ttk.Separator(shell).grid(row=row, column=0, sticky="ew", pady=(12, 8))
        row += 1
        toolkit_frame = ttk.LabelFrame(shell, text=tr("launcher.toolkits"), padding=10)
        toolkit_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        toolkit_frame.columnconfigure(1, weight=1)
        for index, toolkit in enumerate(registered_media_toolkits()):
            status = str(toolkit.status)
            status_key = status if status in {"stable", "experimental", "development"} else "development"
            label = tr(f"launcher.{status_key}")
            name_key = f"media.{toolkit.key}.name"
            desc_key = f"media.{toolkit.key}.desc"
            localized_name = tr(name_key) if tr(name_key) != name_key else toolkit.label
            localized_desc = tr(desc_key) if tr(desc_key) != desc_key else toolkit.description
            ttk.Label(toolkit_frame, text=localized_name, font=("", 10, "bold")).grid(row=index, column=0, sticky="w", pady=3)
            ttk.Label(
                toolkit_frame,
                text=localized_desc,
                wraplength=440,
            ).grid(row=index, column=1, sticky="w", padx=(10, 10), pady=3)
            ttk.Label(toolkit_frame, text=label).grid(row=index, column=2, sticky="e", pady=3)
        row += 1

        ttk.Separator(shell).grid(row=row, column=0, sticky="ew", pady=(4, 8))
        row += 1
        folder_frame = ttk.LabelFrame(shell, text=tr("launcher.folders"), padding=10)
        folder_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        for index, (label_key, path) in enumerate(FOLDERS):
            ttk.Button(folder_frame, text=tr(label_key), command=lambda target=path: self._open_folder(target)).grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(0 if index == 0 else 6, 0),
            )
            folder_frame.columnconfigure(index, weight=1)

        row += 1
        ttk.Label(shell, textvariable=self.status, wraplength=660).grid(row=row, column=0, sticky="ew")

    def _change_language(self, _event=None) -> None:
        set_language(language_from_label(self.language.get()))
        self.root.title(tr("launcher.title"))
        for child in self.root.winfo_children():
            child.destroy()
        self.status.set(tr("launcher.status.ready"))
        self._build()

    def _open_tool(self, tool_id: str, module_name: str) -> None:
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--tool", tool_id]
        else:
            command = [sys.executable, "-m", module_name]
        try:
            subprocess.Popen(command, cwd=str(PROJECT_ROOT))
            self.status.set(f"Opened: {module_name}" if current_language() == "en_US" else f"已打开：{module_name}")
        except Exception as exc:
            self.status.set(f"Failed to open: {exc}" if current_language() == "en_US" else f"打开失败：{exc}")

    def _open_folder(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.status.set(f"Opened folder: {path}" if current_language() == "en_US" else f"已打开文件夹：{path}")
        except Exception as exc:
            self.status.set(f"Failed to open folder: {exc}" if current_language() == "en_US" else f"打开文件夹失败：{exc}")

    def run(self) -> None:
        self.root.mainloop()


def run_tool(tool_id: str) -> None:
    if tool_id == "main":
        from film_foundry.tools.run_darkroom_gui import DarkroomPanel

        DarkroomPanel().run()
    elif tool_id == "film":
        from film_foundry.tools.run_film_material_editor import FilmMaterialEditor

        FilmMaterialEditor().run()
    elif tool_id == "develop":
        from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor

        DevelopProcessEditor().run()
    elif tool_id == "scanner":
        from film_foundry.tools.run_scanner_render_editor import ScannerRenderEditor

        ScannerRenderEditor().run()
    elif tool_id == "positive_scanner":
        from film_foundry.tools.run_scanner_render_editor import PositiveScannerEditor

        PositiveScannerEditor().run()
    else:
        FoundryLauncher().run()


def release_self_check() -> None:
    """Validate frozen imports and bundled resources without opening a GUI."""
    # Importing tkinter alone does not initialize Tcl. Create a headless Tcl
    # interpreter so mismatched packaged scripts and DLLs fail this check.
    import tkinter

    loaded_tcl_path = "unknown"
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleFileNameW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        )
        handle = kernel32.GetModuleHandleW("tcl86t.dll")
        if handle:
            buffer = ctypes.create_unicode_buffer(32768)
            if kernel32.GetModuleFileNameW(handle, buffer, len(buffer)):
                loaded_tcl_path = buffer.value
    try:
        tcl = tkinter.Tcl()
    except Exception as exc:
        tcl_library = os.environ.get("TCL_LIBRARY", "")
        tk_library = os.environ.get("TK_LIBRARY", "")
        tcl_init = Path(tcl_library) / "init.tcl" if tcl_library else None
        frozen_root = getattr(sys, "_MEIPASS", "")
        diagnostics = (
            f"Loaded Tcl DLL: {loaded_tcl_path}\n"
            f"TCL_LIBRARY: {tcl_library!r}\n"
            f"TK_LIBRARY: {tk_library!r}\n"
            f"Tcl init exists/readable: "
            f"{bool(tcl_init and tcl_init.is_file())}/"
            f"{bool(tcl_init and os.access(tcl_init, os.R_OK))}\n"
            f"Frozen resource root: {frozen_root!r}\n"
            f"Working directory: {os.getcwd()!r}"
        )
        raise RuntimeError(f"{exc}\n{diagnostics}") from exc
    tcl_patchlevel = str(tcl.call("info", "patchlevel"))
    if not tcl_patchlevel.startswith("8.6."):
        raise RuntimeError(f"Unsupported Tcl runtime: {tcl_patchlevel}")
    # Release the interpreter before importing the GUI tool modules. Frozen
    # windowed executables can otherwise keep the Tcl thread alive at shutdown.
    del tcl

    preset_root = resource_root() / "half_frame_darkroom" / "presets"
    required = (
        preset_root / "film" / "clear_modern_negative.json",
        preset_root / "develop" / "standard_color_negative.json",
        preset_root / "scanner" / "neutral_scan.json",
        preset_root / "film" / "color_reversal_transparency.json",
        preset_root / "develop" / "standard_color_reversal.json",
        preset_root / "scanner" / "positive_transparency_scan.json",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Missing bundled release resources: {missing}")

    # These imports are launched as separate tools in the frozen build and must
    # remain reachable even though the launcher does not instantiate them here.
    from film_foundry.tools.run_darkroom_gui import DarkroomPanel  # noqa: F401
    from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor  # noqa: F401
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor  # noqa: F401
    from film_foundry.tools.run_scanner_render_editor import ScannerRenderEditor  # noqa: F401


def main() -> None:
    configure_frozen_tcl_libraries()
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-check":
        # A frozen windowed executable has no console for a traceback. Always
        # terminate this diagnostic mode explicitly and leave a readable error
        # beside the executable if initialization fails.
        try:
            release_self_check()
        except BaseException:
            import traceback

            error_path = app_root() / "self-check-error.log"
            error_path.write_text(traceback.format_exc(), encoding="utf-8")
            os._exit(1)
        else:
            error_path = app_root() / "self-check-error.log"
            if error_path.is_file():
                error_path.unlink()
            os._exit(0)
    elif len(sys.argv) >= 3 and sys.argv[1] == "--tool":
        run_tool(sys.argv[2])
    else:
        FoundryLauncher().run()


if __name__ == "__main__":
    main()
