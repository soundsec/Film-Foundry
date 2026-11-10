"""Finite transfer stock, explicit stabilization, archives and real UI entry."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
import json
import numpy as np
import pytest
from film_foundry.core import peel_apart as peel, integral_instant as instant, transfer_inventory as inv
from film_foundry.app import material_families as api
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.core.material_family import ViewingConditions

IMAGE=np.repeat(np.tile(np.linspace(.01,.8,29,dtype=np.float32),(19,1))[...,None],3,axis=-1)


def contact(material=None,**spread):
    return peel.spread(peel.expose(IMAGE,material or peel.PeelMaterial(),group_id='finite',seed=21),**spread)


def sealed(material=None,**spread):
    return instant.spread(instant.eject(instant.expose(IMAGE,material or instant.InstantMaterial(),group_id='sealed',seed=21)),**spread)


def same(a,b,atol=2e-6):
    assert a.fields.keys()==b.fields.keys()
    for key in a.fields:np.testing.assert_allclose(a.fields[key],b.fields[key],rtol=2e-6,atol=atol,err_msg=key)


def test_capacity_consumes_both_images_and_shortage_never_catches_up():
    low=contact(peel.PeelMaterial(developer_capacity=.2),spread_seconds=0)
    a=peel.advance(low,seconds=300);b=peel.advance(low,seconds=30000)
    full=peel.advance(contact(spread_seconds=0),seconds=30000)
    for state in (a,b,full):
        f=state.fields;inv.validate(f,f['remaining'].shape,state.material)
        np.testing.assert_allclose(f['developer_used'],f['received']+f['transfer_loss']+f['developed_donor'],atol=2e-6)
    assert b.fields['developer_used'].max()<=.200001
    assert b.fields['received'].mean()<full.fields['received'].mean()*.25
    assert b.fields['developed_donor'].mean()<full.fields['developed_donor'].mean()*.25
    assert b.fields['developer_available'].max()<1e-6
    empty=peel.advance(contact(reagent_load=0),seconds=100)
    assert not np.any(empty.fields['received']) and not np.any(empty.fields['developed_donor'])
    inactive=peel.advance(contact(reagent_decay=1),seconds=10000)
    assert inactive.fields['developer_inactive'].mean()>0
    assert inactive.fields['developer_used'].mean()<full.fields['developer_used'].mean()


def test_dark_partition_and_future_environment_preserve_three_budgets():
    base=contact(peel.PeelMaterial(neutralizer_capacity=1.4,neutralization_delay_seconds=35,neutralization_seconds=60),
                 reagent_load=.8,spread_seconds=8,coverage=.9,contact=.75,roller_streaks=.2,reagent_leak=.2)
    one=peel.advance(base,seconds=160)
    split=peel.advance(peel.advance(peel.advance(base,seconds=17),seconds=43),seconds=100)
    same(one,split,4e-6)
    part=peel.advance(base,seconds=20)
    warm=peel.environment(part,temperature_c=30)
    for key in part.fields:np.testing.assert_array_equal(part.fields[key],warm.fields[key])
    slow=peel.advance(part,seconds=30);fast=peel.advance(warm,seconds=30)
    assert fast.fields['neutralization_clock'].mean()>slow.fields['neutralization_clock'].mean()
    assert fast.fields['alkali_neutralized'].mean()>slow.fields['alkali_neutralized'].mean()
    inv.validate(fast.fields,fast.fields['remaining'].shape,fast.material)
    untouched=peel.advance(contact(contact=0),seconds=1000)
    assert not np.any(untouched.fields['developer_used'])


def test_light_competes_without_creating_developer_and_long_steps_arrest():
    material=instant.InstantMaterial(neutralization_delay_seconds=40,neutralization_seconds=50)
    base=sealed(material,light_protection=.1,ambient_light=3,spread_seconds=5)
    one=instant.advance(base,seconds=1000)
    split=base
    for seconds in (10,20,25,100):split=instant.advance(split,seconds=seconds)
    same(one,split,2e-4)
    assert one.stage==split.stage=='stable'
    f=one.fields
    np.testing.assert_allclose(f['remaining']+f['immobile']+f['received']+f['transfer_loss'],1,atol=2e-6)
    np.testing.assert_allclose(f['developer_used'],f['received']+f['transfer_loss']+f['developed_donor'],atol=2e-6)
    assert f['immobile'].mean()>base.fields['immobile'].mean()
    dark=instant.advance(sealed(material),seconds=1000)
    assert dark.fields['received'].mean()>f['received'].mean()


def test_inadequate_acid_requires_explicit_finish_and_observation_is_readonly(tmp_path):
    base=sealed(instant.InstantMaterial(neutralizer_capacity=.2))
    active=instant.stabilize(base)
    assert active.stage=='reacting' and active.fields['alkali_remaining'].mean()>.7
    assert 'finish' in instant.available_operations(active)
    with pytest.raises(ValueError):instant.products(active)
    final=instant.finish(active);group=instant.products(final)
    assert json.loads(group.provenance_json)['completion']=='finished'
    path=tmp_path/'sealed.npz';api.save(path,group);restored=api.load(path)
    body=restored.bodies[0];before={k:v.copy() for k,v in body.fields.items()}
    view=ViewingConditions(include_border=False)
    a=api.observe(body,view,chunk_pixels=0);b=api.observe(body,view,chunk_pixels=80)
    np.testing.assert_array_equal(a,b)
    for key in before:np.testing.assert_array_equal(before[key],body.fields[key])
    assert instant.stabilize(sealed()).stage=='stable'
    assert instant.stabilize(sealed(contact=0)).stage=='reacting'
    assert instant.stabilize(sealed(contact=1e-8)).stage=='reacting'
    assert np.isfinite(instant.preview(instant.expose(IMAGE,group_id='preview'),view)).all()


def test_intermediate_save_negative_and_separate_cleanup(tmp_path):
    m=peel.PeelMaterial(transfer='silver_compound',donor_mode='silver_negative',developer_capacity=.3)
    base=peel.advance(contact(m,reagent_load=.7),seconds=45)
    path=tmp_path/'transfer.npz';api.save(path,base)
    advanced=peel.advance(base,seconds=150)
    same(advanced,peel.advance(api.load(path),seconds=150),0)
    separated=peel.peel(advanced);negative=peel.clear_negative(separated,strength=.7,marks=.3)
    for key in inv.FIELDS:np.testing.assert_array_equal(negative.fields[key],separated.fields[key])
    group=peel.products(negative);assert group.bodies[1].usable_negative
    assert json.loads(group.provenance_json)['reagent_budget']['developer_used']<=.3
    for body in group.bodies:assert np.isfinite(api.observe(body,ViewingConditions(include_border=False))).all()


def test_block_execution_preserves_results_and_rejects_invented_consumption(monkeypatch):
    from film_foundry.core import material_parallel as parallel
    base=contact(peel.PeelMaterial(developer_capacity=1.,neutralizer_capacity=.7),reagent_leak=.2)
    base=peel.environment(base,light_protection=.3,ambient_light=2)
    serial=peel.advance(base,seconds=200)
    real=parallel.run_rows
    monkeypatch.setattr(parallel,'PARALLEL_MIN_PIXELS',0)
    monkeypatch.setattr(parallel,'run_rows',lambda h,w,work,**kw:real(h,w,work,chunk_pixels=87,workers=3))
    same(serial,peel.advance(base,seconds=200),0)
    fields=dict(serial.fields)
    fields['developer_used']=fields['developer_used']*.5
    fields['developer_available']=fields['developer_available']+serial.fields['developer_used']*.5
    with pytest.raises(ValueError,match='formed image'):replace(serial,fields=fields)


@pytest.mark.parametrize('kind',['peel','integral'])
def test_recipe_and_actual_controls_use_same_load_and_keep_legacy_available(kind):
    from film_foundry.ui.material_lab_window import MaterialLabWindow
    from film_foundry.ui.qt import _application
    from film_foundry.app.transfer_intent import reagent_shortage_parameters
    _application();window=MaterialLabWindow(kind)
    try:
        window.session.image=IMAGE;window.session.process('expose')
        if kind=='integral':window.session.process('eject')
        window._sync();assert window.process_controls['reagent_load'].isEnabled()
        assert window.material_controls['neutralizer_capacity'].value()==window.session.state.material.neutralizer_capacity
        window.shortage_enabled.setChecked(True);window.shortage_strength.setValue(.4)
        from film_foundry.ui import material_workspace_state as drafts
        draft=drafts.capture(window);window.shortage_enabled.setChecked(False);window.shortage_strength.setValue(.9)
        drafts.restore(window,draft)
        assert window.shortage_enabled.isChecked() and window.shortage_strength.value()==.4
        # Execute the real UI parameter compilation synchronously, without a GUI job.
        window._submit=lambda action,*args,**kwargs:action()
        window.process('spread')
        assert json.loads(window.session.state.process_json)['reagent_load']==pytest.approx(.6)
        cap=window.session.capabilities()
        assert cap['reagent_inventory']['active']
        assert cap['effect_intents'][-1]['id']=='reagent_shortage'
        assert cap['effect_intents'][-1]['couplings']
        replay=MaterialLabSession(kind);replay.image=IMAGE;replay.replay_recipe(window.session.recipe())
        same(window.session.state,replay.state,0)
        assert reagent_shortage_parameters(strength=.8,enabled=False)=={'reagent_load':1.}
        if kind=='integral':
            window._sync();assert window.operation_buttons['finish'].isEnabled()
            window.process('finish');assert window.session.group is not None
        old=MaterialLabSession(kind);old.image=IMAGE;old.process('expose',material_version=4 if kind=='peel' else 3)
        if kind=='integral':old.process('eject')
        window.session=old;window._sync()
        assert not window.process_controls['reagent_load'].isEnabled()
        window.process('spread');assert 'developer_available' not in old.state.fields
        assert not old.capabilities()['reagent_inventory']['active']
    finally:window.close()
