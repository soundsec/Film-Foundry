"""V4 mechanical allocation, independent cleanup and actual application paths."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
import numpy as np
import pytest
from film_foundry.core import peel_apart as peel,peel_separation as separation,layer_ownership as layers
from film_foundry.core.material_family import ViewingConditions
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.app import material_families as api

IMAGE=np.repeat(np.tile(np.linspace(.01,.8,48,dtype=np.float32),(32,1))[...,None],3,axis=-1)


def contact(**material):
    s=MaterialLabSession('peel');s.image=IMAGE
    s.process('expose',material=material,seed=23)
    s.process('spread');s.process('advance',{'seconds':100})
    return s


def budget(state):
    f=state.fields;r,d=separation.image_amounts(f)
    removed=layers.retained(f['received'],layers.removed(f['receiver_native'],f['receiver_on_donor']))
    removed+=layers.retained(f['immobile']+f['remaining']+f['transfer_loss'],layers.removed(f['donor_native'],f['donor_on_receiver']))
    np.testing.assert_allclose(r+d+removed,1,atol=2e-6,rtol=0)
    return removed


def test_separation_responds_to_material_contact_and_mechanical_conditions():
    state=contact().state
    clean=peel.peel(state)
    np.testing.assert_array_equal(clean.fields['receiver_native'],1)
    assert clean.schema_version>=4 and 'donor_gel' not in state.fields
    material=replace(state.material,interlayer_adhesion=.8,receiver_cohesion=.25,donor_cohesion=.9)
    sticky=replace(state,material=material)
    gentle=peel.peel(sticky,angle_degrees=160,speed_mm_s=5)
    harsh=peel.peel(sticky,angle_degrees=30,speed_mm_s=150)
    assert harsh.fields['receiver_on_donor'].mean()>gentle.fields['receiver_on_donor'].mean()>0
    assert np.std(harsh.fields['receiver_on_donor'])>1e-3
    assert harsh.fields['receiver_on_donor'].mean()>harsh.fields['donor_on_receiver'].mean()
    reversed_strength=peel.peel(replace(sticky,material=replace(material,receiver_cohesion=.9,donor_cohesion=.25)))
    assert reversed_strength.fields['donor_on_receiver'].mean()>reversed_strength.fields['receiver_on_donor'].mean()
    empty=replace(sticky,fields=dict(sticky.fields)|{'contact':np.zeros((32,48),np.float32)})
    np.testing.assert_array_equal(peel.peel(empty).fields['receiver_native'],1)
    budget(harsh)
    with pytest.raises(ValueError):peel.peel(harsh)
    with pytest.raises(ValueError):peel.advance(harsh,seconds=1)
    invalid=dict(harsh.fields);invalid['receiver_native']=np.ones((32,48),np.float32)
    with pytest.raises(ValueError):replace(harsh,fields=invalid)


@pytest.mark.parametrize('target',['receiver','donor'])
def test_independent_cleanup_removes_only_selected_layers(target):
    state=peel.peel(contact(interlayer_adhesion=.8,receiver_cohesion=.3).state)
    marked=peel.handling(state,target=target,fingerprints=.4,reagent_residue=.5)
    other='donor' if target=='receiver' else 'receiver'
    np.testing.assert_array_equal(marked.fields[other+'_residue'],state.fields[other+'_residue'])
    cleaned=peel.clean(marked,target=target,strength=.4,abrasion=.3,marks=.7,angle_degrees=30)
    assert cleaned.fields[target+'_residue'].sum()<marked.fields[target+'_residue'].sum()
    untouched=('donor_native','receiver_on_donor') if target=='receiver' else ('receiver_native','donor_on_receiver')
    for key in (*untouched,other+'_residue',*peel.state_fields(4)):
        np.testing.assert_array_equal(cleaned.fields[key],marked.fields[key],err_msg=key)
    assert budget(cleaned).sum()>budget(marked).sum()
    other_index=1 if target=='receiver' else 0
    np.testing.assert_array_equal(api.observe(peel.products(marked).bodies[other_index]),api.observe(peel.products(cleaned).bodies[other_index]))


def test_negative_clearing_accumulates_and_observation_never_regenerates(monkeypatch):
    s=contact(transfer='silver_compound',donor_mode='silver_negative',interlayer_adhesion=.7,receiver_cohesion=.4)
    s.process('peel');state=s.state
    a=peel.clear_negative(state,strength=.35,marks=.6,angle_degrees=20)
    b=peel.clear_negative(a,strength=.35,marks=.6,angle_degrees=-25)
    assert b.fields['donor_gel'].sum()<a.fields['donor_gel'].sum()<state.fields['donor_gel'].sum()
    assert budget(b).sum()>budget(a).sum()
    for key in ('receiver_native','donor_on_receiver','receiver_residue'):np.testing.assert_array_equal(state.fields[key],b.fields[key])
    zero=peel.clear_negative(b,strength=0,marks=.9)
    for key,value in b.fields.items():np.testing.assert_array_equal(value,zero.fields[key])
    marked=peel.handling(b,target='donor',reagent_residue=.7)
    def forbidden(*args,**kwargs):raise AssertionError('Observation regenerated a brush/structure')
    monkeypatch.setattr(separation,'brush',forbidden)
    group=peel.products(marked)
    assert group.bodies[1].usable_negative
    assert not np.array_equal(api.observe(group.bodies[1]),api.observe(peel.products(b).bodies[1]))
    np.testing.assert_array_equal(api.observe(group.bodies[0]),api.observe(peel.products(state).bodies[0]))
    np.testing.assert_array_equal(api.observe(group.bodies[1]),api.observe(group.bodies[1],chunk_pixels=96,workers=2))


def test_v4_archive_recipe_optical_source_and_future_environment(tmp_path):
    from film_foundry.app.optical_cascade import project_medium
    from film_foundry.contracts.optical_cascade import OpticalRelay
    s=contact(transfer='silver_compound',donor_mode='silver_negative',interlayer_adhesion=.7)
    s.process('environment',{'temperature_c':35});s.process('advance',{'seconds':30})
    path=tmp_path/'pause.npz';api.save(path,s.state)
    s.state=api.load(path);s.process('peel',{'speed_mm_s':100,'angle_degrees':45})
    s.process('clear_negative',{'strength':.4,'marks':.6})
    api.save(path,s.state);s.state=api.load(path)
    s.process('clear_negative',{'strength':.5});s.process('handling',{'target':'donor','fingerprints':.2})
    s.process('clean',{'target':'receiver','abrasion':.15})
    replay=MaterialLabSession('peel');replay.image=IMAGE;replay.replay_recipe(s.recipe())
    for key,value in s.state.fields.items():np.testing.assert_array_equal(value,replay.state.fields[key],err_msg=key)
    api.save(path,s.group);group=api.load(path)
    for actual,expected in zip(group.bodies,s.group.bodies):np.testing.assert_array_equal(api.observe(actual),api.observe(expected))
    before={key:value.copy() for key,value in group.bodies[1].fields.items()}
    projected=project_medium(group,OpticalRelay(mode='transmission'),body_id=group.bodies[1].body_id)
    assert projected.shape==IMAGE.shape and np.isfinite(projected).all()
    for key,value in before.items():np.testing.assert_array_equal(value,group.bodies[1].fields[key])
    assert s.capabilities()['separation']['removed_image_accounted']


def test_workpage_target_dispatch_restore_and_legacy_guards():
    from film_foundry.ui.material_lab_window import MaterialLabWindow
    from film_foundry.ui import material_workspace_state as drafts
    w=MaterialLabWindow('peel')
    try:
        w.session=contact(transfer='silver_compound',donor_mode='silver_negative',interlayer_adhesion=.7)
        def submit(action,*args,**kwargs):action();w._sync()
        w._submit=submit;w._sync()
        w.operation_controls['peel']['speed_mm_s'].setValue(130)
        w.process('peel');w.process('clear_negative');w.process('clear_negative')
        target=w.finish_controls['target'];target.setCurrentIndex(target.findData('donor'))
        w.finish_controls['fingerprints'].setValue(.3);w.process('handling')
        controls=w.operation_controls['clean'];controls['target'].setCurrentIndex(controls['target'].findData('donor'))
        controls['abrasion'].setValue(.25);w.process('clean')
        assert w.session.recipe()['steps'][-1]['parameters']['target']=='donor'
        draft=drafts.capture(w);drafts.restore(w,draft);w._restore_controls()
        assert controls['target'].currentData()=='donor' and controls['abrasion'].value()==.25
        old=MaterialLabSession('peel');old.image=IMAGE;old.process('expose',material_version=3);old.process('spread')
        w.session=old;w._sync();assert not w.operation_controls['peel']['speed_mm_s'].isEnabled()
        w.process('peel');w.process('clean')
        assert old.state.schema_version==3 and not controls['target'].isEnabled()
    finally:w.close()
