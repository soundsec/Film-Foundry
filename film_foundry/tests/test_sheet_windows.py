import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import asdict,replace
from time import monotonic
import json
import numpy as np
from PIL import Image
import pytest
from film_foundry.ui.qt import _application,QtWidgets
from film_foundry.ui.material_lab_window import create_material_window
from film_foundry.ui.sheet_lab_window import SheetBatchWindow
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.app.material_families import load


@pytest.mark.parametrize('kind',['wet','cyanotype','platinum'])
def test_legacy_product_controls_restore_without_new_material_keys(kind,tmp_path,monkeypatch):
    from film_foundry.app import material_families as api
    from film_foundry.core.material_family import ProductGroup
    from film_foundry.ui.sheet_workspace_design import V2_KEYS
    from film_foundry.tests.test_sheet_materials import finish
    monkeypatch.setenv('FILM_FOUNDRY_CATALOG',str(tmp_path/'catalog.json'))
    window=create_material_window(kind)
    try:
        module=window.session.module
        material=window.session.material_type(coating_variation=.37)
        state=module.expose(np.full((12,16,3),.4,np.float32),material,group_id='old',seed=8,schema_version=1)
        group=module.products(finish(module,state));body=group.bodies[0]
        presentation=json.loads(body.presentation_json)
        for key in V2_KEYS[kind]:presentation['material'].pop(key)
        old=replace(body,presentation_json=json.dumps(presentation))
        archive=tmp_path/'old.ffmaterial.npz';api.save(archive,ProductGroup(group.group_id,(old,),group.provenance_json))
        window.session.load(archive);frozen=window.session.group
        expected=window.session.preview()
        window._restore_controls();window._sync()
        assert window.material_controls['coating_variation'].value()==.37
        assert window.session.group is frozen and frozen.bodies[0].schema_version==1
        assert all(key not in json.loads(frozen.bodies[0].presentation_json)['material'] for key in V2_KEYS[kind])
        assert all(window.material_controls[key].isHidden() for key in V2_KEYS[kind])
        np.testing.assert_array_equal(window.session.preview(),expected)
    finally:window.close();_application().processEvents()


def wait(window):
    app=_application();deadline=monotonic()+8
    while window.busy and monotonic()<deadline:app.processEvents()
    assert not window.busy
    assert window.last_error is None,window.last_error


@pytest.mark.parametrize('kind',['paper','wet','cyanotype','platinum'])
def test_new_window_workflow_layout_storage_and_immutable_view(kind,tmp_path,monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_CATALOG',str(tmp_path/'catalog.json'))
    path=tmp_path/'image.png';Image.fromarray(np.broadcast_to(np.linspace(0,255,64,dtype=np.uint8)[None,:,None],(48,64,3))).save(path)
    window=create_material_window(kind);window.show()
    try:
        window._submit(lambda:window.session.open_image(path,0),'open');wait(window)
        window.set_workspace_layout('studio');_application().processEvents()
        assert window.right_scroll.isVisible() and window.process_group.parent() is window.right_controls
        assert window.process_group.isVisible()
        window.set_workspace_layout('compact');assert window.config_tabs.indexOf(window.process_group)>=0
        window.operation_choice.setCurrentIndex(window.operation_choice.findData('handling'))
        assert not window.execute_button.isEnabled()
        window.material_controls['coating_variation'].setValue(.4)
        window.seed.setValue(4000000000)
        window.run_standard();wait(window)
        assert window.session.group is not None and not window.material_controls['coating_variation'].isEnabled()
        assert window.material_sections.isEnabled()  # frozen fields stay readable in every section
        assert window.session.state.seed==4000000000
        before=window.session.state
        window.view_ev.setValue(.5);window.refresh_preview();wait(window)
        assert window.session.state is before and window.viewer.source is not None
        archive=tmp_path/'medium.ffmaterial.npz'
        monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *a,**k:(str(archive),''))
        results=[];window.result_ready.connect(results.append);window.save_dialog('products');wait(window)
        assert len(results)==1 and results[0]['family']==kind
        assert load(archive).bodies[0].family==window.session.module.FAMILY
        window._submit(lambda:window.session.load(archive),'load',restore_controls=True);wait(window)
        assert window.material_controls['coating_variation'].value()==.4
        assert not window.execute_button.isEnabled()
        png=tmp_path/'result.png';monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *a,**k:(str(png),''))
        window.save_dialog('png');wait(window)
        with Image.open(png) as image:
            assert image.size==(64,48)
            assert json.loads(image.info['FilmFoundryObservation'])['view']['exposure_ev']==.5
    finally:window.close();_application().processEvents()


