"""Public entry points at the boundaries between frozen media, work settings and observation."""
from dataclasses import asdict, replace
import json

import numpy as np
import pytest

from film_foundry.model.config import DarkroomConfig
from film_foundry.tests.test_film_workspace import source, payload, config
from film_foundry.tests.test_unified_workspace import panel, wait


def test_scan_framing_and_sample_never_change_capture():
    from film_foundry.core.engine import develop_negative, scan_medium_direct, scan_medium_output, scan_scanner_raw
    from film_foundry.core.scan_framing import frame_scan, capture_position
    c=config(); c.scanner.transmission_light_ev=0.
    image=np.linspace(.04,.94,21*31*3,dtype=np.float32).reshape(21,31,3)
    c.scanner.include_clear_base_border=False
    medium=develop_negative(image,c,rng=np.random.default_rng(19))
    master=medium.optical_density_rgb.copy()
    c.scanner.base_sample_rect=(0.,0.,.25,1.)
    original=scan_medium_direct(medium,c,'negative')
    expected_base=np.median(original.scanner_raw[:,:8].reshape(-1,3),axis=0)
    np.testing.assert_allclose(original.metadata['base_anchor_rgb'],expected_base,rtol=1e-5)
    c.scanner.crop_rect=(.25,.15,.75,.85);c.scanner.straighten_degrees=7.
    full=scan_medium_direct(medium,c,'negative');light=scan_medium_output(medium,c,'negative')
    framed=frame_scan(original.output_srgb,c.scanner)
    np.testing.assert_array_equal(full.output_srgb,framed)
    np.testing.assert_allclose(light.output_srgb,framed,atol=2e-5)
    np.testing.assert_array_equal(full.scanner_raw,original.scanner_raw)
    np.testing.assert_array_equal(medium.optical_density_rgb,master)
    assert DarkroomConfig.from_dict(asdict(c)).scanner==c.scanner
    assert capture_position(((21,31),0,(.25,.15,.75,.85)),1,2)==(8,5,31,21)
    raw=scan_scanner_raw(image,c,known_base_transmittance_rgb=np.ones(3))
    np.testing.assert_allclose(raw.metadata['base_anchor_rgb'],np.median(image[:,:8].reshape(-1,3),axis=0))
    with pytest.raises(ValueError):replace(c.scanner,crop_rect=(.9,0,.1,1))
    # A selected base patch may cross the synthetic edge. That edge is still
    # captured under the actual scan lamp for both tiled and full renders.
    c.scanner.include_clear_base_border=True;c.scanner.crop_rect=None
    c.scanner.base_sample_rect=(.02,.05,.3,.5);c.scanner.transmission_light_ev=-1.
    c.processing.scan_tile_rows=5;c.processing.scan_tile_threshold_megapixels=0.
    tiled=scan_medium_output(medium,c,'negative')
    expected=scan_medium_direct(medium,c,'negative')
    np.testing.assert_allclose(tiled.output_srgb,expected.output_srgb,atol=2e-5)


