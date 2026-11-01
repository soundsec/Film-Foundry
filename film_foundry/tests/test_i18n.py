from film_foundry.ui.i18n import current_language, language_from_label, language_label, set_language, tr


def test_i18n_defaults_to_chinese(monkeypatch):
    monkeypatch.delenv("FILM_FOUNDRY_LANG", raising=False)
    set_language("zh_CN")

    assert tr("mode.full") == "完整流程"
    assert language_label("zh_CN") == "中文"
    assert tr("label.language") == "语言 / Language"
    assert tr("label.preview_output") == "按边长缩放输出 / 冲洗（缩放快速）"
    assert tr("label.preview_long_edge") == "缩放目标长边 px（0=原图）"
    assert tr("label.fast_mode") == "降阶快速模型"


def test_i18n_can_switch_to_english(monkeypatch):
    monkeypatch.setenv("FILM_FOUNDRY_LANG", "en_US")
    set_language("en_US")

    assert tr("mode.full") == "Full Workflow"
    assert tr("label.language") == "语言 / Language"
    assert tr("label.preview_output") == "Resize output / develop by long edge (scaled fast)"
    assert tr("label.preview_long_edge") == "Target long edge px (0=original)"
    assert tr("label.fast_mode") == "Reduced-order fast model"


def test_i18n_missing_key_returns_key(monkeypatch):
    monkeypatch.setenv("FILM_FOUNDRY_LANG", "en_US")
    set_language("en_US")

    assert tr("missing.example") == "missing.example"


def test_i18n_language_labels_round_trip():
    set_language("zh_CN")

    assert language_label("en_US") == "English"
    assert language_from_label("English") == "en_US"


def test_i18n_folder_and_scanner_raw_labels_use_medium_wording(monkeypatch):
    set_language("zh_CN")
    assert tr("launcher.folder.negative") == "已冲洗介质"
    assert "电子负片" not in tr("label.save_scanner_raw")
    assert "电子负片" not in tr("label.scanner_raw_border")

    set_language("en_US")
    assert tr("launcher.folder.negative") == "Developed media"
    assert "Electronic" not in tr("label.save_scanner_raw")
    assert "Electronic" not in tr("label.scanner_raw_border")
    assert "Electronic Negative" not in tr("app.title")


def test_i18n_film_and_scanner_preset_save_labels():
    previous = current_language()
    try:
        set_language("zh_CN")
        assert tr("button.save_film_preset") == "保存胶卷配置"
        assert tr("button.save_scanner_preset") == "保存扫描器配置"
        set_language("en_US")
        assert tr("button.save_film_preset") == "Save Film Setup"
        assert tr("button.save_scanner_preset") == "Save Scanner Setup"
    finally:
        set_language(previous)


def test_i18n_process_cancel_labels():
    previous = current_language()
    try:
        set_language("zh_CN")
        assert tr("button.cancel") == "取消"
        assert "检查点" in tr("status.cancelling")
        set_language("en_US")
        assert tr("button.cancel") == "Cancel"
        assert "checkpoint" in tr("status.cancelling")
    finally:
        set_language(previous)
