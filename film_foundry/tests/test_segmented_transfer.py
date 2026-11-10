"""Necessary v3 integration checks: history, conservation, compatibility and UI."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import numpy as np
import pytest
from film_foundry.core import peel_apart as peel,integral_instant as instant
from film_foundry.app import material_families as api
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.app.material_route import run_material_route
from film_foundry.app.transfer_intent import warm_shift_parameters


def pixels():return np.random.default_rng(11).random((20,28,3),dtype=np.float32)


def start(kind,**material):
    session=MaterialLabSession(kind);session.image=pixels()
    session.process('expose',material=material,seed=71,material_version=3)
    if kind=='integral':session.process('eject')
    return session


def same(a,b,*,exact=False):
    assert a.schema_version==b.schema_version and a.stage==b.stage and a.elapsed_seconds==b.elapsed_seconds
    assert a.fields.keys()==b.fields.keys()
    for key,value in a.fields.items():
        if exact:np.testing.assert_array_equal(value,b.fields[key])
        else:np.testing.assert_allclose(value,b.fields[key],atol=6e-7,rtol=2e-6,err_msg=key)


@pytest.mark.parametrize('kind',('peel','integral'))
@pytest.mark.parametrize('transfer',('dye','silver_compound'))
def test_constant_environment_split_matches_whole_with_competing_light(kind,transfer):
    s=start(kind,transfer=transfer,initial_contamination=.3)
    s.process('spread',{'reagent_leak':.15,'reagent_decay':.2,'spread_seconds':8.})
    s.process('environment',{'temperature_c':35.,'light_protection':.2,'ambient_light':4.})
    module=s.module;initial=s.state
    whole=module.advance(initial,seconds=75.)
    split=initial
    for seconds in (1.,3.,7.,19.,45.):split=module.advance(split,seconds=seconds)
    same(whole,split)
    np.testing.assert_allclose(whole.fields['immobile']+whole.fields['remaining']+whole.fields['received']+whole.fields['transfer_loss'],1,atol=1e-6)
    assert whole.fields['transfer_loss'].max()>0
    assert np.all(whole.fields['received']>=initial.fields['received'])
    assert np.all(whole.fields['developed_donor']<=whole.fields['immobile'])


@pytest.mark.parametrize('kind',('peel','integral'))
def test_environment_changes_future_only_and_persists_through_recipe_and_archive(kind,tmp_path):
    s=start(kind);s.process('spread');s.process('advance',{'seconds':30.})
    before=s.state
    params=warm_shift_parameters(strength=.8,light_protection=.1,ambient_light=3.)
    s.process('environment',params)
    for key,value in before.fields.items():assert s.state.fields[key] is value
    saved=tmp_path/'paused.npz';api.save(saved,s.state)
    resumed=api.load(saved)
    s.process('advance',{'seconds':45.})
    actual=run_material_route(resumed,stop='state',operations=[{'operation':'advance','seconds':45.}]).value
    same(s.state,actual,exact=True)
    assert np.all(s.state.fields['received']>=before.fields['received'])
    assert np.all(s.state.fields['transfer_loss']>=before.fields['transfer_loss'])
    opposite=s.module.environment(before,temperature_c=8.)
    opposite=s.module.advance(opposite,seconds=45.)
    assert not np.allclose(s.state.fields['received'],opposite.fields['received'])
    replay=start(kind);replay.replay_recipe(s.recipe());same(replay.state,s.state,exact=True)
    s.process('peel' if kind=='peel' else 'stabilize')
    group=tmp_path/'group.npz';api.save(group,s.group)
    loaded=api.load(group)
    for a,b in zip(s.group.bodies,loaded.bodies):np.testing.assert_array_equal(api.observe(a),api.observe(b))
    assert s.capabilities()['material_version']==3


def test_local_contamination_and_cleaning_do_not_reverse_formation():
    clean=start('peel');dirty=start('peel')
    dirty.process('contaminate',{'amount':1.,'position_x':.5,'position_y':.5,'radius':.2})
    unchanged=dirty.state
    dirty.process('contaminate',{'amount':0.});dirty.process('clean',{'strength':0.})
    for key,value in unchanged.fields.items():assert dirty.state.fields[key] is value
    latent=dirty.state.fields['immobile']
    for s in (clean,dirty):s.process('spread');s.process('advance',{'seconds':80.})
    center=(10,14)
    assert dirty.state.fields['received'][center].mean()<clean.state.fields['received'][center].mean()
    np.testing.assert_array_equal(dirty.state.fields['immobile'],latent)
    dirty.process('environment',{'temperature_c':40.});dirty.process('advance',{'seconds':20.});dirty.process('peel')
    before=dirty.state;dirty.process('clean')
    for key in ('immobile','remaining','received','transfer_loss'):assert dirty.state.fields[key] is before.fields[key]
    assert not np.any(dirty.state.fields['inhibitor'])
    assert np.all(dirty.state.fields['stain_density']<=before.fields['stain_density'])
    # Initial material residue affects the original exposure, post-exposure deposit does not.
    initial=start('peel',initial_contamination=.7)
    assert not np.allclose(initial.state.fields['immobile'],clean.state.fields['immobile'])
    initial.process('clean');assert np.any(initial.state.fields['immobile'])
    sealed=start('integral');sealed.process('spread')
    for op in ('contaminate','clean'):
        assert op not in sealed.operations
        with pytest.raises(ValueError):getattr(instant,op)(sealed.state)


def test_warm_intent_is_the_same_parameters_and_can_be_disabled():
    temperatures=[warm_shift_parameters(strength=x)['temperature_c'] for x in (0,.25,.5,.75,1)]
    assert temperatures==[20,25,30,35,40]
    assert warm_shift_parameters(strength=1,enabled=False)['temperature_c']==20
    a=start('integral');b=start('integral')
    for s in (a,b):s.process('spread')
    a.process('environment',warm_shift_parameters(strength=.5))
    b.process('environment',{'temperature_c':30.,'light_protection':1.,'ambient_light':1.,'solar_warming':0.})
    a.process('advance',{'seconds':75});b.process('advance',{'seconds':75});same(a.state,b.state,exact=True)


def test_v2_recipe_replay_remains_v2_and_does_not_gain_new_steps():
    a=MaterialLabSession('integral');a.image=pixels();a.process('expose',material_version=2,seed=71)
    for op,p in [('eject',{}),('spread',{'temperature_c':35.,'light_protection':.4}),('advance',{'seconds':40}),('stabilize',{})]:a.process(op,p)
    recipe=a.recipe()
    for key in ('light_sensitivity','temperature_sensitivity','inhibitor_sensitivity','initial_contamination'):recipe['material'].pop(key)
    b=MaterialLabSession('integral');b.image=pixels();b.replay_recipe(recipe)
    same(a.state,b.state,exact=True)
    assert b.state.schema_version==2 and 'environment' not in b.operations
    with pytest.raises(ValueError):instant.environment(b.state,temperature_c=20)


@pytest.mark.parametrize('kind',('peel','integral'))
def test_actual_workpage_environment_and_workspace_draft(kind):
    from film_foundry.ui.material_lab_window import MaterialLabWindow
    from film_foundry.ui import material_workspace_state as drafts
    window=MaterialLabWindow(kind);window.session=start(kind);window._sync()
    try:
        # Use the real dispatch, foreground shortcut only to avoid unrelated UI wait tests.
        def submit(action,*args,**kwargs):action();window._sync()
        window._submit=submit
        window.process('spread')
        assert window.operation_buttons['environment'].isEnabled()
        window.environment_mode.setCurrentIndex(window.environment_mode.findData('warm_shift'))
        window.intent_controls['strength'].setValue(.5)
        window.process('environment')
        assert json.loads(window.session.state.process_json)['temperature_c']==30
        assert window.session.recipe()['steps'][-1]['parameters']['temperature_c']==30
        draft=drafts.capture(window)
        restored=MaterialLabWindow(kind)
        try:
            restored.session=window.session;drafts.restore(restored,draft)
            assert restored.environment_mode.currentData()=='warm_shift'
            assert restored.intent_controls['strength'].value()==.5
        finally:restored.close()
        window.seconds.setValue(30);window.process('advance')
        assert window.session.state.elapsed_seconds==30
    finally:window.close()
