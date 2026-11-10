import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import asdict
from time import monotonic
import numpy as np
import pytest


def wait(predicate):
    from film_foundry.ui.qt import _application
    deadline=monotonic()+12
    while not predicate() and monotonic()<deadline:_application().processEvents()
    assert predicate()


def test_wet_front_precision_preserves_tiling_windows_and_narrow_front_fallback(monkeypatch):
    from film_foundry.core.edge_wetting import contact_duration
    from film_foundry.tests.test_edge_wetting import event
    from film_foundry.core.compute_precision import contact_precision
    from film_foundry.model.config import ProcessingConfig
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    shape=(131,211);extent=(36.,24.);e=event()
    def run(a=0,b=131,window=(0.,1.),precision='float32',ev=e):
        return contact_duration(shape,extent,a,b,120.,ev,window,precision=precision)
    baseline=run(precision='float64');single=run()
    np.testing.assert_allclose(single,baseline,rtol=0,atol=.0002)
    np.testing.assert_array_equal(single,np.concatenate([run(0,39),run(39,131)]))
    np.testing.assert_allclose(single,sum(run(window=(i/8,(i+1)/8)) for i in range(8)),rtol=0,atol=.0001)
    tiny={**e,'meniscus_width_mm':.0001}
    np.testing.assert_array_equal(run(ev=tiny),run(ev=tiny,precision='float64'))
    for tier,expected in [('draft','float32'),('standard','float32'),('high','float64')]:
        assert contact_precision(ProcessingConfig(quality_mode=tier))==expected


def test_comparison_switch_wipe_and_press_release_use_cached_pixels():
    from film_foundry.ui.qt import QtCore,_application
    from film_foundry.ui.comparison_view import ComparisonView
    from PySide6.QtTest import QTest
    app=_application();view=ComparisonView();view.resize(720,360);view.show()
    statistics=[];view.pixels_changed.connect(lambda pixels:statistics.append(pixels.copy()))
    source=np.zeros((40,60,3),np.float32);source[...,0]=1
    result=np.zeros_like(source);result[...,2]=1
    try:
        view.set_pair(source,result);app.processEvents();point=view.viewport().rect().center()
        assert view.source.toImage().pixelColor(20,20).blue()==255
        QTest.mousePress(view.viewport(),QtCore.Qt.MouseButton.LeftButton,pos=point)
        assert view.source.toImage().pixelColor(20,20).red()==255
        QTest.mouseRelease(view.viewport(),QtCore.Qt.MouseButton.LeftButton,pos=point)
        assert view.source.toImage().pixelColor(20,20).blue()==255
        view.set_comparison_mode('side_by_side');assert view.source.width()==120
        view.set_comparison_mode('wipe');image=view.source.toImage()
        assert image.pixelColor(5,20).red()==255 and image.pixelColor(55,20).blue()==255
        previous=len(statistics)
        a=view.mapFromScene(QtCore.QPointF(30,20));b=view.mapFromScene(QtCore.QPointF(15,20))
        QTest.mousePress(view.viewport(),QtCore.Qt.MouseButton.LeftButton,pos=a)
        QTest.mouseMove(view.viewport(),b)
        QTest.mouseRelease(view.viewport(),QtCore.Qt.MouseButton.LeftButton,pos=b)
        assert abs(view.divider-.25)<.01 and view.source.toImage().pixelColor(20,20).blue()==255
        assert len(statistics)==previous and np.all(statistics[-1][...,2]==255)
        view.set_rotation(1);assert view.source.size().width()==40 and view.source.size().height()==60
        np.testing.assert_array_equal(source[...,0],1)
    finally:view.close()


def test_film_config_undo_redo_rotation_and_export(tmp_path,monkeypatch):
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.core.io_utils import save_image_bundle
    from film_foundry.ui.qt import _application
    from PIL import Image
    app=_application();panel=DesktopPanel();panel.auto_preview.set(False)
    try:
        tools=panel.edit_tools;initial=panel.exposure_ev.get()
        panel.exposure_ev.set(1.25);tools.record()
        assert tools.actions.invoke('edit.undo');assert panel.exposure_ev.get()==initial
        tools.actions.invoke('edit.redo');assert panel.exposure_ev.get()==1.25
        interpretation=panel.scan_interpretation.get();preset=panel.film_preset.get()
        revision=panel._preview_revision
        tools.actions.invoke('image.rotate_right');assert panel._config().output.rotation_quarters==1
        assert panel._preview_revision==revision
        assert panel.scan_interpretation.get()==interpretation and panel.film_preset.get()==preset
        image=np.arange(18,dtype=np.float32).reshape(2,3,3)/18
        output=panel._config().output;output.bit_depth=8;output.anti_banding_strength=0
        save_image_bundle(image,tmp_path/'rotated.png',output)
        actual=np.asarray(Image.open(tmp_path/'rotated.png'))
        assert actual.shape==(3,2,3)
        # Same writer, without rotation, then a lossless pixel permutation.
        output.rotation_quarters=0;save_image_bundle(image,tmp_path/'original.png',output)
        np.testing.assert_array_equal(actual,np.rot90(np.asarray(Image.open(tmp_path/'original.png')),-1))
        tools.actions.invoke('edit.undo');assert panel._config().output.rotation_quarters==0
        tools.set_quality('high');tools.record();assert panel._config().processing.quality_mode=='high'
    finally:panel.root.destroy();app.processEvents()


