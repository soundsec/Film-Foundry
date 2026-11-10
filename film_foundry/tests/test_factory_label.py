from pathlib import Path
import re
import pytest
from film_foundry.contracts.edge_marks import factory_label_pattern, EdgeMarkPattern
from film_foundry.contracts.exposure_glyphs import ROWS


def test_fixed_glyphs_match_android_source_and_template_roundtrip():
    root=Path(__file__).resolve().parents[2]
    source=(root/'android/interchange/src/main/kotlin/com/filmfoundry/interchange/ExposureGlyphs.kt').read_text(encoding='utf-8')
    android={int(key):tuple(map(int,row.split(','))) for key,row in re.findall(r'(\d+) to intArrayOf\(([\d,]+)\)',source)}
    assert android==ROWS
    a=factory_label_pattern('FF 400',1234);b=factory_label_pattern('ff 400',1234)
    assert a==b==EdgeMarkPattern.decode(a.encode())
    changed=factory_label_pattern('FF 400',1235)
    assert all(a.ink(x,y)==changed.ink(x,y) for y in range(9) for x in range(96))
    assert any(a.ink(x,y)!=changed.ink(x,y) for y in range(9) for x in range(96,128))
    assert not any(a.ink(x,y) for y in range(9) for x in range(86,92))
    for value in ('', 'X'*15, '中文'):
        with pytest.raises(ValueError):factory_label_pattern(value)


def test_editor_generates_and_applies_template_without_changing_imprint():
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from film_foundry.ui.qt import _application
    from film_foundry.ui.edge_mark_editor import EdgeMarkEditor
    from film_foundry.model.config import FilmBorderConfig
    app=_application();config=FilmBorderConfig(imprint_text='UNCHANGED')
    editor=EdgeMarkEditor(config)
    try:
        editor.template_text.setText('FF 400');editor.template_batch.setValue(1234)
        editor.generate_label();editor.apply_to(config)
        assert config.factory_pattern_top==factory_label_pattern('FF 400',1234).encode()
        assert config.imprint_text=='UNCHANGED'
    finally:editor.close()
