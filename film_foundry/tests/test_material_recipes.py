from dataclasses import replace
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PIL import Image
import pytest
from film_foundry.app.material_lab_session import MaterialLabSession,FAMILIES
from film_foundry.app.material_recipes import load_recipe,run_recipe_file
from film_foundry.app.control import WorkflowControl,WorkflowCancelled
from film_foundry.app import material_families as api
from film_foundry.contracts.material_recipe import KEY,validate_recipe


def image():return np.random.default_rng(13).random((20,28,3),dtype=np.float32)


def formed(kind):
    session=MaterialLabSession(kind);session.image=image()
    sequences={
        'peel':[('expose',{}),('light_leak',{'strength':.12}),('spread',{'reagent_leak':.2}),
                ('advance',{'seconds':20}),('advance',{'seconds':45}),('peel',{}),('handling',{'fingerprints':.3})],
        'integral':[('expose',{}),('eject',{}),('spread',{'light_protection':.4,'solar_warming':3.}),
                    ('advance',{'seconds':20}),('advance',{'seconds':45}),('stabilize',{}),('handling',{'reagent_residue':.2})],
        'plate':[('prepare',{}),('polish',{'streaks':.4}),('sensitize',{}),('expose',{}),
                 ('develop',{'seconds':20}),('develop',{'seconds':15}),('fix',{}),('load_gilding',{}),('gild',{}),('finish',{}),('weather',{'scratches':.2})]}
    if kind in sequences:
        for op,params in sequences[kind]:session.process(op,params,seed=27)
    else:
        session.run_standard(seed=27)
        session.process('handling',{'fingerprints':.2,'reagent_residue':.1})
    return session


@pytest.mark.parametrize('kind',FAMILIES)
def test_recipe_exact_replay_and_immutable_sharing_for_all_families(kind,tmp_path):
    original=formed(kind);recipe=original.recipe()
    path=tmp_path/(kind+'.ffrecipe.json');original.save_recipe(path)
    restored=MaterialLabSession(kind);restored.image=image()
    reports=[];restored.replay_recipe(load_recipe(path),control=WorkflowControl(on_progress=reports.append))
    assert reports[-1].fraction==1 and restored.state.group_id!=original.state.group_id
    assert restored.recipe()==recipe
    assert restored.state.elapsed_seconds==original.state.elapsed_seconds
    for key,value in original.state.fields.items():np.testing.assert_array_equal(value,restored.state.fields[key])
    for i,body in enumerate(original.group.bodies):
        np.testing.assert_array_equal(original.preview(body_index=i),restored.preview(body_index=i))
        assert any(a is b for a in body.fields.values() for b in original.state.fields.values())
        for field in body.fields.values():
            with pytest.raises(ValueError):field.setflags(write=True)
    # Saving/opening a final group retains the complete formation recipe.
    archive=tmp_path/'products.npz';original.save_products(archive)
    loaded=MaterialLabSession(kind);loaded.load(archive)
    assert loaded.recipe()==recipe and loaded.image is None
    with pytest.raises(ValueError,match='曝光'):loaded.replay_recipe(recipe)


def test_partial_save_resume_and_old_history_is_not_invented(tmp_path):
    session=MaterialLabSession('integral');session.image=image()
    session.process('expose',seed=19);session.process('eject')
    session.process('spread',{'temperature_c':26.,'solar_warming':5.})
    path=tmp_path/'state.npz';session.save(path)
    resumed=MaterialLabSession('integral');resumed.load(path)
    resumed.process('advance',{'seconds':30});resumed.process('advance',{'seconds':45});resumed.process('stabilize')
    recipe=resumed.recipe();spread=recipe['steps'][2]['parameters']
    assert spread['temperature_c']==26 and spread['solar_warming']==5 and spread['coverage']==1
    assert [s['parameters']['seconds'] for s in recipe['steps'] if s['operation']=='advance']==[30,45]
    session.replay_recipe(recipe)
    for key,value in session.state.fields.items():np.testing.assert_array_equal(value,resumed.state.fields[key])
    snapshot=json.loads(session.state.process_json);del snapshot[KEY]
    session.state=replace(session.state,process_json=json.dumps(snapshot));session.process('handling')
    with pytest.raises(ValueError,match='完整'):session.recipe()


@pytest.mark.parametrize('change',('family','version','stage','unknown','range','type','nan','array'))
def test_invalid_replay_keeps_existing_result(change):
    session=formed('integral');before=session.state;group=session.group;recipe=session.recipe()
    if change=='family':recipe['family']='missing'
    elif change=='version':recipe['material_version']=99
    elif change=='stage':recipe['steps'][0]['operation']='stabilize'
    elif change=='unknown':recipe['steps'][2]['parameters']['typo']=1
    elif change=='range':recipe['steps'][2]['parameters']['light_protection']=2
    elif change=='type':recipe['steps'][2]['parameters']['solar_warming']='wrong'
    elif change=='nan':recipe['steps'][2]['parameters']['solar_warming']=float('nan')
    elif change=='array':recipe['steps'][2]['parameters']['coverage']=np.ones((20,28))
    with pytest.raises(ValueError):session.replay_recipe(recipe)
    assert session.state is before and session.group is group


def test_cancel_between_steps_does_not_publish_partial_state():
    session=formed('paper');before=session.state;group=session.group;reports=[]
    control=WorkflowControl(on_progress=reports.append,cancel_requested=lambda:len(reports)>=2)
    with pytest.raises(WorkflowCancelled):session.replay_recipe(session.recipe(),control=control)
    assert session.state is before and session.group is group


