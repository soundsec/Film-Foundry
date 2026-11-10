"""Necessary checks for the v4 finite gold, attrition and explicit endpoint."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
import json
import numpy as np
import pytest
from film_foundry.core import silver_plate as p,plate_gold as gold
from film_foundry.app import material_families as api
from film_foundry.app.material_lab_session import MaterialLabSession

IMAGE=np.random.default_rng(21).random((23,37,3),dtype=np.float32)


def fixed(material=p.PlateMaterial()):
    return p.fix(p.develop(p.expose(IMAGE,material,group_id='gold',seed=17),seconds=45))


def same(a,b,tol=2e-6):
    assert a.stage==b.stage and a.schema_version==b.schema_version
    for key in a.fields:np.testing.assert_allclose(a.fields[key],b.fields[key],atol=tol,rtol=tol,err_msg=key)


def budget(state):
    f=state.fields;gold.validate(f)
    np.testing.assert_allclose(f['halide']+f['particles']+f['particles_removed']+f['removed_halide'],f['sensitized_silver'],atol=2e-6)


def test_finite_local_gold_exhaustion_reload_and_rinse():
    start=fixed();empty=p.gild(start,seconds=86400)
    assert empty.stage=='gilding' and not empty.fields['gold'].any()
    contact=np.ones(IMAGE.shape[:2],np.float32);contact[:,:10]=0
    charged=p.load_gilding(start,gold_load=.06,coverage=contact)
    done=p.gild(charged,seconds=86400)
    assert done.stage=='gilding' and done.fields['gold'].max()<=.060001
    assert not done.fields['gold'][:,:10].any()
    same(done,p.gild(done,seconds=86400))
    more=p.gild(p.load_gilding(done,gold_load=.8),seconds=86400)
    assert more.fields['gold'].mean()>done.fields['gold'].mean()
    rinsed=p.rinse_gilding(more)
    np.testing.assert_array_equal(more.fields['gold'],rinsed.fields['gold'])
    assert not rinsed.fields['gold_available'].any()
    many=p.load_gilding(p.load_gilding(rinsed,gold_load=1),gold_load=1)
    assert many.fields['gold_supplied'].min()>1
    for state in (charged,done,more,rinsed,many):budget(state)


def test_split_baths_zero_contact_and_lower_target_do_not_undo():
    base=p.load_gilding(fixed(),gold_load=.5)
    whole=p.gild(base,seconds=210,temperature_c=26,coverage=.7)
    split=p.gild(p.gild(base,seconds=50,temperature_c=26,coverage=.7),seconds=160,temperature_c=26,coverage=.7)
    same(whole,split)
    for params in ({'seconds':0},{'activity':0},{'coverage':0}):same(base,p.gild(base,**params),0)
    lower=p.gild(whole,amount=0)
    for key in whole.fields:np.testing.assert_array_equal(whole.fields[key],lower.fields[key])
    # Equal reactant pools take the finite rational branch, without 0/0.
    from film_foundry.core import sheet_common as s
    blank=p.fix(p.develop(p.expose(np.zeros_like(IMAGE),group_id='equal'),seconds=45))
    equal=p.gild(p.load_gilding(blank,gold_load=.125),amount=.5,seconds=20)
    np.testing.assert_allclose(equal.fields['gold'],.125**2/(1+.125),atol=1e-7)


def test_gold_supports_attachment_but_not_corrosion_and_lost_image_stays_lost():
    bare=fixed();gilded=p.gild(p.load_gilding(bare,gold_load=1),seconds=86400)
    scratched=p.surface(gilded,damage=.35);unbound=p.surface(bare,damage=.35)
    assert scratched.fields['particles'].sum()>unbound.fields['particles'].sum()
    assert scratched.fields['gold'].sum()<gilded.fields['gold'].sum()
    for state in (scratched,unbound):budget(state)
    full=p.surface(gilded,damage=1)
    assert not full.fields['particles'].any() and not full.fields['gold'].any()
    regild=p.gild(p.load_gilding(unbound,gold_load=1),seconds=86400)
    np.testing.assert_array_equal(regild.fields['particles'],unbound.fields['particles'])
    np.testing.assert_array_equal(regild.fields['damage'],unbound.fields['damage'])
    a=p.weather(bare,pitting=.4,edge_tarnish=.5);b=p.weather(gilded,pitting=.4,edge_tarnish=.5)
    for key in ('particles','tarnish','pit_depth'):np.testing.assert_array_equal(a.fields[key],b.fields[key])
    same(scratched,p.surface(scratched),0)
    disabled=fixed(p.PlateMaterial(gold_adhesion=0))
    disabled=p.gild(p.load_gilding(disabled,gold_load=1),seconds=86400)
    np.testing.assert_array_equal(p.surface(disabled,damage=.35).fields['particles'],unbound.fields['particles'])


def test_unfinished_snapshot_residual_salt_and_observation_are_readonly():
    partial=p.fix(p.develop(p.expose(IMAGE,group_id='partial'),seconds=20),seconds=1)
    with pytest.raises(ValueError):p.products(partial)
    final=p.finish(partial);assert final.stage=='finished'
    same(final,replace(partial,stage='finished'),0)
    body=p.products(final).bodies[0];metadata=json.loads(body.presentation_json)
    assert metadata['formation_stage']=='fixing' and not metadata['fixed']
    with pytest.raises(ValueError):p.gild(final)
    snapshot={k:v.copy() for k,v in body.fields.items()}
    np.testing.assert_array_equal(api.observe(body,chunk_pixels=0),api.observe(body,chunk_pixels=74,workers=2))
    from film_foundry.app.optical_cascade import project_medium
    from film_foundry.contracts.optical_cascade import OpticalRelay
    assert np.isfinite(project_medium(p.products(final),OpticalRelay(mode='reflection'))).all()
    for key in snapshot:np.testing.assert_array_equal(body.fields[key],snapshot[key])
    worn=p.surface(final,damage=.4);budget(worn)
    assert worn.fields['halide'].sum()<final.fields['halide'].sum()


def test_archive_resume_recipe_and_capabilities(tmp_path):
    session=MaterialLabSession('plate');session.image=IMAGE
    for op in p.EXPOSURE_STEPS:session.process(op,seed=17)
    session.process('develop',{'seconds':45});session.process('fix');session.process('load_gilding',{'gold_load':.08})
    session.process('gild',{'seconds':29})
    path=tmp_path/'state.npz';session.save(path);restored=api.load(path)
    session.process('gild',{'seconds':84});same(session.state,p.gild(restored,seconds=84),0)
    session.process('finish');session.process('weather',{'scratches':.4,'pitting':.2})
    replay=MaterialLabSession('plate');replay.image=IMAGE;replay.replay_recipe(session.recipe());same(session.state,replay.state,0)
    session.save_products(path);group=api.load(path)
    np.testing.assert_array_equal(api.observe(group.bodies[0]),api.observe(session.group.bodies[0]))
    cap=session.capabilities();assert cap['current_version']==4 and cap['effect_intents'][0]['id']=='gold_supply'
    assert 'gold_adhesion_not_sulfide_barrier' in cap['limitations']


def test_bounded_thread_rows_and_invalid_gold_origin(monkeypatch):
    from film_foundry.core import material_parallel as parallel
    base=p.load_gilding(fixed());expected=p.gild(base,seconds=40)
    original=parallel.run_rows;monkeypatch.setattr(parallel,'PARALLEL_MIN_PIXELS',0)
    monkeypatch.setattr(gold,'run_rows',lambda h,w,action,**kw:original(h,w,action,chunk_pixels=74,workers=3))
    same(expected,p.gild(base,seconds=40),0)
    with pytest.raises(ValueError,match='conserved'):
        replace(base,fields={**base.fields,'gold':np.ones_like(base.fields['gold'])})


def test_actual_ui_intent_draft_and_legacy_guards():
    from film_foundry.ui.material_lab_window import MaterialLabWindow
    from film_foundry.ui import material_workspace_state as drafts
    from film_foundry.app.plate_gold_intent import gold_supply_parameters
    window=MaterialLabWindow('plate')
    try:
        window.session.image=IMAGE;window._submit=lambda action,*a,**k:action()
        for op in (*p.EXPOSURE_STEPS,'develop','fix'):window.process(op)
        window._sync();assert window.workflow.choice.currentData()=='load_gilding'
        window.gold_intent['enabled'].setChecked(True);window.gold_intent['strength'].setValue(.3)
        draft=drafts.capture(window);window.gold_intent['enabled'].setChecked(False);drafts.restore(window,draft)
        assert window.gold_intent['enabled'].isChecked()
        assert not window.operation_controls['load_gilding']['gold_load'].isEnabled()
        manual=p.load_gilding(window.session.state,**gold_supply_parameters(strength=.3))
        window.process('load_gilding');same(manual,window.session.state,0)
        window.process('gild');window.process('finish');window._restore_controls();window._sync()
        assert window.session.recipe()['steps'][-3]['parameters']['gold_load']==.3
        old=MaterialLabSession('plate');old.image=IMAGE
        for op in p.EXPOSURE_STEPS:old.process(op,material_version=3)
        old.process('develop',{'seconds':45});old.process('fix');window.session=old;window._sync()
        assert not window.operation_buttons['load_gilding'].isEnabled()
        assert not window.operation_buttons['finish'].isEnabled()
        assert not window.material_controls['gold_adhesion'].isEnabled()
        window.process('gild');assert old.state.stage=='gilded'
        assert gold_supply_parameters(strength=.7,enabled=False)=={'gold_load':0.}
    finally:window.close()


def test_batch_uses_explicit_gold_and_same_parameters(monkeypatch,tmp_path):
    from film_foundry.ui.material_batch_dialog import MaterialBatchDialog
    from film_foundry.ui.qt import QtWidgets
    from film_foundry.app import workspace_jobs
    window=MaterialBatchDialog('plate',['unused.png'],load_source=False);captured={}
    try:
        window.gold_intent['enabled'].setChecked(True);window.gold_intent['strength'].setValue(.1)
        window.operation_controls['gild']['seconds'].setValue(7)
        monkeypatch.setattr(QtWidgets.QFileDialog,'getExistingDirectory',lambda *a: str(tmp_path))
        def run(kind,paths,output,material,steps,*a,**kw):captured.update(material=material,steps=steps)
        monkeypatch.setattr(workspace_jobs,'process_material_photos',run)
        window._submit=lambda action,*a,**k:action();window.start_batch()
        assert dict(captured['steps'])['load_gilding']['gold_load']==.1
        session=MaterialLabSession('plate');session.image=IMAGE
        for op,parameters in captured['steps']:session.process(op,parameters,captured['material'])
        assert session.state.stage=='finished' and session.group is not None
        assert json.loads(session.group.bodies[0].presentation_json)['formation_stage']=='gilding'
    finally:window.close()
