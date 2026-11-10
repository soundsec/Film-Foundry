"""Material-space identity, actual workflow persistence and bounded coverage."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
import cv2
import numpy as np
import pytest
from film_foundry.app.material_lab_session import MaterialLabSession,FAMILIES
from film_foundry.app import material_families as api
from film_foundry.core import material_structure as ms,scaled_structure as scaled

FIELDS={
 'peel':('backing_texture',),'integral':('backing_texture',),
 'plate':('polish_x','polish_y','microtexture'),
 'wet':('coating','flow','roughness'), 'paper':('coating','paper'),
 'cyanotype':('coating','binding','fibers'),
 'platinum':('coating','absorption','clear_access','fibers')}
FINISH={
 'peel':(('spread',{'roller_streaks':.4,'reagent_leak':.3,'spread_unevenness':.6}),('advance',{'seconds':160}),('peel',{})),
 'integral':(('eject',{}),('spread',{'roller_streaks':.4,'reagent_leak':.3,'spread_unevenness':.6}),('advance',{'seconds':160}),('stabilize',{})),
 'plate':(('sensitize',{}),('expose',{}),('develop',{'seconds':60}),('fix',{}),('weather',{'scratches':.3,'pitting':.5,'edge_tarnish':.5})),
 'wet':(('sensitize',{}),('expose',{}),('develop',{}),('fix',{}),('wash',{})),
 'paper':(('develop',{}),('fix',{}),('wash',{})),
 'cyanotype':(('wash',{}),('oxidize',{})),
 'platinum':(('develop',{}),('clear',{}),('wash',{}))}


def source(shape):return np.full((*shape,3),.18,np.float32)


def start(kind,shape):
    session=MaterialLabSession(kind);session.image=source(shape)
    material={'material_long_edge_mm':80.,'texture_scale_mm':1.3,'process_scale_mm':6.}
    if kind in ('wet','paper','cyanotype','platinum'):material.update(coating_variation=.6,texture=.6,edge_loss=.3)
    session.process('prepare' if kind in ('plate','wet') else 'expose',material=material,seed=17)
    if kind=='plate':session.process('polish',{'streaks':.8,'angle_degrees':30})
    return session


@pytest.mark.parametrize('kind',FIELDS)
def test_material_structure_survives_resolution_changes(kind):
    small=start(kind,(64,96));large=start(kind,(128,192))
    for key in FIELDS[kind]:
        field=small.state.fields[key]
        assert field.shape==(64,96) and np.std(field)>1e-5
        np.testing.assert_allclose(field,cv2.resize(large.state.fields[key],(96,64),interpolation=cv2.INTER_AREA),atol=3e-7,rtol=0,err_msg=kind+'/'+key)


@pytest.mark.parametrize('kind',FIELDS)
def test_new_structure_archive_recipe_and_workpage(kind,tmp_path):
    from film_foundry.ui.material_lab_window import MaterialLabWindow
    from film_foundry.ui.sheet_lab_window import SheetLabWindow
    from film_foundry.ui import material_workspace_state as drafts
    window=(MaterialLabWindow if kind in ('peel','integral','plate') else SheetLabWindow)(kind)
    try:
        controls=window.material_controls
        # A saved pre-feature planning draft must not silently change generator.
        old=drafts.capture(window)
        for key in ms.PARAMETERS:old['values'].pop('material_controls.'+key)
        drafts.restore(window,old)
        assert controls['structure_model'].currentData()==ms.LEGACY
        assert not controls['texture_scale_mm'].isEnabled()
        controls['structure_model'].setCurrentIndex(controls['structure_model'].findData(ms.CURRENT))
        assert controls['texture_scale_mm'].isEnabled()
        controls['material_long_edge_mm'].setValue(80)
        controls['texture_scale_mm'].setValue(1.3);controls['process_scale_mm'].setValue(6)
        saved=drafts.capture(window);drafts.restore(window,saved)
        window.session.image=source((32,48));window._sync()
        def submit(action,*args,**kwargs):action();window._sync()
        window._submit=submit
        window.process('prepare' if kind in ('plate','wet') else 'expose')
        session=window.session
        assert session.state.material.structure_model==ms.CURRENT
        assert session.state.material.material_long_edge_mm==80
        assert not controls['texture_scale_mm'].isEnabled()
        path=tmp_path/'state.npz';api.save(path,session.state);session.state=api.load(path)
        if kind=='plate':session.process('polish',{'streaks':.7})
        for op,params in FINISH[kind]:session.process(op,params)
        if kind in ('peel','integral'):
            f=session.state.fields
            np.testing.assert_allclose(f['received']+f['remaining']+f['immobile']+f['transfer_loss'],1,atol=2e-6)
            assert float((f['reagent']+f['escaped_reagent']).mean())==pytest.approx(1,abs=2e-6)
        replay=MaterialLabSession(kind);replay.image=session.image;replay.replay_recipe(session.recipe())
        for key,value in session.state.fields.items():np.testing.assert_array_equal(value,replay.state.fields[key],err_msg=key)
        api.save(path,session.group);group=api.load(path)
        info=session.capabilities()
        assert info['structure']['model']==ms.CURRENT
        before={key:value.copy() for key,value in group.bodies[0].fields.items()}
        np.testing.assert_array_equal(api.observe(group.bodies[0]),api.observe(session.group.bodies[0]))
        for key,value in before.items():np.testing.assert_array_equal(value,group.bodies[0].fields[key])
    finally:window.close()


def test_mm_scale_changes_frequency_and_large_sheets_remain_covered():
    material=FAMILIES['cyanotype'][1](coating_variation=1,edge_loss=0,texture=0)
    shape=(256,384)
    fine=ms.noise(shape,4,material,900,1)
    coarse=ms.noise(shape,4,material,900,8)
    def variation(a):return np.mean(np.abs(np.diff(a,axis=1)))
    assert variation(fine)>2*variation(coarse)
    larger=ms.noise(shape,4,replace(material,material_long_edge_mm=200),900,8)
    assert variation(larger)>1.5*variation(coarse)
    assert not np.array_equal(coarse,ms.noise(shape,5,material,900,8))
    tiny=replace(material,material_long_edge_mm=1000,texture_scale_mm=.05,process_scale_mm=.1)
    coating=scaled.cyan_structure((64,96),3,tiny)['coating']
    assert min(coating[:32].mean(),coating[32:].mean())>.6
    for direction in ('down','right'):
        for extreme in (tiny,replace(material,material_long_edge_mm=10,process_scale_mm=200)):
            remaining,escaped,residue,roller=scaled.transfer_flow((64,96),3,extreme,direction,.5,1)
            assert np.isfinite(remaining).all() and np.isfinite(residue).all()
            assert float((remaining+escaped).mean())==pytest.approx(1,abs=2e-6)
            if extreme is tiny:
                assert np.quantile(remaining,.05)>.5
                assert min(roller[:32].mean(),roller[32:].mean())>.1