def test_recipe_bounds_and_strict_json(tmp_path):
    recipe=formed('paper').recipe();recipe['steps']*=513
    with pytest.raises(ValueError,match='512'):validate_recipe(recipe)
    path=tmp_path/'bad.json';path.write_text('{"model":"one","model":"two"}')
    with pytest.raises(ValueError,match='Duplicate'):load_recipe(path)
    recipe=formed('paper').recipe();recipe['material']['name']='x'*(512*1024)
    with pytest.raises(ValueError,match='KiB'):validate_recipe(recipe)


@pytest.mark.parametrize('kind',('paper','peel'))
def test_external_readonly_views_are_detached_and_old_snapshots_stay_unchanged(kind):
    session=formed(kind);state=session.state;key=next(iter(state.fields))
    external=state.fields[key].copy();view=external.view();view.setflags(write=False)
    detached=replace(state,fields={**state.fields,key:view})
    expected=external.copy();external[:]=0
    np.testing.assert_array_equal(detached.fields[key],expected)
    with pytest.raises(ValueError):detached.fields[key].setflags(write=True)
    previous={k:v.copy() for k,v in state.fields.items()}
    session.process('handling',{'fingerprints':.7})
    for k,v in previous.items():np.testing.assert_array_equal(v,state.fields[k])
    # A metadata-only replacement shares the exact storage, never copies pools.
    metadata=replace(state,process_json=state.process_json)
    assert all(metadata.fields[k] is v for k,v in state.fields.items())


def test_optical_source_recipe_replay_preserves_source_and_link(tmp_path):
    source=formed('wet');path=tmp_path/'wet.npz';source.save_products(path)
    session=MaterialLabSession('paper');session.open_medium(path,long_edge=0);session.run_standard(seed=6)
    original=session.preview();source_image=session.image.copy();recipe=session.recipe()
    session.replay_recipe(recipe)
    np.testing.assert_array_equal(session.preview(),original)
    np.testing.assert_array_equal(session.image,source_image)
    trace=json.loads(session.state.process_json)['optical_cascade']
    assert trace['steps'][-1]['source']['group_id']==source.group.group_id


def test_cli_replay_tiff_and_explicit_multi_body_endpoint(tmp_path):
    from film_foundry.app.cli import main
    from film_foundry.core.io_utils import cv_imread_unicode
    source=tmp_path/'source.png';Image.fromarray(np.uint8(image()*255)).save(source)
    session=formed('peel');recipe=tmp_path/'peel.ffrecipe.json';session.save_recipe(recipe)
    target=tmp_path/'result.tif'
    args=['material-replay',str(source),str(recipe),str(target),'--stop','image','--bit-depth','16','--progress']
    assert main(args)==1 and not target.exists()
    assert main([*args,'--body-role','receiver'])==0
    assert cv_imread_unicode(target).dtype==np.uint16
    group=api.load(target.with_name('result.formed.ffmaterial.npz'))
    assert len(group.bodies)==2 and KEY in json.loads(group.provenance_json)['process']
    assert main(['material-replay',str(source),str(recipe),str(tmp_path/'state.npz'),'--stop','state'])==0
    # A recipe named like the output sidecar must not be overwritten.
    collision=tmp_path/'result.tif.json';session.save_recipe(collision)
    with pytest.raises(ValueError,match='overwrite'):run_recipe_file(source,collision,target,stop='image',body_role='receiver')
    assert load_recipe(collision)==session.recipe()


def test_recipe_export_failure_restores_whole_output_set(tmp_path,monkeypatch):
    session=formed('paper');recipe=tmp_path/'paper.json';session.save_recipe(recipe)
    source=tmp_path/'input.png';Image.fromarray(np.uint8(image()*255)).save(source)
    target=tmp_path/'result.png';medium=tmp_path/'result.formed.ffmaterial.npz';sidecar=tmp_path/'result.png.json'
    for path in (target,medium,sidecar):path.write_bytes(b'previous')
    def fail(self,path,*args,**kwargs):
        path.write_bytes(b'partial')
        raise OSError('encoder failed')
    monkeypatch.setattr(MaterialLabSession,'export_image',fail)
    with pytest.raises(OSError,match='encoder failed'):run_recipe_file(source,recipe,target,stop='image')
    assert all(p.read_bytes()==b'previous' for p in (target,medium,sidecar))


@pytest.mark.parametrize('kind',('integral','cyanotype'))
def test_both_window_types_save_and_apply_recipe(kind,tmp_path,monkeypatch):
    from film_foundry.ui.material_lab_window import create_material_window
    from film_foundry.ui.qt import QtWidgets
    from film_foundry.tests.test_material_windows import wait
    monkeypatch.setenv('FILM_FOUNDRY_CATALOG',str(tmp_path/'catalog.json'))
    window=create_material_window(kind)
    try:
        window.show();QtWidgets.QApplication.instance().processEvents()
        assert not window.save_recipe_action.isEnabled() and not window.apply_recipe_action.isEnabled()
        window.session=formed(kind);window._sync();before=window.session.preview()
        assert sum(window.config_tabs.widget(i).isVisible() for i in range(window.config_tabs.count()))==1
        assert window.save_recipe_action.isEnabled() and window.apply_recipe_action.isEnabled()
        path=tmp_path/'saved.ffrecipe.json'
        monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *a,**k:(str(path),''))
        window.save_recipe_dialog();wait(window)
        old=window.session.state
        monkeypatch.setattr(QtWidgets.QFileDialog,'getOpenFileName',lambda *a,**k:(str(path),''))
        monkeypatch.setattr(window,'confirm_replace',lambda:True)
        window.apply_recipe_dialog();wait(window)
        assert window.session.state is not old
        assert sum(window.config_tabs.widget(i).isVisible() for i in range(window.config_tabs.count()))==1
        np.testing.assert_array_equal(window.session.preview(),before)
    finally:window.close()
