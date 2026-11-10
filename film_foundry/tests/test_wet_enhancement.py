"""V4 wet intervals and independent finite intensifier, including real UI."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
import json
import numpy as np
import pytest
from film_foundry.core import wet_plate as w,wet_plate_process as chemistry
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.app import material_families as api
from film_foundry.core.material_family import ViewingConditions

IMAGE=np.repeat(np.tile(np.linspace(0.,.8,31,dtype=np.float32),(21,1))[...,None],3,axis=-1)


def fixed(material=None,image=IMAGE):
    return w.fix(w.develop(w.expose(image,material or w.WetPlateMaterial(),group_id='wet4',seed=17)))


def same(a,b,atol=2e-6):
    assert a.fields.keys()==b.fields.keys()
    for key in a.fields:np.testing.assert_allclose(a.fields[key],b.fields[key],atol=atol,rtol=2e-6,err_msg=key)


def test_air_drying_and_bath_rewetting_have_independent_history():
    initial=w.sensitize(w.prepare(IMAGE.shape[:2],group_id='moisture'))
    dried=w.wait(initial,seconds=450,humidity=0)
    one=w.sensitize(dried,seconds=80,coverage=.65,temperature_c=25)
    split=w.sensitize(w.sensitize(dried,seconds=30,coverage=.65,temperature_c=25),seconds=50,coverage=.65,temperature_c=25)
    same(one,split)
    assert one.fields['moisture'].mean()>dried.fields['moisture'].mean()
    assert np.all(one.fields['collodion_access']<=dried.fields['collodion_access'])
    fresh=w.develop(w.expose_surface(initial,image=IMAGE),seconds=80)
    damaged=w.develop(w.expose_surface(one,image=IMAGE),seconds=80)
    assert damaged.fields['silver'].sum()<fresh.fields['silver'].sum()
    assert np.all(damaged.fields['silver']<=fresh.fields['silver']+1e-6)
    washed=w.wash(w.fix(damaged),seconds=180)
    assert washed.fields['moisture'].min()>.99
    np.testing.assert_array_equal(washed.fields['collodion_access'],damaged.fields['collodion_access'])
    # Zero contact changes air history, never consumes silver or sensitizes.
    none=w.sensitize(dried,seconds=100,coverage=0)
    np.testing.assert_array_equal(none.fields['halide'],dried.fields['halide'])


def test_development_and_fixing_use_integrated_wet_contact():
    exposed=w.expose(IMAGE,group_id='split')
    exposed=w.wait(exposed,seconds=95)
    whole=w.develop(exposed,seconds=90,coverage=.75,pour_uniformity=.6,temperature_c=24)
    split=w.develop(w.develop(exposed,seconds=20,coverage=.75,pour_uniformity=.6,temperature_c=24),seconds=70,coverage=.75,pour_uniformity=.6,temperature_c=24)
    same(whole,split)
    contact=np.ones(IMAGE.shape[:2],np.float32);contact[:,:10]=0
    partial=w.fix(whole,seconds=45,coverage=contact)
    np.testing.assert_array_equal(partial.fields['halide'][:,:10],whole.fields['halide'][:,:10])
    assert partial.fields['halide'][:,10:].sum()<whole.fields['halide'][:,10:].sum()
    for state in (whole,partial):
        f=state.fields
        np.testing.assert_allclose(f['silver']+f['halide']+f['removed_silver'],exposed.fields['halide'],atol=2e-6)
        np.testing.assert_allclose(f['external_silver']+f['external_deposit']+f['external_removed'],f['external_supplied'],atol=2e-6)


def test_intensification_is_finite_seeded_and_split_consistent():
    base=w.load_enhancer(fixed(),silver_load=.3)
    whole=w.intensify(base,seconds=100,coverage=.8,pour_uniformity=.7)
    split=w.intensify(w.intensify(base,seconds=35,coverage=.8,pour_uniformity=.7),seconds=65,coverage=.8,pour_uniformity=.7)
    same(whole,split)
    exhausted=w.intensify(base,seconds=86400)
    repeated=w.intensify(exhausted,seconds=86400)
    np.testing.assert_allclose(exhausted.fields['enhancement_deposit'],repeated.fields['enhancement_deposit'],atol=2e-6)
    assert exhausted.fields['enhancement_deposit'].mean()>.2
    assert np.all(exhausted.fields['enhancement_deposit']<=base.fields['enhancement_supplied']+1e-6)
    for state in (whole,repeated):
        chemistry.validate(state.fields)
        for key in ('silver','halide','external_deposit','external_supplied'):np.testing.assert_array_equal(state.fields[key],base.fields[key])
    more=w.load_enhancer(repeated,silver_load=1)
    assert more.fields['enhancement_supplied'].max()>1
    blank=w.intensify(w.load_enhancer(fixed(image=np.zeros_like(IMAGE))),seconds=86400)
    assert not np.any(blank.fields['enhancement_deposit'])
    fog=w.intensify(w.load_enhancer(fixed(w.WetPlateMaterial(enhancement_nucleation=.1),np.zeros_like(IMAGE))),seconds=120)
    assert fog.fields['enhancement_deposit'].mean()>.05


def test_wash_ends_bath_removes_only_available_silver_and_keeps_finished_optics():
    base=w.load_enhancer(fixed(),silver_load=.7)
    partial=w.intensify(base,seconds=20)
    assert w.wash(partial,seconds=0).stage=='enhancing'
    assert w.fix(partial,seconds=0).stage=='enhancing'
    assert w.fix(partial,activity=0).stage=='enhancing'
    assert w.fix(partial,coverage=0).stage=='enhancing'
    washed=w.wash(partial,seconds=120)
    assert washed.stage=='washed'
    np.testing.assert_array_equal(washed.fields['enhancement_deposit'],partial.fields['enhancement_deposit'])
    assert washed.fields['enhancement_silver'].sum()<partial.fields['enhancement_silver'].sum()
    chemistry.validate(washed.fields)
    with pytest.raises(ValueError):w.intensify(washed)
    body=w.products(washed).bodies[0]
    stronger=api.observe(body,ViewingConditions(include_border=False))
    weaker=api.observe(w.products(w.wash(fixed())).bodies[0],ViewingConditions(include_border=False))
    assert stronger.mean()<weaker.mean()
    positive=fixed(w.WetPlateMaterial(support='tintype'))
    a=w.wash(positive);b=w.wash(w.intensify(w.load_enhancer(positive),seconds=120))
    assert w.observe(w.products(b).bodies[0]).mean()>w.observe(w.products(a).bodies[0]).mean()


def test_exposure_and_later_light_filter_only_their_own_dose():
    source=w.expose(IMAGE,group_id='dose')
    later=w.handling(w.wait(source,seconds=80),reagent_residue=.9)
    zero=w.light_leak(later,strength=0)
    np.testing.assert_array_equal(zero.fields['dose'],source.fields['dose'])
    np.testing.assert_array_equal(zero.fields['latent'],source.fields['latent'])
    clean=w.wait(source,seconds=80)
    lit=w.light_leak(later,strength=.4);unfiltered=w.light_leak(clean,strength=.4)
    assert (lit.fields['dose']-source.fields['dose']).sum()<(unfiltered.fields['dose']-source.fields['dose']).sum()
    assert np.all(lit.fields['dose']>=source.fields['dose'])


def test_archive_recipe_capabilities_and_readonly_final(tmp_path):
    session=MaterialLabSession('wet');session.image=IMAGE
    for op in ('prepare','sensitize','expose','develop','fix','load_enhancer'):session.process(op,seed=18)
    session.process('intensify',{'seconds':27})
    path=tmp_path/'wet.npz';session.save(path)
    resumed=api.load(path)
    session.process('intensify',{'seconds':80})
    same(session.state,w.intensify(resumed,seconds=80),0)
    cap=session.capabilities();assert cap['current_version']==4
    assert cap['effect_intents'][0]['id']=='enhancement_silver_supply'
    replay=MaterialLabSession('wet');replay.image=IMAGE;replay.replay_recipe(session.recipe());same(session.state,replay.state,0)
    session.process('wash');session.save_products(path);group=api.load(path);body=group.bodies[0]
    before={k:v.copy() for k,v in body.fields.items()}
    view=ViewingConditions(include_border=False)
    np.testing.assert_array_equal(api.observe(body,view,chunk_pixels=0),api.observe(body,view,chunk_pixels=62,workers=2))
    from film_foundry.app.optical_cascade import project_medium
    from film_foundry.contracts.optical_cascade import OpticalRelay
    assert np.isfinite(project_medium(group,OpticalRelay(mode='transmission'))).all()
    for key in before:np.testing.assert_array_equal(body.fields[key],before[key])


def test_real_ui_intent_draft_and_legacy_guards():
    from film_foundry.ui.sheet_lab_window import SheetLabWindow
    from film_foundry.ui import material_workspace_state as drafts
    from film_foundry.app.wet_plate_intent import silver_supply_parameters
    window=SheetLabWindow('wet')
    try:
        window.session.image=IMAGE;window._submit=lambda action,*a,**k:action()
        for op in ('prepare','sensitize','expose','develop','fix'):window.process(op)
        window._sync();assert 'load_enhancer' in window.session.operations
        window.enhancement_intent['enabled'].setChecked(True);window.enhancement_intent['strength'].setValue(.6)
        draft=drafts.capture(window);window.enhancement_intent['enabled'].setChecked(False);drafts.restore(window,draft)
        assert window.enhancement_intent['enabled'].isChecked()
        assert not window.operation_controls['load_enhancer']['silver_load'].isEnabled()
        window.process('load_enhancer')
        assert window.session.recipe()['steps'][-1]['parameters']['silver_load']==.6
        window.process('intensify');window.process('wash');window._sync()
        assert window.inspection.findData('collodion_access')>=0
        old=MaterialLabSession('wet');old.image=IMAGE;old.process('prepare',material_version=3);window.session=old;window._sync()
        assert not window.material_controls['wetting_seconds'].isEnabled()
        assert not window.operation_controls['sensitize']['coverage'].isEnabled()
        window.operation_controls['sensitize']['coverage'].setValue(.2);window.process('sensitize')
        assert old.state.schema_version==3 and 'collodion_access' not in old.state.fields
        assert silver_supply_parameters(strength=.8,enabled=False)=={'silver_load':0.}
    finally:window.close()


def test_bounded_rows_and_silver_ledger_reject_invented_deposit(monkeypatch):
    from film_foundry.core import material_parallel as parallel
    base=w.load_enhancer(fixed(),silver_load=.5)
    expected=w.intensify(base,seconds=80,coverage=.7)
    original=parallel.run_rows
    monkeypatch.setattr(parallel,'PARALLEL_MIN_PIXELS',0)
    monkeypatch.setattr(chemistry,'run_rows',lambda h,w,action,**kwargs:original(h,w,action,chunk_pixels=93,workers=3))
    same(expected,w.intensify(base,seconds=80,coverage=.7),0)
    with pytest.raises(ValueError,match='conserved'):
        replace(base,fields={**base.fields,'enhancement_deposit':np.ones_like(base.fields['silver'])})
