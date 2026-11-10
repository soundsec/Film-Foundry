"""Targeted bath/history/inventory and actual frontend dispatch checks."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import numpy as np
import pytest
from film_foundry.core import silver_plate as p,wet_plate as w
from film_foundry.app import material_families as api
from film_foundry.app.material_lab_session import MaterialLabSession


def image():return np.random.default_rng(5).random((20,28,3),dtype=np.float32)


def same(a,b):
    assert a.stage==b.stage and a.schema_version==b.schema_version and a.elapsed_seconds==b.elapsed_seconds
    for key,value in a.fields.items():np.testing.assert_allclose(value,b.fields[key],atol=4e-7,rtol=1e-6,err_msg=key)


def plate():return p.develop(p.expose(image(),group_id='test',seed=17,schema_version=3),seconds=35)


def test_plate_partial_fix_gild_and_zero_activity_preserve_image():
    from dataclasses import replace
    initial=plate();partial=p.fix(initial,seconds=1,temperature_c=10)
    assert partial.stage=='fixing' and partial.fields['halide'].max()>1e-4
    np.testing.assert_array_equal(initial.fields['particles'],partial.fields['particles'])
    with pytest.raises(ValueError):p.products(partial)
    with pytest.raises(ValueError):p.gild(partial)
    with pytest.raises(ValueError):replace(partial,stage='fixed',fields={**partial.fields,'sensitivity':np.zeros_like(partial.fields['sensitivity'])})
    no_reaction=p.fix(partial,seconds=120,activity=0)
    for key,value in partial.fields.items():np.testing.assert_array_equal(value,no_reaction.fields[key])
    whole=p.fix(initial,seconds=120);split=p.fix(p.fix(initial,seconds=40),seconds=80)
    same(whole,split);assert whole.stage=='fixed'
    gold=p.gild(whole,seconds=1);assert gold.stage=='gilding'
    with pytest.raises(ValueError):p.products(gold)
    same(p.gild(whole,seconds=180),p.gild(p.gild(whole,seconds=60),seconds=120))
    done=p.gild(whole,seconds=180);assert done.stage=='gilded'
    more=p.gild(done,amount=.1)
    np.testing.assert_array_equal(done.fields['gold'],more.fields['gold'])
    for state in (initial,partial,whole,done):
        f=state.fields
        np.testing.assert_allclose(f['halide']+f['removed_halide']+f['particles'],f['sensitized_silver'],atol=1e-6)


def test_wet_external_silver_has_finite_origin_and_separate_fates():
    source=w.expose(image(),group_id='wet',seed=17)
    assert source.fields['external_supplied'].max()>0
    whole=w.develop(source,seconds=100,temperature_c=27,pour_uniformity=.6)
    split=w.develop(w.develop(source,seconds=35,temperature_c=27,pour_uniformity=.6),seconds=65,temperature_c=27,pour_uniformity=.6)
    same(whole,split)
    exhausted=w.develop(whole,seconds=86400)
    repeated=w.develop(exhausted,seconds=86400)
    np.testing.assert_allclose(exhausted.fields['external_deposit'],repeated.fields['external_deposit'],atol=1e-6)
    np.testing.assert_array_equal(source.fields['external_supplied'],repeated.fields['external_supplied'])
    washed=w.wash(w.fix(whole))
    np.testing.assert_array_equal(whole.fields['external_deposit'],washed.fields['external_deposit'])
    assert washed.fields['external_silver'].sum()<whole.fields['external_silver'].sum()
    for state in (source,whole,repeated,washed):
        f=state.fields
        np.testing.assert_allclose(f['external_silver']+f['external_deposit']+f['external_removed'],f['external_supplied'],atol=1e-6)
        np.testing.assert_allclose(f['halide']+f['silver']+f['removed_silver'],source.fields['halide'],atol=1e-6)
    no_supply=w.expose(image(),w.WetPlateMaterial(external_silver_load=0),group_id='none',seed=17)
    no_supply=w.develop(no_supply,seconds=100)
    assert not np.any(no_supply.fields['external_deposit'])
    dry=w.wait(source,seconds=86400,humidity=0)
    dry=w.develop(dry,seconds=100)
    assert dry.fields['external_deposit'].sum()<whole.fields['external_deposit'].sum()


@pytest.mark.parametrize('kind',('plate','wet'))
def test_archive_resume_route_recipe_and_legacy_replay(kind,tmp_path):
    from film_foundry.app.material_route import run_material_route
    session=MaterialLabSession(kind);session.image=image()
    for op in session.module.EXPOSURE_STEPS:session.process(op,seed=17,**({'material_version':3} if kind=='plate' else {}))
    session.process('develop',{'seconds':35})
    if kind=='plate':session.process('fix',{'seconds':1})
    path=tmp_path/'pause.npz';api.save(path,session.state)
    restored=api.load(path)
    operation='fix';parameters={'seconds':120}
    result=run_material_route(restored,stop='state',operations=[{'operation':operation,**parameters}]).value
    session.process(operation,parameters);same(result,session.state)
    session.process('gild' if kind=='plate' else 'wash')
    replay=MaterialLabSession(kind);replay.image=image();replay.replay_recipe(session.recipe());same(session.state,replay.state)
    path=tmp_path/'group.npz';api.save(path,session.group);group=api.load(path)
    np.testing.assert_array_equal(api.observe(group.bodies[0]),api.observe(session.group.bodies[0]))
    legacy=MaterialLabSession(kind);legacy.image=image()
    for op in legacy.module.EXPOSURE_STEPS:legacy.process(op,seed=17,material_version=2)
    legacy.process('develop',{'seconds':35});legacy.process('fix');legacy.process('gild' if kind=='plate' else 'wash')
    replay.replay_recipe(legacy.recipe());same(legacy.state,replay.state);assert replay.state.schema_version==2


def test_plate_workpage_actual_bath_parameters_and_partial_state():
    from film_foundry.ui.material_lab_window import MaterialLabWindow
    from film_foundry.ui import material_workspace_state as drafts
    window=MaterialLabWindow('plate')
    try:
        window.session.image=image()
        def submit(action,*args,**kwargs):action();window._sync()
        window._submit=submit
        for op in ('prepare','polish','sensitize','expose','develop'):window.process(op)
        window.operation_controls['fix']['seconds'].setValue(1)
        window.process('fix');assert window.session.state.stage=='fixing'
        assert window.operation_buttons['fix'].isEnabled() and not window.operation_buttons['gild'].isEnabled()
        window.operation_controls['fix']['seconds'].setValue(120);window.process('fix')
        window.operation_controls['gild']['temperature_c'].setValue(30);window.process('gild')
        assert window.session.recipe()['steps'][-1]['parameters']['temperature_c']==30
        draft=drafts.capture(window);drafts.restore(window,draft)
        assert window.operation_controls['gild']['temperature_c'].value()==30
        window.operation_controls['fix']['seconds'].setValue(9)
        window.operation_controls['gild']['temperature_c'].setValue(5)
        window._restore_controls()
        assert window.operation_controls['fix']['seconds'].value()==120
        assert window.operation_controls['gild']['temperature_c'].value()==30
    finally:window.close()


def test_wet_workpage_material_and_inspection():
    from film_foundry.ui.sheet_lab_window import SheetLabWindow
    from film_foundry.app import sheet_inspection
    window=SheetLabWindow('wet')
    try:
        assert 'external_silver_load' in window.material_controls
        session=window.session;session.image=image()
        for op in ('prepare','sensitize','expose','develop'):session.process(op,material={'external_silver_load':.5},seed=17)
        window._sync()
        assert 'external_deposit' in sheet_inspection.options(session)
        assert dict(sheet_inspection.metrics(session))['silver']>0
    finally:window.close()
