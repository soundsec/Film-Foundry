"""Targeted hydration, local bath, compatibility and desktop dispatch checks."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
import pytest
from film_foundry.core import photographic_paper as paper,cyanotype as cyan,platinum_palladium as pt,paper_support as support
from film_foundry.core.material_family import ViewingConditions
from film_foundry.app import material_families as api
from film_foundry.app.material_lab_session import MaterialLabSession

FAMILIES={'paper':paper,'cyanotype':cyan,'platinum':pt}
LOCAL=dict(region='spot',position_x=.35,position_y=.5,radius=.3,coverage=.7,temperature_c=28.)


def image():return np.random.default_rng(8).random((20,28,3),dtype=np.float32)


def same(a,b):
    assert a.schema_version==b.schema_version and a.stage==b.stage and a.elapsed_seconds==b.elapsed_seconds
    for key,value in a.fields.items():np.testing.assert_allclose(value,b.fields[key],atol=5e-7,rtol=2e-6,err_msg=key)


def start(kind,version=None):
    session=MaterialLabSession(kind);session.image=image()
    steps={'paper':('expose','develop','fix'),'cyanotype':('expose',),'platinum':('expose','develop')}[kind]
    for op in steps:session.process(op,seed=19,material_version=version)
    session.process('handling',{'fingerprints':.3,'reagent_residue':.4})
    return session


@pytest.mark.parametrize('kind',FAMILIES)
def test_local_baths_integrate_hydration_and_preserve_uncontacted_pools(kind):
    session=start(kind);state=session.state;module=FAMILIES[kind]
    if kind=='paper':state=paper.dry(paper.wash(state,seconds=0),seconds=600)
    operation=module.clear if kind=='platinum' else module.wash
    whole=operation(state,seconds=130,**LOCAL)
    split=operation(operation(state,seconds=50,**LOCAL),seconds=80,**LOCAL);same(whole,split)
    mask=support.contact((20,28),**{k:v for k,v in LOCAL.items() if k!='temperature_c'})
    for key,value in state.fields.items():np.testing.assert_array_equal(value[mask==0],whole.fields[key][mask==0],err_msg=key)
    assert np.any(whole.fields['paper_moisture'][mask>0]>state.fields['paper_moisture'][mask>0])
    untouched=operation(state,seconds=130,**{**LOCAL,'coverage':0})
    for key,value in state.fields.items():np.testing.assert_array_equal(value,untouched.fields[key])
    f=whole.fields
    if kind=='paper':
        np.testing.assert_allclose(f['halide']+f['silver']+f['removed_silver'],np.repeat(f['coating'][...,None],3,axis=-1),atol=1e-6)
        np.testing.assert_array_equal(f['silver'],state.fields['silver']);np.testing.assert_array_equal(f['dye'],state.fields['dye'])
    elif kind=='cyanotype':
        np.testing.assert_allclose(f['ferric']+f['ferrous']+f['reduced_pigment']+f['blue_pigment']+f['iron_removed'],f['coating'],atol=1e-6)
    else:
        np.testing.assert_allclose(f['ferric']+f['ferrous']+f['spent_ferric']+f['iron_removed'],2*f['coating'],atol=1e-6)
        np.testing.assert_allclose(f['platinum_salt']+f['palladium_salt']+f['platinum']+f['palladium']+f['metal_removed'],f['coating'],atol=1e-6)
        for key in ('platinum','palladium'):np.testing.assert_array_equal(f[key],state.fields[key])


def test_base_drying_optics_and_cyan_oxidation_have_explicit_ownership():
    endpoints={}
    for base in ('rc','fiber'):
        state=paper.expose(image(),paper.PaperMaterial('color',paper_base=base),group_id=base,seed=19)
        state=paper.wash(paper.bleach_fix(paper.develop(state)),seconds=90)
        dry=paper.dry(state,seconds=600,humidity=.3,temperature_c=30)
        split=paper.dry(paper.dry(state,seconds=200,humidity=.3,temperature_c=30),seconds=400,humidity=.3,temperature_c=30)
        same(dry,split)
        for key,value in state.fields.items():
            if key!='paper_moisture':np.testing.assert_array_equal(value,dry.fields[key])
        endpoints[base]=(state,dry)
        assert not np.array_equal(paper.preview(state),paper.preview(dry))
        body=paper.products(dry).bodies[0]
        before={key:value.copy() for key,value in body.fields.items()}
        api.observe(body,ViewingConditions(view_direction=(.8,0,1)))
        for key,value in before.items():np.testing.assert_array_equal(value,body.fields[key])
    assert endpoints['rc'][1].fields['paper_moisture'].mean()<endpoints['fiber'][1].fields['paper_moisture'].mean()
    assert endpoints['rc'][0].fields['residue'].sum()<endpoints['fiber'][0].fields['residue'].sum()
    washed=cyan.wash(cyan.expose(image(),group_id='blue',seed=19),seconds=45)
    dry=cyan.dry(washed,seconds=300,temperature_c=25)
    same(dry,cyan.dry(cyan.dry(washed,seconds=100,temperature_c=25),seconds=200,temperature_c=25))
    assert dry.fields['blue_pigment'].sum()>washed.fields['blue_pigment'].sum()
    np.testing.assert_allclose(dry.fields['blue_pigment']+dry.fields['reduced_pigment'],washed.fields['blue_pigment']+washed.fields['reduced_pigment'],atol=1e-6)
    rewetted=cyan.alkali_contact(dry,seconds=1,activity=0)
    assert rewetted.fields['paper_moisture'].sum()>dry.fields['paper_moisture'].sum()
    np.testing.assert_array_equal(rewetted.fields['blue_pigment'],dry.fields['blue_pigment'])


@pytest.mark.parametrize('kind',FAMILIES)
def test_archive_recipe_continuation_and_legacy_version(kind,tmp_path):
    session=start(kind)
    if kind=='platinum':session.process('clear',{'seconds':35,**LOCAL})
    session.process('wash',{'seconds':80,**LOCAL})
    path=tmp_path/'pause.npz';api.save(path,session.state)
    restored=MaterialLabSession(kind);restored.load(path);restored.process('dry',{'seconds':300})
    session.process('dry',{'seconds':300});same(session.state,restored.state)
    replay=MaterialLabSession(kind);replay.image=image();replay.replay_recipe(session.recipe());same(replay.state,session.state)
    api.save(path,session.group);group=api.load(path)
    np.testing.assert_array_equal(api.observe(group.bodies[0]),api.observe(session.group.bodies[0]))
    np.testing.assert_allclose(api.observe(group.bodies[0],chunk_pixels=56,workers=1),api.observe(group.bodies[0]),atol=2e-7)
    old=start(kind,1 if kind=='paper' else 2)
    if kind=='platinum':old.process('clear')
    old.process('wash')
    if kind=='cyanotype':old.process('oxidize')
    replay.replay_recipe(old.recipe());same(old.state,replay.state)
    assert 'dry' not in old.operations
    with pytest.raises(ValueError):FAMILIES[kind].wash(old.state,**LOCAL)


@pytest.mark.parametrize('kind',FAMILIES)
def test_workpage_local_dispatch_and_draft_restore(kind):
    from film_foundry.ui.sheet_lab_window import SheetLabWindow
    from film_foundry.ui import material_workspace_state as drafts
    from film_foundry.app import sheet_inspection
    window=SheetLabWindow(kind)
    try:
        window.session=start(kind)
        if kind=='platinum':window.session.process('clear')
        def submit(action,*args,**kwargs):action();window._sync()
        window._submit=submit;window._sync()
        controls=window.operation_controls['wash'];controls['region'].setCurrentIndex(controls['region'].findData('spot'))
        controls['position_x'].setValue(.3);window.process('wash')
        assert window.session.recipe()['steps'][-1]['parameters']['region']=='spot'
        assert 'paper_moisture' in sheet_inspection.options(window.session)
        window.process('dry')
        window.view_angle.setValue(32);draft=drafts.capture(window);drafts.restore(window,draft)
        assert window.view_angle.value()==32 and controls['position_x'].value()==.3
        info=window.session.capabilities()
        dry=next(item for item in info['operations'] if item['operation']=='dry')
        assert dry['minimum_version']==(2 if kind=='paper' else 3)
        if kind=='cyanotype':assert 'blue_pigment' in dry['targets']
        window.session=start(kind,1 if kind=='paper' else 2)
        if kind=='platinum':window.session.process('clear')
        window._sync();assert not controls['region'].isEnabled()
        window.process('wash')
        assert window.session.state.schema_version==(1 if kind=='paper' else 2)
    finally:window.close()
