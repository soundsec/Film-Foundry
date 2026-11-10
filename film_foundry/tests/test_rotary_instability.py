"""Seeded liquid-level poses must affect actual, reproducible bath contact."""
import numpy as np
import pytest
from film_foundry.contracts.process_events import EVENT_MODEL_V13,validate_process_events
from film_foundry.core.rotary_contact import contact_duration,motion_intervals
from film_foundry.tests.test_rotary_contact import configured


def event(**values):
    return validate_process_events({'model':EVENT_MODEL_V13,'events':[{'kind':'rotary_contact',**values}]})['events'][0]


def config(**values):
    c=configured(pause_s=50.,pause_at_fraction=.25)
    c.chemistry.process_events={'model':EVENT_MODEL_V13,'events':[event(pause_s=50.,pause_at_fraction=.25,**values)]}
    return c


def test_contact_matches_independent_time_steps_and_preserves_windows_tiles():
    shape=(5,7);duration=12.;dt=.001
    e=event(rotation_instability=.8,rotation_seed=31,rotation_rpm=7.,pause_s=4.,pause_at_fraction=.25,immersion_fraction=.37)
    poses=np.random.Generator(np.random.PCG64(31)).uniform(-1.,1.,(4,2))*.8
    y=((np.arange(5)+.5)/5-.5)[:,None];x=((np.arange(7)+.5)/7-.5)[None,:]*7/5
    activity=np.zeros(shape);whole=np.zeros(shape);part=np.zeros(shape)
    decay=np.exp(-dt/5.)
    for t in (np.arange(round(duration/dt))+.5)*dt:
        moving=t-np.clip(t-3.,0.,4.)
        index=min(3,int(moving/8.*4))
        shift,angle=poses[index];angle=np.deg2rad(angle*25.)
        phase=np.mod(.5+y*np.cos(angle)+x*np.sin(angle)+shift*.2+moving*7./60.,1.)
        wet=phase<.37
        dose=np.where(wet,dt,activity*5.*(1.-decay))
        whole+=dose
        if .2*duration<t<.9*duration:part+=dose
        activity=np.where(wet,1.,activity*decay)
    np.testing.assert_allclose(contact_duration(shape,0,5,duration,e),whole,atol=.007,rtol=0)
    np.testing.assert_allclose(contact_duration(shape,0,5,duration,e,(.2,.9)),part,atol=.007,rtol=0)
    shape=(37,61);full=contact_duration(shape,0,37,duration,e)
    tiled=np.concatenate([contact_duration(shape,a,b,duration,e) for a,b in ((0,9),(9,24),(24,37))])
    np.testing.assert_array_equal(full,tiled)
    np.testing.assert_allclose(full,sum(contact_duration(shape,0,37,duration,e,(i/8,(i+1)/8)) for i in range(8)),atol=2e-6)
    assert np.max(np.ptp(full,axis=1))>.01
    assert not np.array_equal(full,contact_duration(shape,0,37,duration,{**e,'rotation_seed':32}))
    assert len(motion_intervals(duration,{**e,'rotation_rpm':120.}))<=6
    for fraction in (0.,1.):
        assert np.all(contact_duration(shape,0,37,duration,{**e,'immersion_fraction':fraction})==duration*fraction)
    stopped=event(rotation_instability=.7,rotation_rpm=0.)
    assert len({(row[4],row[5]) for row in motion_intervals(120.,stopped)})==1
    # A pause exactly at the first moving boundary must retain that pose.
    boundary=event(rotation_instability=.7,pause_s=40.,pause_at_fraction=1/6)
    intervals=motion_intervals(120.,boundary)
    assert intervals[0][4:]==intervals[1][4:]


