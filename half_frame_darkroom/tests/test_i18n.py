from half_frame_darkroom.ui.i18n import tr


def test_i18n_defaults_to_chinese(monkeypatch):
    monkeypatch.delenv("FILM_FOUNDRY_LANG", raising=False)

    assert tr("mode.full") == "完整流程"


def test_i18n_can_switch_to_english(monkeypatch):
    monkeypatch.setenv("FILM_FOUNDRY_LANG", "en_US")

    assert tr("mode.full") == "Full workflow"


def test_i18n_missing_key_returns_key(monkeypatch):
    monkeypatch.setenv("FILM_FOUNDRY_LANG", "en_US")

    assert tr("missing.example") == "missing.example"
