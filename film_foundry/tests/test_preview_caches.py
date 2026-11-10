import copy
from dataclasses import asdict
import json
import os
from pathlib import Path
import numpy as np

from film_foundry.model.config import DarkroomConfig


def test_config_and_catalog_caches_invalidate_and_return_independent_drafts(tmp_path, monkeypatch):
    from film_foundry.model import config as model
    from film_foundry.app import workflow
    from film_foundry.app import seasonal_collection as seasons
    path=tmp_path/'material.json'
    path.write_text(json.dumps({'film':{'name':'First'}}),encoding='utf-8')
    calls=[];read=model.strict_json_load
    monkeypatch.setattr(model,'strict_json_load',lambda p:(calls.append(p),read(p))[1])
    first=DarkroomConfig.from_json(path);first.film.name='Draft'
    assert DarkroomConfig.from_json(path).film.name=='First'
    assert workflow.film_preset_record(str(path))['name']=='First'
    assert len(calls)==1
    old=path.stat().st_mtime_ns
    path.write_text(json.dumps({'film':{'name':'After'}}),encoding='utf-8')
    os.utime(path,ns=(old+1000000,old+1000000))
    assert workflow.film_preset_record(str(path))['name']=='After'
    assert len(calls)==2
    monkeypatch.setenv('FILM_FOUNDRY_SEASONAL_STATE',str(tmp_path/'unlock.json'))
    generated=seasons.preset_path('film','season_autumn_negative')
    generated.unlink()
    assert seasons.preset_path('film','season_autumn_negative').is_file()


def test_thumbnail_cache_reuses_file_across_views_and_invalidates_overwrite(tmp_path):
    from film_foundry.ui.qt import QtGui
    from film_foundry.ui.image_cache import ImagePreviewCache
    cache=ImagePreviewCache(max_bytes=30000,max_entries=2)
    calls=[]
    def decode(path,w,h):
        calls.append((w,h));image=QtGui.QImage(w,h,QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor('red' if Path(path).read_text()=='red' else 'blue'));return image
    path=tmp_path/'image';path.write_text('red')
    first=cache.read(path,40,30,decode);first.fill(QtGui.QColor('green'))
    assert cache.read(path.resolve(),40,30,decode).pixelColor(0,0).name()=='#ff0000'
    assert len(calls)==1
    oldkey=cache.key(path,40,30);path.write_text('blue')
    assert cache.read(path,40,30,decode).pixelColor(0,0).name()=='#0000ff'
    assert cache.peek(oldkey) is None
    cache.read(path,41,30,decode);cache.read(path,42,30,decode)
    assert cache.peek(cache.key(path,40,30)) is None


def test_target_preview_matches_pipeline_reuses_formation_and_invalidates_edits(monkeypatch):
    from film_foundry.app import preview_session as preview
    from film_foundry.app.diagnostic_targets import diagnostic_target
    calls=[];develop=preview.develop_negative;scan=preview.scan_medium_output
    monkeypatch.setattr(preview,'develop_negative',lambda *a,**kw:(calls.append('form'),develop(*a,**kw))[1])
    monkeypatch.setattr(preview,'scan_medium_output',lambda *a,**kw:(calls.append('scan'),scan(*a,**kw))[1])
    cfg=DarkroomConfig()
    before=asdict(cfg);session=preview.PreviewSession()
    source,result,hit=session.render_target('color24',cfg,long_edge=96)
    expected_cfg=copy.deepcopy(cfg);expected_cfg.seed_strategy='fixed';expected_cfg.random_seed=13579
    medium=develop(source,expected_cfg,rng=preview.rng_for_develop(Path('diagnostic_color24'),expected_cfg))
    expected=scan(medium,expected_cfg,expected_cfg.scanner.interpretation_mode).output_srgb
    np.testing.assert_allclose(result,expected,atol=1e-6)
    assert not hit and asdict(cfg)==before
    result[:]=0
    _,again,hit=session.render_target('color24',cfg,long_edge=96)
    assert hit and np.any(again) and calls==['form','scan']
    cfg.scanner.scan_saturation=.6
    session.render_target('color24',cfg,long_edge=96)
    assert calls==['form','scan','scan']
    cfg.film.halation_strength+=.03
    session.render_target('color24',cfg,long_edge=96)
    assert calls[-2:]==['form','scan']
    for kind in ('grayscale','edges'):
        target=diagnostic_target(kind,long_edge=96)
        assert target.shape==(64,96,3) and np.isfinite(target).all()
        target[:]=0
        assert np.any(diagnostic_target(kind,long_edge=96))


def test_auto_scan_keeps_user_colour_style_on_negative_positive_and_bw():
    from film_foundry.app.workflow import film_preset_path
    from film_foundry.app.diagnostic_targets import diagnostic_target
    from film_foundry.core.engine import develop_negative,scan_medium_output
    from film_foundry.core.scan_runtime import scan_medium_direct, scan_scanner_raw_direct
    image=diagnostic_target('color24',long_edge=96)
    for name in ('clear_modern_negative','color_reversal_transparency','monochrome_fine_negative'):
        cfg=DarkroomConfig.from_json(film_preset_path(name))
        cfg.random_seed=42;cfg.seed_strategy='fixed'
        medium=develop_negative(image,cfg,rng=np.random.default_rng(42))
        snapshot=medium.optical_density_rgb.copy()
        cfg.scanner.interpretation_mode='auto';cfg.scanner.scan_normalize_mode='rgb'
        cfg.scanner.print_color_bias=(1.08,1,.83)
        cfg.scanner.color_curve_rgb=(((0,0),(.5,.58),(1,1)),((0,0),(.5,.5),(1,1)),((0,0),(.5,.44),(1,1)))
        cfg.scanner.tone_curve=((0,0),(.5,.54),(1,1))
        before=asdict(cfg)
        full=scan_medium_direct(medium,cfg,'auto')
        effective=full.metadata['runtime_config'].scanner
        assert effective.print_color_bias==cfg.scanner.print_color_bias
        assert effective.color_curve_rgb==cfg.scanner.color_curve_rgb
        assert effective.tone_curve==cfg.scanner.tone_curve
        assert effective.scan_normalize_mode=='luma'
        assert effective.invert_transmission==(medium.image_polarity=='negative')
        np.testing.assert_allclose(scan_medium_output(medium,cfg,'auto').output_srgb,full.output_srgb,atol=2e-6)
        assert asdict(cfg)==before
        np.testing.assert_array_equal(medium.optical_density_rgb,snapshot)
        raw=scan_scanner_raw_direct(full.scanner_raw,cfg,'auto',raw_source_kind='light_table_raw_tiff' if medium.image_polarity=='positive' else 'scanner_raw_tiff')
        effective_raw=raw.metadata['runtime_config'].scanner
        assert effective_raw.scan_normalize_mode=='luma'
        assert effective_raw.color_curve_rgb==cfg.scanner.color_curve_rgb
