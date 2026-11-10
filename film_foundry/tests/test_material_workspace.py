"""Checkpoint continuation, transactional saves and frontend draft ownership."""
import json
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from film_foundry.app import material_workspace as archive
from film_foundry.app.material_lab_session import MaterialLabSession,FAMILIES
from film_foundry.tests.test_material_recipes import formed,image
from film_foundry.tests.test_unified_workspace import panel,wait
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui import material_workspace_state as drafts


def same_state(left,right):
    assert left.group_id==right.group_id and left.seed==right.seed and left.stage==right.stage
    assert left.elapsed_seconds==right.elapsed_seconds and left.material==right.material
    for key,value in left.fields.items():np.testing.assert_array_equal(value,right.fields[key])


def test_seven_families_resume_exact_formation_and_source(tmp_path):
    originals=[];next_steps=[]
    for kind in FAMILIES:
        session=MaterialLabSession(kind);session.image=image();session.source_path=str(tmp_path/'missing.png');session.source_name='missing.png'
        session.storage_precision='full'
        recipe=formed(kind).recipe();split=max(1,len(recipe['steps'])//2)
        for step in recipe['steps'][:split]:session.process(step['operation'],step['parameters'],recipe['material'],recipe['seed'])
        originals.append(session);next_steps.append(recipe['steps'][split:])
    # Empty, source-only and final-group-only documents are also valid checkpoints.
    empty=MaterialLabSession('plate');source=MaterialLabSession('integral');source.image=image()
    final=formed('peel');final.state=None;final.image=None
    final.storage_precision='full'
    path=tmp_path/'materials.ffworkspace'
    archive.save_material_workspace(path,[archive.snapshot_document(s,{'pending':17}) for s in [*originals,empty,source,final]],active=6)
    restored=archive.load_material_workspace(path)
    assert restored.active==6 and len(restored.documents)==10
    for before,document,remaining in zip(originals,restored.documents,next_steps):
        after=document.session;same_state(before.state,after.state)
        np.testing.assert_array_equal(before.image,after.image)
        assert before.source_path==after.source_path and document.draft=={'pending':17}
        for step in remaining:
            before.process(step['operation'],step['parameters']);after.process(step['operation'],step['parameters'])
        same_state(before.state,after.state)
        assert before.recipe()==after.recipe()
    assert restored.documents[7].session.state is None and restored.documents[7].session.image is None
    np.testing.assert_array_equal(restored.documents[8].session.image,source.image)
    assert restored.documents[9].session.group.group_id==final.group.group_id
    np.testing.assert_array_equal(restored.documents[9].session.preview(),final.preview())


def test_prepared_plate_retains_optical_exposure_without_source_file(tmp_path):
    source=formed('wet');medium=tmp_path/'wet.npz';source.save_products(medium)
    session=MaterialLabSession('plate');session.open_medium(medium,long_edge=0)
    for op in ('prepare','polish','sensitize'):session.process(op,seed=31)
    path=tmp_path/'optical.ffworkspace'
    archive.save_material_workspace(path,[archive.snapshot_document(session,{})]);medium.unlink()
    restored=archive.load_material_workspace(path).documents[0].session
    assert restored.optical_source is None and restored.optical_reference is not None
    session.process('expose');restored.process('expose');same_state(session.state,restored.state)
    record=json.loads(restored.state.process_json)['optical_cascade']['steps'][-1]
    assert record['source']['group_id']==source.group.group_id
    assert record['target']['group_id']==restored.state.group_id
    restored.replay_recipe(session.recipe())
    for key,value in session.state.fields.items():np.testing.assert_array_equal(value,restored.state.fields[key])
    assert json.loads(restored.state.process_json)['optical_cascade']['steps'][-1]['source']==record['source']


def test_failed_save_preserves_previous_package_and_foreign_files(tmp_path,monkeypatch):
    document=archive.snapshot_document(formed('paper'),{'pending':1});path=tmp_path/'safe.ffworkspace'
    archive.save_material_workspace(path,[document])
    with monkeypatch.context() as patch:
        def fail(*args,**kwargs):raise OSError('disk failure')
        patch.setattr(archive,'atomic_savez',fail)
        with pytest.raises(OSError,match='disk failure'):archive.save_material_workspace(path,[archive.snapshot_document(formed('plate'),{})])
    loaded=archive.load_material_workspace(path).documents[0]
    same_state(loaded.session.state,document.session.state)
    assert loaded.draft=={'pending':1} and not list(tmp_path.glob('.ff-*'))
    from film_foundry.core import atomic_io
    replace=atomic_io.os.replace
    with monkeypatch.context() as patch:
        def fail_commit(source,target):
            if Path(source).name.startswith('.ff-stage-') and target==path:raise OSError('commit failure')
            return replace(source,target)
        patch.setattr(atomic_io.os,'replace',fail_commit)
        with pytest.raises(OSError,match='commit failure'):archive.save_material_workspace(path,[document])
    same_state(archive.load_material_workspace(path).documents[0].session.state,document.session.state)
    foreign=path/'keep.txt';foreign.write_text('unrelated')
    with pytest.raises(ValueError,match='unrelated'):archive.save_material_workspace(path,[document])
    assert foreign.read_text()=='unrelated'


def test_workspace_reopens_seven_drafts_without_replacing_live_pages(panel,tmp_path,monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox,'warning',lambda *args:None)
    saved={};originals=[]
    for kind in FAMILIES:
        page=panel._show_material_workspace(kind);originals.append(page)
        page.session=formed(kind);page._restore_controls();page._sync()
        page.view_ev.setValue(.7);page.work_size.setValue(900)
        page.set_workspace_layout('studio' if kind in ('peel','paper') else 'compact')
        if kind in ('peel','integral','plate'):page.seconds.setValue(57)
        else:page.operation_controls['handling']['fingerprints'].setValue(.43)
        if kind=='peel':page.body_choice.setCurrentIndex(1)
        page.inspector.histogram_button.setChecked(True);page.inspector.histogram.logarithmic=True
        page.preview_timer.stop();saved[kind]=drafts.capture(page)
    # A pending optical choice is a draft, separate from the already loaded exposure.
    page=originals[0]
    pending=tmp_path/'pending.npz';formed('paper').save_products(pending)
    from film_foundry.app.optical_cascade import source_choices
    page.choose_optical_source(str(pending),choices=list(source_choices(pending)))
    page.optical_panel.ev.setValue(1.25);saved['peel']=drafts.capture(page)
    path=tmp_path/'ui.ffworkspace';files=panel._workspace_files
    assert files.save_to(path);wait(lambda:not files.busy);assert files.last_error is None
    assert files.open_from(path);wait(lambda:not files.busy);assert files.last_error is None
    wait(lambda:not panel.workspace_host.busy_pages())
    assert len(panel._material_windows)==14
    loaded=[page for page in panel._material_windows.values() if page not in originals]
    for page in loaded:
        assert page.last_error is None
        actual=drafts.capture(page)
        if actual.get('pending_optical'):
            assert Path(actual['pending_optical']['path']).is_file()
            actual['pending_optical']['path']=saved[page.kind]['pending_optical']['path']
        assert actual==saved[page.kind]
        same_state(page.session.state,originals[list(FAMILIES).index(page.kind)].session.state)
    assert files.path is None  # Imported into a populated workspace: next save is explicit.
    # Reject a later invalid draft before mounting any of the earlier candidates.
    manifest=path/'workspace.json';value=json.loads(manifest.read_text('utf-8'))
    value['documents'][-1]['draft']['values']['not_a_control']=1
    manifest.write_text(json.dumps(value),encoding='utf-8')
    before=list(panel._material_windows.values())
    assert files.open_from(path);wait(lambda:not files.busy)
    assert files.last_error and list(panel._material_windows.values())==before


def test_batch_drafts_restore_without_loading_originals(panel,tmp_path,monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox,'warning',lambda *args:None)
    from film_foundry.ui.material_batch_dialog import MaterialBatchDialog
    from film_foundry.ui.sheet_lab_window import SheetBatchWindow
    originals=[]
    from PIL import Image
    paths=[str(tmp_path/'source1.png'),str(tmp_path/'source2.png')]
    for path in paths:Image.fromarray(np.uint8(image()*255)).save(path)
    for kind,factory in (('integral',MaterialBatchDialog),('cyanotype',SheetBatchWindow)):
        page=factory(kind,paths,load_source=False);page.session=formed(kind);page._sync()
        page.material_controls['half_exposure'].setValue(1.7)
        panel._mount_material_workspace(page,'batch:'+kind);page.preview_timer.stop();originals.append(page)
    files=panel._workspace_files;path=tmp_path/'batch.ffworkspace'
    assert files.save_to(path);wait(lambda:not files.busy);assert files.last_error is None
    for source_path in paths:Path(source_path).unlink()
    assert files.open_from(path);wait(lambda:not files.busy);assert files.last_error is None
    wait(lambda:not panel.workspace_host.busy_pages())
    loaded=[page for page in panel._material_windows.values() if page not in originals]
    assert len(loaded)==2
    for page in loaded:
        assert page.last_error is None and page._planning_batch and len(page.paths)==2
        assert page.material_controls['half_exposure'].value()==1.7
        assert page.session.image is not None


def test_close_waits_for_workspace_commit(panel,tmp_path,monkeypatch):
    import film_foundry.ui.material_workspace_io as io
    panel._show_material_workspace('plate');gate=Event();save=io.save_material_workspace
    monkeypatch.setattr(QtWidgets.QMessageBox,'question',lambda *_:QtWidgets.QMessageBox.StandardButton.Yes)
    def held(*args,**kwargs):gate.wait(5);return save(*args,**kwargs)
    monkeypatch.setattr(io,'save_material_workspace',held)
    path=tmp_path/'closing.ffworkspace'
    try:
        assert panel._workspace_files.save_to(path)
        assert not panel.root.qt.close() and panel._workspace_exit_pending
    finally:gate.set()
    wait(lambda:not panel.root.winfo_exists())
    assert len(archive.load_material_workspace(path).documents)==1