@pytest.mark.parametrize('kind',('paper','integral'))
def test_material_action_undo_redo_rotation_preserves_formed_arrays(tmp_path,kind):
    from film_foundry.ui.material_lab_window import create_material_window
    from film_foundry.ui.qt import _application
    from PIL import Image
    page=create_material_window(kind);page.session.image=np.linspace(.01,.8,12*18*3,dtype=np.float32).reshape(12,18,3)
    try:
        page._submit(lambda:page.session.process('expose'), 'Expose');wait(lambda:not page.busy)
        assert page.last_error is None
        formed=page.session.state;fields={key:value.copy() for key,value in formed.fields.items()}
        tools=page.edit_tools;assert tools.actions.invoke('edit.undo');wait(lambda:not page.busy)
        assert page.session.state is None
        tools.actions.invoke('edit.redo');wait(lambda:not page.busy);assert page.session.state is formed
        tools.actions.invoke('image.rotate_right');wait(lambda:not page.busy)
        assert page.last_error is None and page.session.scan_adjustments['rotation_quarters']==1
        observed=page.session.preview(page.conditions());assert observed.shape[:2]==(18,12)
        path=tmp_path/'result.png';page.session.export_image(path,page.conditions())
        assert Image.open(path).size==(12,18)
        for key,value in fields.items():np.testing.assert_array_equal(formed.fields[key],value)
        tools.actions.invoke('edit.undo');wait(lambda:not page.busy)
        assert page.session.scan_adjustments.get('rotation_quarters',0)==0
    finally:
        wait(lambda:not page.busy);page.close();_application().processEvents()


def test_preset_filters_intersect_without_changing_index():
    from film_foundry.ui.qt import _application
    from film_foundry.ui.preset_picker import PresetPicker
    _application()
    picker=PresetPicker(None,'Presets',('Warm','Warm','Cool'),0,records=[
        {'key':'warm_stock','origin':'builtin','color':'color'},
        {'key':'my_warm','origin':'custom','color':'monochrome'},
        {'key':'cool_stock','origin':'builtin','color':'color'}])
    try:
        picker.filters['origin'].setCurrentIndex(picker.filters['origin'].findData('custom'))
        picker.search.setText('my_warm');picker._apply();assert picker.selected_index==1
        picker.search.setText('not found');assert not picker.apply.isEnabled()
        picker.clear_filters();assert all(not picker.results.item(i).isHidden() for i in range(3))
    finally:picker.close()


def test_film_wet_precision_full_formation_and_old_checkpoint(tmp_path,monkeypatch):
    import json
    from copy import deepcopy
    from film_foundry.tests.test_rotary_instability import config
    from film_foundry.tests.test_edge_wetting import event
    from film_foundry.tests.test_film_process_work import source,scene,same_medium
    from film_foundry.core.engine import develop_negative,scan_medium_output
    from film_foundry.core.film_process.checkpoint_io import JSON_KEY,load_checkpoint
    from film_foundry.app.film_process_work import start_work,continue_work
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.core.atomic_io import atomic_savez
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    c=config();c.output.material_storage_precision='full'
    c.chemistry.process_events={'model':'film_process_events_v15','events':[event(pause_s=40.)]}
    double=deepcopy(c);double.processing.contact_precision='float64'
    a=develop_negative(scene(),double,rng=np.random.default_rng(19))
    b=develop_negative(scene(),c,rng=np.random.default_rng(19))
    error=np.max(np.abs(scan_medium_output(a,double).output_srgb-scan_medium_output(b,c).output_srgb))*255
    assert error<.05
    path=source(tmp_path,double);checkpoint=tmp_path/'new.ffprocess.npz'
    start_work(path,checkpoint,double,stop_after=0)
    with np.load(checkpoint) as archive:payload={key:archive[key] for key in archive.files}
    metadata=json.loads(str(payload[JSON_KEY].item()))
    for settings in (metadata['config'],metadata['spatial_preparation']['binding']['config']):
        settings['processing'].pop('contact_precision');settings['output'].pop('rotation_quarters')
    payload[JSON_KEY]=np.asarray(json.dumps(metadata));old=tmp_path/'old.ffprocess.npz';atomic_savez(old,**payload)
    assert load_checkpoint(old)[0]['config']['processing']['contact_precision']=='float64'
    expected=continue_work(checkpoint,tmp_path/'new.npz');restored=continue_work(old,tmp_path/'old.npz')
    same_medium(load_developed_negative_npz(expected.output_path),load_developed_negative_npz(restored.output_path))
