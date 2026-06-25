import run_darkroom_gui as gui


def test_gui_default_preset_lists_hide_internal_presets():
    film_names = gui.film_preset_names()
    develop_names = gui.develop_preset_names()
    scanner_names = gui.scanner_preset_names()

    assert "diagnostic_develop_sensitive" not in film_names
    assert "accident_kelp_light_leak" not in develop_names
    assert "diagnostic_flat_scan" not in scanner_names

    for name in film_names + develop_names + scanner_names:
        assert not gui.is_internal_preset_name(name)

