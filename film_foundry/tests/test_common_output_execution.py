import numpy as np
import pytest

from film_foundry.core.execution_topology import execution_topology


@pytest.mark.parametrize('bits', [8, 16])
def test_parallel_quantization_keeps_dither_halos_and_input(monkeypatch, bits):
    from film_foundry.core.io_utils import _quantize_rgb_rows
    from film_foundry.core import material_parallel
    monkeypatch.setattr(material_parallel, 'PARALLEL_MIN_PIXELS', 1)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', 'cpu')
    source = np.random.default_rng(27).uniform(-.03, 1.03, (87, 53, 3)).astype(np.float32)
    source[20:55] = np.linspace(.35, .4, 53, dtype=np.float32)[None, :, None]
    source.setflags(write=False)
    options = dict(bit_depth=bits, tile_rows=13, anti_banding_strength=.75, bgr=bits==16)
    with execution_topology('reference'):
        expected = _quantize_rgb_rows(source, **options)
    np.testing.assert_array_equal(_quantize_rgb_rows(source, workers=4, **options), expected)
    with material_parallel.ordered_results([1], lambda x:x, workers=4) as (_, results):
        list(results)
        np.testing.assert_array_equal(_quantize_rgb_rows(source, workers=4, **options), expected)
    bad = source.copy();bad[-2, 3, 0] = np.nan
    with pytest.raises(ValueError, match='NaN'):
        _quantize_rgb_rows(bad, workers=4, **options)
    assert material_parallel._lease.acquire(blocking=False)
    material_parallel._lease.release()


def test_scanner_raw_fused_write_keeps_mark_and_pixels(tmp_path, monkeypatch):
    from film_foundry.core.electronic_negative import save_linear_rgb_tiff, scanner_raw_with_clear_border
    from film_foundry.core.io_utils import cv_imread_unicode
    from film_foundry.core import material_parallel
    from film_foundry.model.config import DarkroomConfig
    monkeypatch.setattr(material_parallel, 'PARALLEL_MIN_PIXELS', 1)
    c = DarkroomConfig()
    c.scanner.scanner_response_matrix = ((.9,.1,0),(.03,.96,.01),(.0,.1,.9))
    rng = np.random.default_rng(19)
    density = rng.uniform(-.01, 2.5, (517, 97, 3)).astype(np.float32)
    density.setflags(write=False)
    options = dict(optical_density_rgb=density, clear_base_optical_density_rgb=(.1,.2,.03))
    with execution_topology('reference'):
        expected = scanner_raw_with_clear_border(density, c.film, c.scanner, **options)
    actual = scanner_raw_with_clear_border(density, c.film, c.scanner, workers=4, **options)
    np.testing.assert_array_equal(actual, expected)
    for shape, border in (((13,17,3),0),((13,17,3),9),((257,503,3),22),((257,503,3),300)):
        source = rng.uniform(-.1, 1.1, shape).astype(np.float32)
        source.setflags(write=False)
        with execution_topology('reference'):
            save_linear_rgb_tiff(source,tmp_path/'old.tif',watermark_payload={'id':'ggJY19'},border_width=border)
        save_linear_rgb_tiff(source,tmp_path/'new.tif',workers=4,watermark_payload={'id':'ggJY19'},border_width=border)
        np.testing.assert_array_equal(cv_imread_unicode(tmp_path/'old.tif'),cv_imread_unicode(tmp_path/'new.tif'))


def test_npz_borrowed_bytes_keep_numpy_layouts(tmp_path):
    from film_foundry.core.atomic_io import atomic_savez
    arrays = {'rgb': np.arange(513*73*3,dtype=np.float32).reshape(513,73,3),
              'fortran': np.asfortranarray(np.arange(56).reshape(7,8)),
              'slice': np.arange(80,dtype='>i4').reshape(10,8)[::2,::2],
              'empty': np.empty((0,3),np.float32), 'scalar':np.asarray('底片'),
              'time': np.array(['2026-11-08'],dtype='datetime64[D]'),
              'records': np.array([(3, .2)],dtype=[('数量','i4'),('density','f4')])}
    atomic_savez(tmp_path/'medium.npz',compression_level=1,parallel_compression=True,**arrays)
    with np.load(tmp_path/'medium.npz',allow_pickle=False) as stored:
        for key, value in arrays.items():
            assert stored[key].dtype == value.dtype
            np.testing.assert_array_equal(stored[key],value)


def test_editor_filter_and_mode_context_preserve_drafts(tmp_path, monkeypatch):
    from film_foundry.ui.qt import _application, QtGui
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    app = _application();config=DarkroomConfig()
    material=FilmMaterialEditor(config=config);process=DevelopProcessEditor(config=config)
    try:
        material.scalar_vars['silver_grain_strength'].set(.17)
        process.vars['reticulation_strength'].set(.35)
        for editor, query in ((material,'silver_grain_strength'),(process,'reticulation_strength')):
            editor.root.qt.show();app.processEvents()
            editor.parameter_search.setText(query)
            assert editor.parameter_matches.count()==1
            assert editor.parameter_matches.currentData().isVisible()
            editor.parameter_search.setText('does-not-exist')
            assert editor.parameter_matches.count()==0
            editor.parameter_search.clear()
            editor.parameter_sections.setCurrentIndex(0)
            config.fast_mode=True
            editor.set_execution_context(config)
            assert editor.formation_badge.execution_info['basic_formation_only']
            config.fast_mode=False
            editor.set_execution_context(config)
            assert not editor.formation_badge.execution_info['lower_order_enabled']
        assert material._config_from_controls().film.silver_grain_strength==.17
        assert process._config_from_controls().chemistry.reticulation_strength==.35
        material.parameter_search.setText('layer sensitivity')
        assert len(material.matrix_inputs['layer_sensitivity_matrix'])==9
        assert all(widget.qt.isVisible() for widget in material.matrix_inputs['layer_sensitivity_matrix'])
        config.fast_mode=True
        material.set_execution_context(config)
        material._load_selected_preset()
        assert material.formation_badge.execution_info['basic_formation_only']
        material.material_class.set('bw_negative')
        assert all(not widget.qt.isEnabled() for widget in material.field_controls['dir_diffusion_radius'])
    finally:
        material.root.destroy();process.root.destroy();app.processEvents()
