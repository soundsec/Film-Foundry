import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import colorsys
from dataclasses import asdict
import numpy as np
import pytest
from film_foundry.core.color_mixer import apply_color_mixer
from film_foundry.model.config import DarkroomConfig, ScannerConfig, normalize_color_mixer


def settings(index=0, row=(30, -50, 20)):
    return tuple(row if i==index else (0,0,0) for i in range(8))


def test_identity_neutrals_selection_and_tile_equivalence():
    image=np.array([[[1.,0.,0.],[0.,1.,0.],[.5,.5,.5],[0.,0.,0.],[1.,1.,1.]]],np.float32)
    image.setflags(write=False)
    assert apply_color_mixer(image,None) is image
    assert apply_color_mixer(image,[(0,0,0)]*8) is image
    result=apply_color_mixer(image,settings())
    np.testing.assert_allclose(result[0,0],colorsys.hls_to_rgb(30/360,.55,.5),atol=1e-6)
    np.testing.assert_array_equal(result[0,1:],image[0,1:])
    np.testing.assert_array_equal(np.concatenate([apply_color_mixer(image[:,:2],settings()),apply_color_mixer(image[:,2:],settings())],axis=1),result)


def test_smooth_circular_boundaries_and_valid_range():
    hues=np.array([0,30,60,120,180,240,270,300,360])
    for h in hues:
        rgb=np.array([[colorsys.hls_to_rgb(((h+d)%360)/360,.5,.7) for d in (-.001,0,.001)]],np.float32)
        result=apply_color_mixer(rgb,settings())
        assert np.max(np.abs(np.diff(result,axis=1))) < .0001
        assert np.isfinite(result).all() and result.min()>=0 and result.max()<=1


@pytest.mark.parametrize("value",[[],[(0,0,0)]*7,[(0,0)]*8,[(31,0,0)]*8,[(0,101,0)]*8,[(0,0,float('nan'))]*8])
def test_invalid_settings_rejected(value):
    with pytest.raises(ValueError):normalize_color_mixer(value)


def test_recipe_share_plan_snapshot_and_reference(tmp_path):
    from film_foundry.app.workflow import encode_recipe_share
    from film_foundry.app.config_assets import load_recipe_file
    from film_foundry.core.scan_output import ScanOutputPlan
    from film_foundry.core.scan_runtime import _encode_scan_output
    config=DarkroomConfig();config.scanner.color_mixer=settings();config.scanner.scan_normalize=False
    path=tmp_path/'recipe.ffc2';path.write_text(encode_recipe_share(config),encoding='utf-8')
    assert load_recipe_file(path).scanner.color_mixer==settings()
    assert DarkroomConfig.from_dict(asdict(config)).scanner.color_mixer==settings()
    plan=ScanOutputPlan.prepare(config.scanner)
    image=np.array([[[.8,.1,.05],[.1,.4,.2]]],np.float32);before=image.copy()
    np.testing.assert_allclose(plan.apply_cpu(image),_encode_scan_output(image,config.scanner),atol=0)
    config.scanner.color_mixer=None
    assert plan.mixer==settings()
    np.testing.assert_array_equal(image,before)


def test_editor_load_reset_and_channel_preservation():
    from film_foundry.ui.qt import _application
    from film_foundry.ui.color_mixer import ColorMixerEditor
    _application();editor=ColorMixerEditor()
    try:
        editor.load(settings());editor.channel.setCurrentIndex(5)
        editor.controls[1].setValue(40)
        assert editor.read()[0]==settings()[0] and editor.read()[5][1]==40
        editor.reset();assert editor.read()[5]==(0,0,0)
        editor.reset(True);assert editor.read() is None
    finally:editor.close()


def test_workbench_recipe_and_language_keep_mixer(tmp_path, monkeypatch):
    from film_foundry.ui.preferences import _read
    monkeypatch.setenv("FILM_FOUNDRY_CAMERA_SETTINGS",str(tmp_path/"camera.json"))
    monkeypatch.setenv("FILM_FOUNDRY_UI_SETTINGS",str(tmp_path/"ui.json"));_read.cache_clear()
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    panel=DesktopPanel()
    try:
        config=panel._config();config.scanner.color_mixer=settings()
        panel._apply_loaded_config(config)
        assert panel._config().scanner.color_mixer==settings()
        assert panel._recipe_export_config().scanner.color_mixer==settings()
        panel._change_language()
        assert panel.color_mixer.read()==settings()
        panel.color_mixer.reset(True)
        assert panel._config().scanner.color_mixer is None
    finally:panel.root.destroy()


@pytest.mark.parametrize("tiled", [False, True])
def test_scan_mixer_changes_only_output(tiled):
    from film_foundry.core.engine import develop_negative, scan_medium_output
    config=DarkroomConfig(enable_grain=False,enable_halation=False,enable_mtf=False)
    config.processing.scan_tile_rows=3
    config.processing.scan_tile_threshold_megapixels=.000001 if tiled else 9999
    config.seed_strategy="fixed";config.random_seed=17
    image=np.linspace(.03,.9,12*18*3,dtype=np.float32).reshape(12,18,3)
    medium=develop_negative(image,config);original=medium.optical_density_rgb.copy()
    baseline=scan_medium_output(medium,config,"negative")
    config.scanner.color_mixer=tuple((10,-30,20) for _ in range(8))
    changed=scan_medium_output(medium,config,"negative")
    np.testing.assert_allclose(changed.output_srgb,apply_color_mixer(baseline.output_srgb,config.scanner.color_mixer),atol=1e-6)
    assert not np.array_equal(changed.output_srgb,baseline.output_srgb)
    np.testing.assert_array_equal(original,medium.optical_density_rgb)
