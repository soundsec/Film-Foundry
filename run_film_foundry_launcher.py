"""Film Foundry launcher.

Run this file directly in an IDE to open the main workflow, preset editors, or
common working folders.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from film_foundry.core.media_registry import registered_media_toolkits
from film_foundry.ui.qt import ui
from film_foundry.ui.i18n import current_language, language_from_label, language_label, language_options, set_language, tr
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


TOOLS = (
    ("main", "launcher.tool.main.name", "launcher.tool.main.desc", "film_foundry.tools.run_desktop_gui"),
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
        self.root = ui.Application()
        self.root.title(tr("launcher.title"))
        self.root.geometry("860x740")
        self.root.minsize(720, 560)
        self.language = ui.TextValue(value=language_label(current_language()))
        self.status = ui.TextValue(value=tr("launcher.status.ready"))
        self._build()

    def _build(self) -> None:
        from PySide6 import QtCore, QtWidgets

        outer = ui.Frame(self.root, padding=0)
        outer.pack(fill="both", expand=True)

        header = ui.Frame(outer, padding=(20, 16, 20, 8))
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)
        ui.Label(header, text=tr("launcher.brand"), font=("", 16, "bold")).grid(row=0, column=0, sticky="w")
        ui.Label(header, text=tr("launcher.subtitle"), wraplength=760).grid(row=1, column=0, sticky="ew", pady=(6, 12))
        language_row = ui.Frame(header)
        language_row.grid(row=2, column=0, sticky="w")
        ui.Label(language_row, text=tr("label.language")).pack(side="left", padx=(0, 8))
        self.language.set(language_label(current_language()))
        language_combo = ui.Choice(language_row, textvariable=self.language, values=language_options(), width=14, state="readonly")
        language_combo.pack(side="left")
        language_combo.bind("<<ComboboxSelected>>", self._change_language)
        from film_foundry.ui.theme import current_theme
        self.theme_display = ui.TextValue(value=tr("theme." + current_theme()))
        self.theme_keys = {tr("theme." + key): key for key in ("dark", "ivory")}
        self.theme_combo = ui.Choice(language_row, textvariable=self.theme_display, values=tuple(self.theme_keys), state="readonly", width=10)
        self.theme_combo.pack(side="left", padx=(12, 0))
        self.theme_combo.bind("<<ComboboxSelected>>", self._change_theme)

        scroll = QtWidgets.QScrollArea(outer._content_widget())
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer._ensure_pack_layout().addWidget(scroll, 1)
        body = ui.Frame(outer, padding=(20, 8, 20, 12))
        scroll.setWidget(body.qt)
        body.qt.show()
        body.columnconfigure(0, weight=1)

        row = 0
        for tool_id, name_key, description_key, module_name in TOOLS:
            card = ui.Frame(body, padding=(14, 12))
            card.qt.setObjectName("launcherCard")
            card.qt.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            card.grid(row=row, column=0, sticky="ew", pady=(0, 8))
            row += 1
            card.columnconfigure(0, weight=1)
            ui.Label(card, text=tr(name_key), font=("", 11, "bold")).grid(row=0, column=0, sticky="w")
            note = ui.Label(card, text=tr(description_key), wraplength=640)
            note.qt.setObjectName("launcherNote")
            note.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
            ui.Button(
                card,
                text=tr("launcher.open"),
                command=lambda tool=tool_id, module=module_name: self._open_tool(tool, module),
            ).grid(row=0, column=1, sticky="e", padx=(16, 0))

        toolkit_frame = ui.Group(body, text=tr("launcher.toolkits"), padding=(12, 10))
        toolkit_frame.grid(row=row, column=0, sticky="ew", pady=(8, 8))
        row += 1
        toolkit_frame.columnconfigure(1, weight=1)
        for index, toolkit in enumerate(registered_media_toolkits()):
            status = str(toolkit.status)
            status_key = status if status in {"stable", "experimental", "development"} else "development"
            label = tr(f"launcher.{status_key}")
            name_key = f"media.{toolkit.key}.name"
            desc_key = f"media.{toolkit.key}.desc"
            localized_name = tr(name_key) if tr(name_key) != name_key else toolkit.label
            localized_desc = tr(desc_key) if tr(desc_key) != desc_key else toolkit.description
            ui.Label(toolkit_frame, text=localized_name, font=("", 10, "bold")).grid(row=index, column=0, sticky="nw", padx=(0, 12), pady=4)
            note = ui.Label(toolkit_frame, text=localized_desc, wraplength=520)
            note.qt.setObjectName("launcherNote")
            note.grid(row=index, column=1, sticky="ew", pady=4)
            status_label = ui.Label(toolkit_frame, text=label, anchor="e")
            status_label.qt.setMinimumWidth(64)
            status_label.grid(row=index, column=2, sticky="e", padx=(12, 0), pady=4)

        folder_frame = ui.Group(body, text=tr("launcher.folders"), padding=(12, 10))
        folder_frame.grid(row=row, column=0, sticky="ew", pady=(4, 0))
        for index, (label_key, path) in enumerate(FOLDERS):
            ui.Button(folder_frame, text=tr(label_key), command=lambda target=path: self._open_folder(target)).grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(0 if index == 0 else 8, 0),
            )
            folder_frame.columnconfigure(index, weight=1)

        footer = ui.Frame(outer, padding=(20, 4, 20, 14))
        footer.pack(fill="x")
        ui.Label(footer, textvariable=self.status, wraplength=700).pack(side="left",fill="x",expand=True)
        from film_foundry.ui.about import show_about
        self.about_button=ui.Button(footer,text="关于" if current_language()=="zh_CN" else "About",command=lambda:show_about(self.root.qt))
        self.about_button.qt.setObjectName("aboutLink")
        self.about_button.pack(side="right")
        self.about_button.qt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,QtWidgets.QSizePolicy.Policy.Fixed)

    def _change_language(self, _event=None) -> None:
        set_language(language_from_label(self.language.get()))
        self.root.title(tr("launcher.title"))
        for child in self.root.winfo_children():
            child.destroy()
        self.status.set(tr("launcher.status.ready"))
        self._build()
        self._save_display_preferences(language=current_language())

    def _change_theme(self, _event=None) -> None:
        from film_foundry.ui.theme import apply_theme, current_theme
        apply_theme(self.theme_keys[self.theme_display.get()])
        self._save_display_preferences(theme=current_theme())

    def _save_display_preferences(self, **values) -> None:
        from film_foundry.ui.preferences import save_preferences
        try:
            save_preferences(**values)
        except OSError as exc:
            self.status.set(f"{tr('status.ui_preferences_failed')}: {exc}")

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
        from film_foundry.tools.run_desktop_gui import DesktopPanel

        DesktopPanel().run()
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
    from PySide6 import QtCore

    qt_version = str(QtCore.qVersion())
    if int(qt_version.split(".", 1)[0]) < 6:
        raise RuntimeError(f"Unsupported Qt runtime: {qt_version}")
    qt_plugins = Path(
        QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.LibraryPath.PluginsPath)
    )
    if os.name == "nt" and not (qt_plugins / "platforms" / "qwindows.dll").is_file():
        raise RuntimeError(f"Qt Windows platform plugin is missing: {qt_plugins}")
    # Creating QApplication is the point at which Qt resolves and loads its
    # platform plugin.  No widget is shown, but a broken qwindows deployment
    # now fails the frozen self-check instead of the user's first launch.
    from film_foundry.ui.qt import _application

    qt_app = _application()
    if not qt_app.platformName():
        raise RuntimeError("Qt application initialized without a platform plugin")

    preset_root = resource_root() / "film_foundry" / "presets"
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

    # Optional catalog data is encoded outside presets, so ordinary preset
    # checks alone cannot detect an incomplete portable/wheel deployment.
    from film_foundry.app.finder_resources import payload
    from film_foundry.model.config import DarkroomConfig
    resources = payload()
    entries = resources["collection"]["entries"]
    if len(entries) != 8:
        raise RuntimeError("Incomplete optional catalog resources")
    for entry in entries:
        DarkroomConfig.from_dict(resources["desktop"]["film"][entry["material_id"]])
        DarkroomConfig.from_dict(resources["desktop"]["scanner"][entry["scanner_id"]])

    # These imports are launched as separate tools in the frozen build and must
    # remain reachable even though the launcher does not instantiate them here.
    from film_foundry.tools.run_desktop_gui import DesktopPanel  # noqa: F401
    from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor  # noqa: F401
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor  # noqa: F401
    from film_foundry.tools.run_scanner_render_editor import ScannerRenderEditor  # noqa: F401

    # Exercise native array/image dependencies as well as Python imports.
    import numpy as np
    from film_foundry.core.engine import process_array
    from film_foundry.model.config import merge_config_presets
    target = np.broadcast_to(np.linspace(0.01, 0.9, 32, dtype=np.float32)[None, :, None], (24, 32, 3)).copy()
    for offset in (0, 3):
        config = merge_config_presets(
            DarkroomConfig.from_json(required[offset]),
            DarkroomConfig.from_json(required[offset + 2]),
            DarkroomConfig.from_json(required[offset + 1]),
        )
        rendered = process_array(target, config, rng=np.random.default_rng(40))
        if rendered.shape != target.shape or not np.isfinite(rendered).all():
            raise RuntimeError("Packaged image workflow failed")


def main() -> None:
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
