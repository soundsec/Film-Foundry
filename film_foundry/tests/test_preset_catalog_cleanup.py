"""Catalog visibility must not migrate recipes or reset edited scan settings."""
from dataclasses import asdict
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from film_foundry.app import workflow
from film_foundry.app.config_assets import load_reference_tone
from film_foundry.app.preset_catalog import CORE_DEVELOP_PRESETS, CORE_FILM_PRESETS, CORE_SCANNER_PRESETS
from film_foundry.model.config import DarkroomConfig


def test_core_catalog_keeps_user_variants_and_old_lookup(tmp_path, monkeypatch):
    film_dir, scan_dir = tmp_path / "film", tmp_path / "scanner"
    film_dir.mkdir();scan_dir.mkdir()
    monkeypatch.setattr(workflow, "USER_FILM_PRESET_DIR", film_dir)
    monkeypatch.setattr(workflow, "USER_SCANNER_PRESET_DIR", scan_dir)
    assert set(workflow.film_preset_names(include_variants=False)) == CORE_FILM_PRESETS
    assert set(workflow.scanner_preset_names(include_variants=False)) == CORE_SCANNER_PRESETS
    assert set(workflow.develop_preset_names()) == CORE_DEVELOP_PRESETS
    assert "high_acutance_bw" in workflow.develop_preset_names(include_variants=True)
    assert "accident_kelp_light_leak" not in workflow.develop_preset_names(include_variants=True)
    assert "red_rich_negative" in workflow.film_preset_names()
    assert "reference_soft_tone_scan" in workflow.scanner_preset_names()
    old = DarkroomConfig.from_json(workflow.film_preset_path("red_rich_negative"))
    representative = DarkroomConfig.from_json(workflow.film_preset_path("warm_consumer_negative"))
    assert old.film.hd_gamma != representative.film.hd_gamma
    # A user's same-named variant is still always visible and wins lookup.
    (film_dir / "red_rich_negative.json").write_text(json.dumps({"film":{"name":"My material","contrast":1.7}}),encoding="utf-8")
    (scan_dir / "reference_soft_tone_scan.json").write_text(json.dumps({"scanner":{"tone_curve":[[0,0],[1,.5]]}}),encoding="utf-8")
    assert "red_rich_negative" in workflow.film_preset_names(include_variants=False)
    assert "reference_soft_tone_scan" in workflow.scanner_preset_names(include_variants=False)
    assert workflow.film_preset_path("red_rich_negative") == film_dir / "red_rich_negative.json"
    assert DarkroomConfig.from_json(workflow.film_preset_path("red_rich_negative")).film.contrast == 1.7
    # A built-in reference button must not silently load the user's overwrite.
    assert load_reference_tone("reference_soft_tone_scan")[-1] == (1.,1.)


def test_more_variants_preserve_recipe_and_current_legacy_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("FILM_FOUNDRY_UI_SETTINGS",str(tmp_path/"ui.json"))
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.preferences import load_preferences
    panel=DesktopPanel();panel.auto_preview.set(False)
    try:
        assert not panel.show_preset_variants.get()
        assert "red_rich_negative" not in panel._film_display_key_map.values()
        assert "rich_color_scan" not in panel._scanner_display_key_map.values()
        panel.film_preset.set("red_rich_negative")
        panel.scanner_preset.set("reference_deep_tone_scan")
        panel.develop_preset.set("high_acutance_bw")
        panel.exposure_ev.set(.73)
        panel.curve_editors["color_curve_rgb"].add_point(.37,.42)
        before=asdict(panel._config())
        for visible in (True,False):
            panel.show_preset_variants.set(visible);panel._change_preset_visibility()
            assert asdict(panel._config()) == before
            assert "red_rich_negative" in panel._film_display_key_map.values()
            assert "reference_deep_tone_scan" in panel._scanner_display_key_map.values()
            assert "high_acutance_bw" in panel._develop_display_key_map.values()
        assert load_preferences()["show_preset_variants"] is False
        assert not panel._preview_pending
    finally:
        panel.root.destroy()


@pytest.mark.parametrize("key",["reference_soft_tone_scan","reference_deep_tone_scan"])
def test_photo_tone_button_changes_only_tone_and_keeps_scanner_choice(tmp_path, monkeypatch, key):
    monkeypatch.setenv("FILM_FOUNDRY_UI_SETTINGS",str(tmp_path/"ui.json"))
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    panel=DesktopPanel();panel.auto_preview.set(False)
    try:
        panel.scanner_preset.set("warm_consumer_scan")
        panel.light_table_ev.set(.45)
        panel.curve_editors["color_curve_rgb"].add_point(.3,.4)
        before=asdict(panel._config())
        panel.curve_editors["tone_curve"].reference_buttons[key].click()
        after=asdict(panel._config())
        assert after["scanner"].pop("tone_curve") == load_reference_tone(key)
        before["scanner"].pop("tone_curve")
        assert after == before
        assert panel.scanner_preset.get() == "warm_consumer_scan"
        assert workflow.config_from_share_code(workflow.encode_recipe_share(panel._config())).scanner.tone_curve == load_reference_tone(key)
    finally:
        panel.root.destroy()