def test_zero_keeps_old_pixels_and_rng_and_contact_drives_fog_hardening():
    from film_foundry.core.engine import develop_negative
    from film_foundry.core.process_operations import BathContactRate,solution_hardening
    from film_foundry.contracts.process_events import validate_process_response
    old=configured(pause_s=50.,pause_at_fraction=.25);neutral=config(rotation_seed=47)
    shape=(48,64);image=np.full((*shape,3),.34,np.float32)
    ra=np.random.default_rng(19);rb=np.random.default_rng(19);rc=np.random.default_rng(19)
    a=develop_negative(image,old,rng=ra);b=develop_negative(image,neutral,rng=rb)
    np.testing.assert_array_equal(a.optical_density_rgb,b.optical_density_rgb)
    changed=config(rotation_instability=.8,rotation_seed=47);c=develop_negative(image,changed,rng=rc)
    assert ra.bit_generator.state==rb.bit_generator.state==rc.bit_generator.state
    assert np.max(np.abs(c.density_cmy-a.density_cmy))>.001
    provider=BathContactRate(changed,shape);integrated=np.zeros(shape,np.float64);total=0.
    for i,step in enumerate(provider.program.steps):
        provider.contact_for_step(i,step)
        if step.formation_response:
            dose=step.formation_response.fog_dose;total+=dose
            integrated+=dose*provider.fog_rate_for_step(step.label,step.action.value)
    e=changed.chemistry.process_events['events'][0];wet=contact_duration(shape,0,shape[0],120.,e)
    np.testing.assert_allclose(integrated,total*wet/120.,atol=1e-9)
    hard=solution_hardening(changed,shape,validate_process_response(None),.4,{'bath':1,'duration_s':120.,'concentration_g_l':2.})
    np.testing.assert_allclose(hard,1.-.6*np.exp(-.008*wet),atol=1e-7)
    assert event()['rotation_instability']==0.
    with pytest.raises(ValueError,match='Unknown'):validate_process_events({'model':'film_process_events_v12','events':[event()]})
    with pytest.raises(ValueError,match='integer'):event(rotation_seed=.5)


def test_checkpoint_recovers_the_same_unstable_bath_and_ordered_surface(tmp_path):
    from film_foundry.tests.test_film_process_work import source,same_medium
    from film_foundry.app.film_process_work import start_work,continue_work
    from film_foundry.core.negative_io import load_developed_negative_npz
    c=config(rotation_instability=.75,rotation_seed=28)
    c.chemistry.process_events['events'].append({'kind':'silver_bath','after_bath':1,'solution_ml_m2':10.})
    initial=tmp_path/'start.ffprocess.npz';start_work(source(tmp_path,c),initial,c)
    mid=tmp_path/'mid.ffprocess.npz';continue_work(initial,mid,stop_after=3)
    a=continue_work(initial,tmp_path/'direct.npz');b=continue_work(mid,tmp_path/'resumed.npz')
    same_medium(load_developed_negative_npz(a.output_path),load_developed_negative_npz(b.output_path))


def test_editor_upgrades_only_on_edit_and_share_preserves_identity(tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.process_controls import condition_driven_config
    from film_foundry.core.effect_intent import compile_effect_intent
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    app=_application();c=configured(pause_s=30.123456)
    original=validate_process_events(c.chemistry.process_events)
    editor=ProcessOperationsEditor(original)
    try:
        assert editor.read()==original
        editor.controls['rotation_instability'].setValue(.75)
        editor.controls['rotation_seed'].setValue(31)
        c.chemistry.process_events=editor.read()
        assert c.chemistry.process_events['model']==EVENT_MODEL_V13
        assert c.chemistry.process_events['events'][0]['pause_s']==30.123456
        editor.kind.setCurrentIndex(editor.kind.findData('emulsion_shear'));editor.add()
        assert editor.read()['model']==EVENT_MODEL_V13
    finally:editor.close();app.processEvents()
    shared=config_from_share_code(encode_recipe_share(c));assert shared.chemistry.process_events==c.chemistry.process_events
    assert condition_driven_config(c).chemistry.process_events==c.chemistry.process_events
    c.chemistry.effect_intent={'model':'process_effect_intent_v3','enabled':True,'amounts':{'chemical_stain':.1}}
    compiled=compile_effect_intent(c)[0].chemistry.process_events
    assert compiled['model']==EVENT_MODEL_V13
    assert compiled['events'][0]==c.chemistry.process_events['events'][0]
