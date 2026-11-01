"""Display-only checks: no processing, benchmarks, or image hashes."""
import os
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def isolated_appearance(tmp_path, monkeypatch):
    from film_foundry.ui import i18n, theme
    from film_foundry.ui.preferences import _read
    previous_language=i18n.current_language()
    previous_theme=theme.current_theme()
    monkeypatch.setenv("FILM_FOUNDRY_UI_SETTINGS",str(tmp_path/"ui.json"))
    _read.cache_clear()
    i18n.set_language("zh_CN")
    theme.apply_theme("dark")
    yield
    i18n.set_language(previous_language)
    theme.apply_theme(previous_theme)
    _read.cache_clear()


def test_preferences_restart_and_explicit_language_override(tmp_path):
    from film_foundry.ui.preferences import save_preferences,load_preferences
    save_preferences(language="en_US",theme="ivory")
    assert load_preferences() == {"language":"en_US","theme":"ivory"}
    env=dict(os.environ);env.pop("FILM_FOUNDRY_LANG",None)
    code="from film_foundry.ui.i18n import current_language; from film_foundry.ui.qt import _application; from film_foundry.ui.theme import current_theme; _application(); print(current_language(), current_theme())"
    process=subprocess.run([sys.executable,"-c",code],env=env,capture_output=True,text=True,check=True)
    assert process.stdout.strip() == "en_US ivory"
    env["FILM_FOUNDRY_LANG"]="zh_CN"
    process=subprocess.run([sys.executable,"-c",code],env=env,capture_output=True,text=True,check=True)
    assert process.stdout.strip() == "zh_CN ivory"
    assert set(json.loads((tmp_path/"ui.json").read_text(encoding="utf-8"))) == {"language","theme"}


@pytest.mark.parametrize("value",["invalid JSON",'{"language":"unknown","theme":"white"}',"[]"])
def test_invalid_preferences_fall_back_to_readable_defaults(tmp_path,value):
    from film_foundry.ui.preferences import load_preferences
    (tmp_path/"ui.json").write_text(value,encoding="utf-8")
    assert load_preferences() == {"language":"zh_CN","theme":"dark"}


def test_theme_and_language_switch_preserve_config_curves_preview_and_open_drafts():
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui import theme
    from film_foundry.ui.qt import QtWidgets
    from film_foundry.ui.i18n import language_label
    from film_foundry.ui.preferences import load_preferences
    panel=DesktopPanel()
    try:
        panel.curve_editors["tone_curve"].add_point(.3,.6)
        panel.curve_editors["device_response_rgb"].add_point(.4,.55)
        panel.curve_editors["device_response_rgb"].enabled.setChecked(False)
        point_draft=[list(points) for points in panel.curve_editors["device_response_rgb"].points]
        panel.exposure_ev.set(3.8)
        before=asdict(panel._config())
        image=np.full((12,18,3),.25,np.float32)
        panel._show_preview("sample",image,image,"Source","Result")
        panel._preview_timer.stop();panel._preview_pending=False
        image_key=panel.result_view.source.cacheKey()
        panel._open_editor("film_foundry.tools.run_film_material_editor")
        editor=panel._editor_windows["film"]
        editor.preset_name.set("unsaved_name")
        editor_config=asdict(editor._config_from_controls())
        for key in ("ivory","dark","ivory"):
            panel.theme_display.set(next(label for label,name in panel.theme_keys.items() if name==key))
            panel._change_theme();QtWidgets.QApplication.processEvents()
            assert theme.current_theme() == key
            assert asdict(panel._config()) == before
            assert panel.result_view.source.cacheKey() == image_key
            assert asdict(editor._config_from_controls()) == editor_config
        panel.language.set(language_label("en_US"));panel._change_language()
        assert panel.navigation.item(0).text() == "Task & output"
        assert panel.theme_display.get() == "Light"
        assert panel.scanner_preset_source.get() == "Built-in preset"
        assert asdict(panel._config()) == before
        assert panel.result_view.source.cacheKey() == image_key
        assert panel.curve_editors["device_response_rgb"].points == point_draft
        assert editor.root.winfo_exists()
        assert editor.preset_name.get() == "unsaved_name"
        assert asdict(editor._config_from_controls()) == editor_config
        assert load_preferences() == {"language":"en_US","theme":"ivory"}
    finally:panel.root.destroy()


def test_new_and_existing_editor_windows_follow_palette_without_rebuilding():
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.theme import apply_theme,THEMES
    from film_foundry.ui.qt import QtWidgets,QtGui
    panel=DesktopPanel()
    try:
        for module in ("run_develop_process_editor","run_scanner_render_editor"):
            panel._open_editor("film_foundry.tools."+module)
        before={key:asdict(editor._config_from_controls()) for key,editor in panel._editor_windows.items()}
        apply_theme("ivory");QtWidgets.QApplication.processEvents()
        assert panel._editor_windows["scanner"].curve_fields["tone_curve"].editor.canvas.height() >= 180
        for key,editor in panel._editor_windows.items():
            assert editor.root.qt.palette().color(QtGui.QPalette.ColorRole.Window).name() == THEMES["ivory"].background
            assert asdict(editor._config_from_controls()) == before[key]
        panel._open_runtime_settings()
        assert panel._editor_windows["runtime"].root.qt.palette().color(QtGui.QPalette.ColorRole.Window).name() == THEMES["ivory"].background
    finally:panel.root.destroy()