def test_wet_prepared_archive_can_receive_image_after_reopening(tmp_path):
    path=tmp_path/'input.png';Image.new('RGB',(16,12),'white').save(path)
    session=MaterialLabSession('wet');session.open_image(path,0);session.process('prepare');session.process('sensitize')
    archive=tmp_path/'wet.ffmaterial.npz';session.save(archive)
    resumed=MaterialLabSession('wet');resumed.load(archive)
    assert 'expose' not in resumed.operations
    resumed.open_image(path,0,preserve_preparation=True);resumed.process('expose');resumed.process('develop');resumed.process('fix');resumed.process('wash')
    assert resumed.group is not None
    medium=tmp_path/'formed.ffmaterial.npz';resumed.save_products(medium)
    prepared=MaterialLabSession('wet');prepared.load(archive);before=prepared.state
    prepared.open_medium(medium,long_edge=0,preserve_preparation=True)
    assert prepared.state is before
    prepared.process('expose')
    assert prepared.state.group_id==before.group_id
    assert len(json.loads(prepared.state.process_json)['optical_cascade']['steps'])==1


def test_sheet_batch_uses_controls_and_registers_each_result(tmp_path,monkeypatch):
    paths=[]
    for index in range(2):
        path=tmp_path/f'{index}.png';Image.new('RGB',(16,12),(index*100+50,50,150)).save(path);paths.append(str(path))
    window=SheetBatchWindow('paper',paths);wait(window)
    try:
        output=tmp_path/'batch';output.mkdir()
        monkeypatch.setattr(QtWidgets.QFileDialog,'getExistingDirectory',lambda *a,**k:str(output))
        window.material_controls['kind'].setCurrentIndex(window.material_controls['kind'].findData('color'))
        window.operation_controls['develop']['seconds'].setValue(20)
        records=[];window.result_ready.connect(records.append);window.start_batch();wait(window)
        assert len(records)==2
        state=load(records[0]['artifacts']['process_state'])
        assert state.material.kind=='color'
        assert next(e for e in json.loads(state.process_json)['events'] if e['operation']=='develop')['seconds']==20
        assert state.fields['dye'].max()>0 and state.fields['silver'].max()<.003
    finally:window.close();_application().processEvents()


def test_optical_source_panel_selects_body_filter_and_freezes_provenance(tmp_path,monkeypatch):
    path=tmp_path/'input.png';Image.new('RGB',(16,12),'gray').save(path)
    source=MaterialLabSession('wet');source.open_image(path,0);source.run_standard()
    medium=tmp_path/'wet.ffmaterial.npz';source.save_products(medium)
    window=create_material_window('paper')
    try:
        monkeypatch.setattr(QtWidgets.QFileDialog,'getOpenFileName',lambda *a,**k:(str(medium),''))
        window.open_medium_dialog()
        panel=window.optical_panel
        assert not panel.isWindow() and not panel.mode.isEnabled()
        panel.filters[1].setValue(.8);panel.ev.setValue(1.);panel.applied.emit();wait(window)
        window.run_standard();wait(window)
        trace=json.loads(window.session.state.process_json)['optical_cascade']
        assert trace['steps'][0]['relay']['filter_density_rgb']==[0,.8,0]
        monkeypatch.setattr(window,'confirm_replace',lambda:True)
        window.restart();wait(window);window.run_standard();wait(window)
        assert len(json.loads(window.session.state.process_json)['optical_cascade']['steps'])==1
    finally:window.close();_application().processEvents()


def test_specific_workflow_guides_cyanotype_and_maps_never_become_export(tmp_path,monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_CATALOG',str(tmp_path/'catalog.json'))
    path=tmp_path/'image.png';Image.new('RGB',(48,32),(0,100,180)).save(path)
    window=create_material_window('cyanotype');window.show()
    try:
        window._submit(lambda:window.session.open_image(path,0),'load');wait(window)
        assert tuple(window.flow_buttons)==('expose','wash','oxidize')
        assert window.material_sections.itemText(0) in ('纸与刷涂','Paper & brushing')
        for operation,next_op in (('expose','wash'),('wash','oxidize')):
            window.flow_buttons[operation].click();assert window.operation_choice.currentData()==operation
            window.execute_button.click();wait(window);assert window.operation_choice.currentData()==next_op
        window.execute_button.click();wait(window)
        before=window.session.state
        window.inspection.setCurrentIndex(window.inspection.findData('binding'));window.refresh_preview();wait(window)
        assert window.session.state is before
        png=tmp_path/'image-export.png';monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *a,**k:(str(png),''))
        expected=np.uint8(window.session.preview(window.conditions())*255+.5)
        window.save_dialog('png');wait(window)
        with Image.open(png) as result:np.testing.assert_array_equal(np.asarray(result),expected)
    finally:window.close();_application().processEvents()
