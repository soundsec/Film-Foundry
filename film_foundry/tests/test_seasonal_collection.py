import copy
import json
import os
from dataclasses import asdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from film_foundry.app import seasonal_collection as seasons
from film_foundry.app import workflow
from film_foundry.model.config import DarkroomConfig


def test_unlock_is_local_persistent_and_phrase_can_change_without_relocking(tmp_path,monkeypatch):
    monkeypatch.setenv("FILM_FOUNDRY_SEASONAL_STATE",str(tmp_path/"unlock.json"))
    assert not seasons.is_unlocked()
    assert not (tmp_path/"unlock.json").exists()
    identity=seasons.installation_state()["identity"]
    cipher=bytes.fromhex(seasons.puzzle_cipher(identity))
    phrase=bytes(b^ord(identity[i%12]) for i,b in enumerate(cipher)).decode("utf-8")
    assert not seasons.try_unlock("wrong")
    assert not seasons.try_unlock("\n\t")
    assert seasons.try_unlock(phrase)
    assert seasons.installation_state()["identity"] == identity and seasons.is_unlocked()
    changed=copy.deepcopy(seasons.definition());changed["sealed_phrase"]=seasons.seal_phrase("another season")
    monkeypatch.setattr(seasons,"definition",lambda:changed)
    assert not seasons.try_unlock(phrase)
    assert seasons.is_unlocked() and seasons.installation_state()["identity"]==identity
    assert seasons.try_unlock("another season")


def test_hidden_in_all_catalogs_then_official_and_direct_sharing_always_works(tmp_path,monkeypatch):
    monkeypatch.setenv("FILM_FOUNDRY_SEASONAL_STATE",str(tmp_path/"unlock.json"))
    from film_foundry.tools import run_film_material_editor as film_editor,run_scanner_render_editor as scan_editor
    films=seasons.collection_ids("film");scans=seasons.collection_ids("scanner")
    for variants in (True,False):
        assert not films.intersection(workflow.film_preset_names(include_variants=variants))
        assert not scans.intersection(workflow.scanner_preset_names(include_variants=variants))
    assert not films.intersection(film_editor.film_preset_names())
    assert not scans.intersection(scan_editor.scanner_preset_names())
    assert len(films)==len(scans)==8
    assert all(not (workflow.FILM_PRESET_DIR/(key+".json")).exists() for key in films)
    assert all(not (workflow.SCANNER_PRESET_DIR/(key+".json")).exists() for key in scans)
    # Referenced recipes remain readable without a catalog entitlement.
    cfg=DarkroomConfig.from_json(workflow.film_preset_path(next(iter(films))))
    from film_foundry.app.cli import _preset_path
    assert _preset_path(next(iter(films)),"film").is_file()
    assert _preset_path(next(iter(scans)),"scanner").is_file()
    decoded=workflow.config_from_share_code(workflow.encode_recipe_share(cfg))
    assert asdict(decoded.film)==asdict(cfg.film)
    seasons.try_unlock(seasons._phrase_bytes().decode("utf-8"))
    for variants in (True,False):
        assert films.issubset(workflow.film_preset_names(include_variants=variants))
        assert scans.issubset(workflow.scanner_preset_names(include_variants=variants))
    for e in seasons.definition()["entries"]:
        film=workflow.film_preset_record(e["material_id"])
        scanner=workflow.scanner_preset_record(e["scanner_id"])
        assert film["polarity"]==scanner["polarity"]==e["polarity"]
        from film_foundry.ui.workflow_text import preset_source_text
        from film_foundry.ui.i18n import tr
        assert preset_source_text(e["material_id"],workflow.USER_FILM_PRESET_DIR,workflow.FILM_PRESET_DIR)==tr("preset.source.builtin")
        assert seasons.matching_scanner(e["material_id"])==e["scanner_id"]
    assert films.issubset(film_editor.film_preset_names())
    assert scans.issubset(scan_editor.scanner_preset_names())


def test_desktop_unlock_preserves_recipe_and_explicit_selection_pairs_scan(tmp_path,monkeypatch):
    monkeypatch.setenv("FILM_FOUNDRY_SEASONAL_STATE",str(tmp_path/"unlock.json"))
    monkeypatch.setenv("FILM_FOUNDRY_CAMERA_SETTINGS",str(tmp_path/"camera.json"))
    monkeypatch.setenv("FILM_FOUNDRY_UI_SETTINGS",str(tmp_path/"ui.json"))
    from film_foundry.ui.preferences import _read
    _read.cache_clear()
    from film_foundry.tools.run_desktop_gui import DesktopPanel,QtWidgets
    panel=DesktopPanel();panel.auto_preview.set(False)
    try:
        before=asdict(panel._config())
        monkeypatch.setattr(QtWidgets.QInputDialog,"getMultiLineText",lambda *a:(seasons._phrase_bytes().decode(),True))
        panel._enter_share_code()
        assert asdict(panel._config())==before
        key="season_spring_negative"
        label=next(label for label,value in panel._film_display_key_map.items() if value==key)
        panel.film_preset_display.set(label);panel._on_film_preset_display_selected()
        assert panel.scanner_preset.get()==seasons.matching_scanner(key)
        references=panel._session_payload()["presets"]
        assert references["film"]["source"]==references["scanner"]["source"]=="builtin"
        assert references["film"]["name"]==key
        config=panel._config()
        expected=DarkroomConfig.from_json(workflow.scanner_preset_path(seasons.matching_scanner(key)))
        assert config.scanner.color_curve_rgb==expected.scanner.color_curve_rgb
        # Normal FFC2 still imports, with the same public application method.
        shared=DarkroomConfig();shared.look.exposure_ev=.37
        monkeypatch.setattr(QtWidgets.QInputDialog,"getMultiLineText",lambda *a:(workflow.encode_recipe_share(shared),True))
        panel._enter_share_code()
        assert panel._config().look.exposure_ev==.37
    finally:
        panel.root.destroy()
