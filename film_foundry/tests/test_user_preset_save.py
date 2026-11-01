import pytest

from film_foundry.app.workflow import (
    film_preset_payload,
    save_user_film_preset,
    save_user_scanner_preset,
    scanner_preset_payload,
)
from film_foundry.model.config import DarkroomConfig, merge_config_presets


def test_save_user_film_and_scanner_presets_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("film_foundry.app.workflow.USER_FILM_PRESET_DIR", tmp_path / "film")
    monkeypatch.setattr("film_foundry.app.workflow.USER_SCANNER_PRESET_DIR", tmp_path / "scanner")
    config = DarkroomConfig()
    config.film.name = "Pocket Custom"
    config.scanner.remove_base_mask = False
    config.look.print_contrast = 1.15

    film_path = save_user_film_preset("Pocket Custom", config)
    scanner_path = save_user_scanner_preset("neutral look", config)
    assert film_path.parent == tmp_path / "film"
    assert scanner_path.parent == tmp_path / "scanner"
    assert film_path.name == "pocket_custom.json"
    assert scanner_path.name == "neutral_look.json"
    assert set(film_preset_payload(config)) == {"mode", "medium", "film"}
    assert set(scanner_preset_payload(config)) == {"scanner", "look", "enable_subtractive"}

    loaded_film = DarkroomConfig.from_json(film_path)
    loaded_scan = DarkroomConfig.from_json(scanner_path)
    merged = merge_config_presets(loaded_film, loaded_scan)
    assert merged.film.name == "Pocket Custom"
    assert merged.scanner.remove_base_mask is False
    assert merged.look.print_contrast == 1.15


def test_save_user_film_preset_refuses_overwrite_unless_asked(tmp_path, monkeypatch):
    monkeypatch.setattr("film_foundry.app.workflow.USER_FILM_PRESET_DIR", tmp_path / "film")
    config = DarkroomConfig()
    save_user_film_preset("stock", config)
    with pytest.raises(FileExistsError):
        save_user_film_preset("stock", config)
    save_user_film_preset("stock", config, overwrite=True)
    assert (tmp_path / "film" / "stock.json").is_file()


def test_save_user_preset_rejects_empty_name(tmp_path, monkeypatch):
    monkeypatch.setattr("film_foundry.app.workflow.USER_FILM_PRESET_DIR", tmp_path / "film")
    with pytest.raises(ValueError):
        save_user_film_preset("???", DarkroomConfig())
