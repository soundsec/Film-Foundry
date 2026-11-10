import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from time import monotonic
from dataclasses import replace
import numpy as np
from PIL import Image
import pytest
from film_foundry.app import material_families as api


def wait(predicate):
    from film_foundry.ui.qt import _application
    deadline=monotonic()+10
    while not predicate() and monotonic()<deadline:_application().processEvents()
    assert predicate()


@pytest.mark.parametrize('kind',('paper','integral'))
def test_resolution_switch_and_export_facts(tmp_path,kind):
    from film_foundry.ui.material_lab_window import create_material_window
    from film_foundry.ui.material_workspace_state import capture,restore
    from film_foundry.ui.qt import QtCore,_application
    from film_foundry.app.material_processing import processing_info
    page=create_material_window(kind);page.show()
    try:
        path=tmp_path/'input.png';Image.fromarray(np.full((24,1800,3),120,np.uint8)).save(path)
        assert page.work_size.value()==1600
        page.session.open_image(path,page.work_size.value());page._sync()
        assert page.session.image.shape[1]==1600
        page.processing_bar.limit.setChecked(False)
        wait(lambda:page.session.image.shape[1]==1800 and not page.busy)
        assert page.work_size.value()==0
        draft=capture(page);page.work_size.setValue(300);restore(page,draft)
        assert not page.processing_bar.limit.isChecked() and page.work_size.value()==0
        if kind=='paper':page.session.run_standard()
        else:
            for op,p in [('expose',{}),('eject',{}),('spread',{}),('advance',{'seconds':500}),('finish',{})]:
                if op in page.session.operations:page.session.process(op,p)
        state=page.session.state;page._sync()
        page.work_size.setValue(1600);page._sync()
        assert processing_info(page.session)['working_size']==[1800,24]
        page.save_dialog('png');dialog=page._export_dialog
        assert dialog.info['working_size']==[1800,24]
        dialog.reject();assert page.session.state is state
    finally:
        wait(lambda:not page.busy);page.close();_application().sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('kind',('peel','integral','plate','paper','wet','cyanotype','platinum'))
def test_mixed_storage_keeps_chemistry_and_roundtrips(tmp_path,kind):
    from film_foundry.app.family_preview import FamilyPreviewSession
    from film_foundry.app.material_precision import APPEARANCE_FIELDS
    from film_foundry.app.material_lab_session import MaterialLabSession
    reference=FamilyPreviewSession(kind);reference.render_target('grayscale',{})
    session=reference.session;state=session.state;baseline=session.preview()
    path=tmp_path/'mixed.npz';api.save(path,state,precision='appearance_float16_v1')
    loaded=api.load(path);selected=APPEARANCE_FIELDS[session.module.FAMILY]
    assert any(a.dtype==np.float16 for k,a in np.load(path).items() if k!='manifest')
    for key,value in state.fields.items():
        if key not in selected:np.testing.assert_array_equal(value,loaded.fields[key])
    copy=MaterialLabSession(kind);copy.state=loaded;copy.group=copy._final_group(loaded)
    assert np.max(np.abs(copy.preview()-baseline))<1/255
    # No progressive loss after the first half-precision rounding.
    api.save(path,loaded,precision='appearance_float16_v1');again=api.load(path)
    for key in state.fields:np.testing.assert_array_equal(again.fields[key],loaded.fields[key])
    group_path=tmp_path/'products.npz';api.save(group_path,copy.group,precision='appearance_float16_v1')
    group=api.load(group_path)
    for body in group.bodies:assert np.isfinite(api.observe(body)).all()


def test_storage_preference_persists_and_invalidates_result_cache(tmp_path):
    from film_foundry.app.family_preview import FamilyPreviewSession
    from film_foundry.app.workspace_jobs import archive_material_result
    from film_foundry.app.material_result_store import load_result
    from film_foundry.app.material_workspace import snapshot_document,save_material_workspace,load_material_workspace
    from film_foundry.core.material_family import ViewingConditions
    from film_foundry.app.material_batch_run import create_batch,load_plan
    ref=FamilyPreviewSession('paper');ref.render_target('grayscale',{});session=ref.session
    auto=archive_material_result(session,tmp_path/'results',ViewingConditions())
    session.storage_precision='full'
    full=archive_material_result(session,tmp_path/'results',ViewingConditions())
    assert full['medium']!=auto['medium']
    loaded,_,_=load_result(full['artifacts']['result']);assert loaded.storage_precision=='full'
    np.testing.assert_array_equal(loaded.preview(),session.preview())
    save_material_workspace(tmp_path/'saved.ffworkspace',[snapshot_document(session,{})])
    assert load_material_workspace(tmp_path/'saved.ffworkspace').documents[0].session.storage_precision=='full'
    recipe=session.recipe()
    plan=create_batch('paper',[tmp_path/'later.png'],tmp_path/'batch',recipe['material'],[(s['operation'],s['parameters']) for s in recipe['steps']],ViewingConditions(),storage_precision='full')
    assert load_plan(plan)[1]['settings']['storage_precision']=='full'

