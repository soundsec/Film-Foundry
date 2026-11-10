import copy
import numpy as np
from PIL import Image
from film_foundry.tests.test_desktop_usability import desktop,photos
from film_foundry.model.config import DarkroomConfig,FilmBorderConfig
from film_foundry.ui.qt import _application


def test_controls_reset_pairing_and_library_mode(desktop,tmp_path):
    panel=desktop;panel._open_catalog_batch(photos(tmp_path),'film')
    panel.open_library();library=panel.library_view
    library.work_mode.blockSignals(True);library.work_mode.setCurrentIndex(library.work_mode.findData('darkroom'));library.work_mode.blockSignals(False)
    panel.show_workbench();panel.open_library()
    assert library.work_mode.currentData()=='photos'
    panel.show_workbench()
    panel.exposure_ev.set(.8);panel.grain.set(1.7)
    panel.auto_develop.set(True);panel._apply_recommended_development()
    assert panel.auto_develop.get() and panel.exposure_ev.get()==.8 and panel.grain.get()==1.7
    baseline=panel.develop_time.get();panel.develop_time.set(baseline+1)
    assert not panel.auto_develop.get()
    panel.control_resets.reset_key(panel.develop_time);assert panel.develop_time.get()==baseline
    panel.link_scanner.set(True);panel.film_preset.set('warm_consumer_negative');panel._apply_matching_scanner()
    assert panel.scanner_preset.get()=='warm_consumer_scan'
    panel._on_scanner_preset_display_selected();assert not panel.link_scanner.get()
    panel.imprint_text.set('1999-12-31');panel._edit_imprint()
    dialog=next(w for w in _application().topLevelWidgets() if w.__class__.__name__=='ImprintDialog' and w.isVisible())
    dialog.move(.72,.25);dialog.accept()
    assert panel._config().film_border.imprint_position_xy==(.72,.25)
    loaded=copy.deepcopy(panel._config());loaded.look.print_exposure_ev=.6;panel._apply_loaded_config(loaded)
    panel.print_exposure.set(1.2);panel.control_resets.reset_key(panel.print_exposure)
    assert panel.print_exposure.get()==.6
    panel.auto_develop.set(True);panel.link_scanner.set(True)
    panel.edit_tools.rotate(1)
    assert panel.auto_develop.get() and panel.link_scanner.get()
    panel.print_exposure.set(1.1);panel.control_resets.reset_key(panel.print_exposure);assert panel.print_exposure.get()==.6
    panel.curve_editors['tone_curve'].load(((0.,0.),(.5,.6),(1.,1.)))
    panel.control_resets.reset_group(panel.sections['scan.curves'].qt)
    assert panel.curve_editors['tone_curve'].read() is None
    panel.auto_develop.set(False);panel.develop_time.set(9.1)
    label=next(k for k,v in panel._film_display_key_map.items() if v=='clear_modern_negative')
    panel.film_preset_display.set(label);panel._on_film_preset_display_selected()
    assert panel.develop_time.get()==9.1 and not panel.auto_develop.get()
    panel.auto_develop.set(True);panel.link_scanner.set(True)
    for action in panel._view_submenus[1].actions():
        if 'English' in action.text():action.trigger();break
    assert panel.auto_develop.get() and panel.link_scanner.get()


def test_retro_imprint_and_factory_templates_freeze_and_serialize():
    from film_foundry.core.film_border import _render_imprint_mask,resolve_film_border_layout
    from film_foundry.core.formed_film_border import _strip_masks,border_snapshot,validate_border_snapshot
    cfg=FilmBorderConfig(enabled=True,factory_layout='text_code',stock_label='FF 400',frame_number=12,
        imprint_style='optical_amber',imprint_text='1999-12-31 ABC!',imprint_font='seven_segment',imprint_position_xy=(.7,.2))
    mask=_render_imprint_mask((240,480),cfg);assert mask.max()>.1
    ys,xs=np.where(mask>.1);assert ys.mean()<100 and xs.mean()>200
    old=copy.deepcopy(cfg);old.imprint_position_xy=(.1,.8)
    assert np.where(_render_imprint_mask((240,480),old)>.1)[0].mean()>150
    layout=resolve_film_border_layout(cfg,(120,480,3));dose,holes=_strip_masks(cfg,layout,'FF 400')
    cfg2=copy.deepcopy(cfg);cfg2.frame_number=13
    second,_=_strip_masks(cfg2,layout,'FF 400');rows=dose.shape[0]//2
    np.testing.assert_array_equal(dose[:rows],second[:rows]);assert not np.array_equal(dose[rows:],second[rows:])
    assert validate_border_snapshot(border_snapshot(cfg))['frame_number']==12
    full=DarkroomConfig();full.film_border=cfg
    from dataclasses import asdict
    restored=DarkroomConfig.from_dict(asdict(full))
    assert restored.film_border.imprint_font=='seven_segment' and restored.film_border.factory_layout=='text_code'


def test_lamp_changes_survive_base_correction_without_mutating_medium():
    from film_foundry.core.engine import develop_negative,scan_medium_direct
    cfg=DarkroomConfig(enable_mtf=False,enable_halation=False,enable_grain=False)
    cfg.scanner.scan_normalize=False;cfg.scanner.remove_base_mask=True
    medium=develop_negative(np.linspace(.02,.8,240,dtype=np.float32).reshape(8,10,3),cfg)
    original=medium.optical_density_rgb.copy();values=[]
    for ev in (-1,0,1):
        cfg.scanner.transmission_light_ev=ev
        values.append(scan_medium_direct(medium,cfg,'manual').output_srgb)
    assert np.mean(abs(values[0]-values[1]))>.01
    assert np.mean(abs(values[2]-values[1]))>.01
    np.testing.assert_array_equal(original,medium.optical_density_rgb)


def test_image_only_scan_does_not_form_material(tmp_path):
    from film_foundry.app.api import WorkflowRequest,run_request
    cfg=DarkroomConfig();cfg.scanner.interpretation_mode='auto';cfg.scanner.scan_normalize=False
    cfg.scanner.scan_saturation=1.;cfg.output.anti_banding_strength=0
    for suffix in ('png','jpg','tiff'):
        path=tmp_path/('source.'+suffix);Image.new('RGB',(32,20),(80,120,160)).save(path)
        result=run_request(WorkflowRequest('scan',path,tmp_path/'unused.png',cfg))
        assert result.source_kind=='image_rgb' and result.medium_path is None
        assert result.extra['scanned'].output_srgb.shape==(20,32,3)
    assert not list(tmp_path.glob('*.npz'))