def test_recorded_raw_base_and_eyedropper_survive_reload(tmp_path):
    from film_foundry.app.workflow import observe_medium_file
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.engine import scan_medium_output,scan_medium_direct
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    from film_foundry.app.scan_calibration import CalibrationPreview
    from film_foundry.model.config import ScannerConfig
    c=config();c.output.save_scanner_raw=True;c.save_sidecar=True
    c.scanner.transmission_light_ev=-1.;c.output.scanner_raw_border_percent=0.
    c.scanner.interpretation_mode='manual';c.scanner.invert_transmission=True
    path=source(tmp_path/'photo')
    medium_path=run_request(WorkflowRequest('develop',path,tmp_path/'negative.npz',c)).medium_path
    medium=load_developed_medium_for_scan(medium_path)
    raw=medium_path.with_suffix('.scanner_raw.tiff')
    sidecar=raw.with_suffix(raw.suffix+'.json')
    recorded=json.loads(sidecar.read_text(encoding='utf8'))
    native=scan_medium_direct(medium,c,'manual')
    expected=native.metadata['base_anchor_rgb']
    np.testing.assert_allclose(recorded['clear_base_reference_rgb'],expected,rtol=1e-6)
    # The TIFF was already captured. Current lighting or stock cannot change its reference.
    c.scanner.transmission_light_temperature_k=3200
    for old_sidecar in (False,True):
        if old_sidecar:
            recorded.pop('clear_base_reference_rgb')
            sidecar.write_text(json.dumps(recorded),encoding='utf8')
        auto=observe_medium_file(raw,c)[0]
        assert auto.metadata['base_balance_source']=='recorded_clear_base'
        np.testing.assert_allclose(auto.metadata['base_anchor_rgb'],expected,rtol=1e-6)
    capture=CalibrationPreview(raw,'scan',c,None,640).render(True,None,None)
    picked=capture.pick(.5,.5)
    h,w=auto.scanner_raw.shape[:2]
    center=auto.scanner_raw[h//2-1:h//2+2,w//2-1:w//2+2]
    np.testing.assert_array_equal(picked,np.median(center.reshape(-1,3),axis=0))
    assert picked!=tuple(expected)
    c.scanner.base_reference_rgb=picked
    saved=DarkroomConfig.from_dict(json.loads(json.dumps(asdict(c))))
    for mode in (True,False):
        saved.scanner.invert_transmission=mode
        observed=observe_medium_file(raw,saved)[0]
        assert observed.metadata['base_balance_source']=='eyedropper_rgb'
        np.testing.assert_allclose(observed.metadata['base_anchor_rgb'],picked)
        np.testing.assert_allclose(observed.negative_base_balanced,np.clip(observed.scanner_raw/np.float32(picked),1e-6,1))
        full=scan_medium_direct(medium,saved,'manual')
        saved.processing.scan_tile_threshold_megapixels=0.;saved.processing.scan_tile_rows=4
        tiled=scan_medium_output(medium,saved,'manual')
        np.testing.assert_allclose(full.output_srgb,tiled.output_srgb,atol=2e-5)
    saved.scanner.remove_base_mask=False
    without=observe_medium_file(raw,saved)[0]
    assert np.array_equal(without.negative_base_balanced,without.scanner_raw)
    sidecar.rename(tmp_path/'prior-sidecar.json')
    assert observe_medium_file(medium_path,c)[1]=='density_npz'
    # A physical border can be darker than the nominal reference. Its visible
    # pixels retain their capture value when the scanner adds a margin.
    from film_foundry.core.electronic_negative import save_linear_rgb_tiff
    physical=(.12,.22,.32)
    bordered=np.empty((h+2,w+2,3),np.float32);bordered[:]=physical;bordered[1:-1,1:-1]=auto.scanner_raw
    capture_path=tmp_path/'bordered.scanner_raw.tiff';save_linear_rgb_tiff(bordered,capture_path)
    recorded['border'].update(width_px=1,present=True)
    capture_path.with_suffix('.tiff.json').write_text(json.dumps(recorded),encoding='utf8')
    c.scanner.base_reference_rgb=None;c.scanner.include_clear_base_border=True
    c.output.scanner_raw_border_percent=.1;c.output.scanner_raw_border_min_px=1
    with_margin=observe_medium_file(capture_path,c)[0]
    np.testing.assert_allclose(with_margin.scanner_raw[0,0],physical,atol=2e-5)
    np.testing.assert_allclose(with_margin.metadata['base_anchor_rgb'],expected,rtol=1e-6)
    with pytest.raises(ValueError):ScannerConfig(base_reference_rgb=(.5,float('nan'),.6))


def test_per_frame_offset_survives_move_and_resume(tmp_path):
    from film_foundry.app import film_workspace as store
    from film_foundry.app.workspace_jobs import process_photos
    from film_foundry.app.replay_batch import load_resume_plan,run_resume_batch
    from film_foundry.core.latent_io import load_exposed_latent
    original=[source(tmp_path/'a'),source(tmp_path/'b')]
    c=config();c.seed_strategy='fixed';c.look.exposure_ev=.25
    record=payload(c,'expose')
    record['frame_exposure_offsets']={str(original[0]):-1.,str(original[1]):.75}
    package=tmp_path/'one.fffilm'
    store.save_film_workspace(package,store.snapshot_film_workspace(record,original,active=1))
    moved=package.rename(tmp_path/'moved.fffilm');work=store.load_film_workspace(moved)
    assert work.source_names==('source.png','source.png')
    assert work.session['paths']['input']==str(work.sources[1])
    offsets=work.session['frame_exposure_offsets']
    assert list(offsets)==list(map(str,work.sources)) and list(offsets.values())==[-1.,.75]
    assert 'frame_exposure_offsets' not in json.loads((moved/'workspace.json').read_text(encoding='utf8'))['session']
    roots=[]
    assert process_photos(work.sources,tmp_path/'jobs',c,command='expose',frame_exposure_offsets=offsets,on_run=roots.append)==[]
    plan=load_resume_plan(roots[0]/'film_foundry_run.json')
    one,two=(load_exposed_latent(item.output_path)[0].developability.copy() for item in plan.items)
    assert two.mean()>one.mean() and c.exposure_ev==.25
    plan.items[1].output_path.rename(tmp_path/'previous.npz')
    result=run_resume_batch(plan)
    assert (result.reused,result.completed,result.failures)==(1,1,[])
    np.testing.assert_array_equal(two,load_exposed_latent(plan.items[1].output_path)[0].developability)
    assert plan.config.exposure_ev==.25
    manifest=moved/'workspace.json'
    legacy=json.loads(manifest.read_text(encoding='utf8'))
    for entry in legacy['sources']:entry.pop('exposure_offset_ev')
    manifest.write_text(json.dumps(legacy),encoding='utf8')
    assert store.load_film_workspace(moved).session['paths']['input']==str(moved/'sources')


def test_local_exposure_on_receiving_plane_and_optical_provenance():
    from film_foundry.contracts.optical_cascade import OpticalRelay, PrintExposureRegion
    from film_foundry.core.optical_relay import relay_dose
    from film_foundry.core.engine import develop_negative,expose_linear_to_latent
    from film_foundry.app.optical_cascade import expose_on_film,project_medium
    c=config();c.seed_strategy='fixed'
    scene=np.repeat(np.linspace(.05,.65,11,dtype=np.float32)[None,:,None],9,axis=0).repeat(3,axis=2)
    region=PrintExposureRegion((0,.1,.5,.9),-1,0.)
    relay=OpticalRelay(flip_horizontal=True,local_exposures=(region,replace(region,ev=2)))
    assert 'local_exposures' not in OpticalRelay().as_dict()
    assert OpticalRelay.from_value(json.loads(json.dumps(relay.as_dict())))==relay
    base=relay_dose(scene,replace(relay,local_exposures=()))
    dose=relay_dose(scene,relay)
    np.testing.assert_array_equal(dose[4,2],2*base[4,2])
    np.testing.assert_array_equal(dose[4,8],base[4,8])
    medium=develop_negative(scene,c,rng=np.random.default_rng(19));master=medium.optical_density_rgb.copy()
    latent,info=expose_on_film(medium,c,relay,rng=np.random.default_rng(31))
    expected=expose_linear_to_latent(project_medium(medium,relay),c,rng=np.random.default_rng(31))
    np.testing.assert_array_equal(latent.developability,expected.developability)
    assert OpticalRelay.from_value(info['steps'][-1]['relay'])==relay
    np.testing.assert_array_equal(master,medium.optical_density_rgb)


def test_pinned_edit_and_selected_source_dialog(panel,tmp_path):
    from film_foundry.ui.parameter_pins import ParameterPins,set_film_parameter,film_editable
    from film_foundry.ui.preferences import load_preferences
    from film_foundry.ui.qt import QtWidgets
    paths=[str(source(tmp_path/'a')),str(source(tmp_path/'b'))]
    panel._replace_film_sources(paths);before=panel._config().exposure_ev
    pins=ParameterPins(panel,film=True);pins.filter.setText('look.exposure_ev')
    assert pins.list.topLevelItemCount()==1
    pins.toggle()
    assert 'look.exposure_ev' in load_preferences()['parameter_pins']['film']
    common=panel.common_controls
    editor=common.findChild(QtWidgets.QDoubleSpinBox)
    assert editor is not None and common.findChild(QtWidgets.QSlider) is not None
    editor.setValue(before+.25)
    assert panel._config().look.exposure_ev==before+.25
    editor.setValue(before)
    set_film_parameter(panel,('look','exposure_ev'),before+.5)
    assert panel._config().exposure_ev==before+.5
    panel.edit_tools.travel(False);assert panel._config().exposure_ev==before
    pins.close()
    dialog=panel._show_selected_sources()
    rows=dialog.findChild(QtWidgets.QTreeWidget);rows.setCurrentItem(rows.topLevelItem(1))
    dialog.findChild(QtWidgets.QDoubleSpinBox).setValue(.75);dialog.accept()
    assert panel._fast_preview_config().exposure_ev==before+.75
    assert panel._session_payload()['frame_exposure_offsets']=={paths[1]:.75}
    panel.edit_tools.travel(False)
    assert panel._fast_preview_config().exposure_ev==before
    panel.pipeline_mode.set('scan')
    assert not film_editable(panel,('look','exposure_ev'))


def test_scan_dialog_and_cached_probe(panel,tmp_path):
    from film_foundry.ui.scan_framing import open_framing
    from film_foundry.app.preview_probe import PreviewProbe
    from film_foundry.app.preview_session import PreviewSession
    from film_foundry.core.formation_observer import observe
    from film_foundry.core.scan_framing import capture_position
    path=source(tmp_path/'photo')
    panel._replace_film_sources([str(path)])
    linked=panel.link_scanner.get()
    inspect=open_framing(panel,calibration=True);wait(lambda:inspect.frame is not None)
    assert inspect.mode.currentIndex()==1 and inspect.auto.isEnabled() is False
    inspect.accept();assert panel.link_scanner.get()==linked
    dialog=open_framing(panel);wait(lambda:dialog.canvas.pixmap is not None)
    rect=(.2,.1,.8,.9)
    dialog.angle.setValue(5);dialog.update_region(rect)
    dialog.mode.setCurrentIndex(1)
    from PySide6.QtTest import QTest
    from film_foundry.ui.qt import QtCore
    area=dialog.canvas.frame()
    point=area.center().toPoint()
    expected=dialog.frame.pick(*dialog.canvas.normalized(point))
    dialog.dropper.click();QTest.mouseClick(dialog.canvas,QtCore.Qt.MouseButton.LeftButton,pos=point)
    assert dialog.scanner.base_reference_rgb==expected and dialog.remove_base.isChecked()
    wait(lambda:dialog.rendered_key==dialog.key())
    assert dialog.swatches.text() and dialog.frame.reference_source=='eyedropper_rgb'
    dialog.auto.click();wait(lambda:dialog.rendered_key==dialog.key())
    assert dialog.scanner.base_reference_rgb is None and dialog.frame.reference_source=='generated_clear_base'
    dialog.dropper.click();QTest.mouseClick(dialog.canvas,QtCore.Qt.MouseButton.LeftButton,pos=point)
    dialog.accept()
    c=panel._fast_preview_config()
    assert c.scanner.crop_rect==rect and c.scanner.straighten_degrees==5 and c.scanner.base_sample_rect is None
    assert c.scanner.base_reference_rgb==expected
    from film_foundry.app.workflow import observe_medium_file
    ordinary=DarkroomConfig.from_dict(asdict(c))
    ordinary.scanner.interpretation_mode='auto';ordinary.scanner.remove_base_mask=True
    scan=observe_medium_file(path,ordinary)[0]
    assert scan.metadata['base_balance_source']=='eyedropper_rgb'
    np.testing.assert_allclose(scan.metadata['base_anchor_rgb'],expected)
    session=PreviewSession()
    for cached in (False,True):
        sink=PreviewProbe()
        with observe(sink):
            _,image,hit=session.render(path,c,long_edge=120)
        assert hit is cached and sink.scan_geometry[1:]==(5.,rect)
        h,w=image.shape[:2];x,y,iw,ih=capture_position(sink.scan_geometry,w//2,h//2)
        assert sink.density_at(x,y,iw,ih) is not None
    panel.edit_tools.travel(False)
    assert panel._config().scanner.crop_rect is None


def test_material_local_exposure_editor_draft_and_portable_dose(panel,tmp_path,monkeypatch):
    from film_foundry.app.api import run_request,WorkflowRequest
    from film_foundry.app import material_workspace as archive
    from film_foundry.app.material_lab_session import MaterialLabSession
    from film_foundry.app.optical_cascade import project_medium
    from film_foundry.contracts.optical_cascade import OpticalRelay
    from film_foundry.ui import material_workspace_state as drafts
    from film_foundry.ui.print_exposures import PrintExposures
    from film_foundry.ui.parameter_pins import ParameterPins
    path=source(tmp_path/'photo')
    medium=run_request(WorkflowRequest('develop',path,tmp_path/'negative.npz',config())).medium_path
    panel._open_catalog_source(str(path),'paper');page=panel._material_windows['paper'];wait(lambda:not page.busy)
    pins=ParameterPins(page,film=False);pins.filter.setText('work_size');pins.toggle()
    assert 'work_size' in pins.pins
    from film_foundry.ui.qt import QtWidgets
    pins.filter.setText('scan.print_gamma')
    with monkeypatch.context() as patch:
        patch.setattr(QtWidgets.QInputDialog,'getText',lambda *args,**kwargs: ('1.25',True))
        pins.edit_current()
    assert page.session.scan_adjustments['print_gamma']==1.25
    pins.close();wait(lambda:not page.busy)
    assert page.choose_optical_source(str(medium))
    optical=page.optical_panel;body,relay=optical.selection()
    dialog=PrintExposures(optical,medium,body,relay);dialog.open()
    wait(lambda:dialog.base is not None and dialog.canvas.pixmap is not None)
    dialog.add_region();dialog.edited_rect((.05,.1,.65,.9));dialog.ev.setValue(1.25)
    optical.local_exposures=tuple(dialog.regions);dialog.accept()
    saved=drafts.capture(page)
    assert OpticalRelay.from_value(saved['pending_optical']['relay']).local_exposures==optical.local_exposures
    archive_path=tmp_path/'draft.ffworkspace'
    archive.save_material_workspace(archive_path,[archive.snapshot_document(page.session,saved)])
    draft=archive.load_material_workspace(archive_path).documents[0].draft
    drafts.restore(page,draft)
    body,relay=page.optical_panel.selection()
    assert relay.local_exposures[0].ev==1.25
    session=MaterialLabSession('paper')
    session.open_medium(page.optical_panel.source_path,body_id=body,relay=relay,long_edge=16)
    smaller=replace(relay,output_shape=session.image.shape[:2])
    np.testing.assert_array_equal(session.image,project_medium(session.optical_source,smaller,body_id=body))
    ready=tmp_path/'ready.ffworkspace'
    archive.save_material_workspace(ready,[archive.snapshot_document(session,{})])
    restored=archive.load_material_workspace(ready).documents[0].session
    np.testing.assert_array_equal(restored.image,session.image)
    restored.process('expose',seed=19)
    step=json.loads(restored.state.process_json)['optical_cascade']['steps'][-1]
    assert OpticalRelay.from_value(step['relay'])==smaller
