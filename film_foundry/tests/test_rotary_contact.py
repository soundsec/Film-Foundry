"""Bounded rotary contact, actual bath clocks and preserved old operations."""
from copy import deepcopy
import numpy as np
import pytest
from film_foundry.contracts.process_events import EVENT_MODEL_V12,validate_process_events
from film_foundry.core.rotary_contact import contact_duration,cumulative_contact,DRAINAGE_TIME_S
from film_foundry.model.config import DarkroomConfig


def event(**values):
    return validate_process_events({'model':EVENT_MODEL_V12,'events':[{'kind':'rotary_contact',**values}]})['events'][0]


def configured(**values):
    c=DarkroomConfig(enable_grain=False,enable_mtf=False,enable_halation=False,random_seed=19,seed_strategy='fixed')
    c.chemistry.process_variation=0.;c.film.color_process='bw';c.chemistry.program_key='bw_negative'
    c.chemistry.process_steps=({'action':'develop_silver','time_min':2.,'solution':{'model':'bath_solution_v3','action':'develop_silver',
        'rate_per_minute':.8,'formation_response':{},'secondary':{'action':'fix_halide','rate_per_minute':.1}}},)
    c.chemistry.process_events={'model':EVENT_MODEL_V12,'events':[event(**values)]}
    return c


def test_closed_form_matches_time_integration_and_has_bounded_edges():
    e=event(immersion_fraction=.37,rotation_rpm=17.,pause_at_fraction=.23,pause_s=3.1)
    phases=(np.arange(13)+.5)/13;duration=12.3;dt=.001
    expected=cumulative_contact(phases,duration,duration,e)
    activity=np.zeros(13);dose=np.zeros(13);decay=np.exp(-dt/DRAINAGE_TIME_S)
    start=duration*e['pause_at_fraction'];end=start+e['pause_s']
    for t in (np.arange(round(duration/dt))+.5)*dt:
        moving=t-np.clip(t-start,0.,e['pause_s'])
        wet=np.mod(phases+moving*e['rotation_rpm']/60.,1.)<e['immersion_fraction']
        dose+=np.where(wet,dt,activity*DRAINAGE_TIME_S*(1.-decay))
        activity=np.where(wet,1.,activity*decay)
    np.testing.assert_allclose(expected,dose,atol=.003,rtol=0)
    shape=(37,21);whole=contact_duration(shape,0,37,120.,e)
    tiled=np.concatenate([contact_duration(shape,a,b,120.,e) for a,b in ((0,9),(9,23),(23,37))])
    np.testing.assert_array_equal(whole,tiled)
    pieces=sum(contact_duration(shape,0,37,120.,e,(i/8,(i+1)/8)) for i in range(8))
    np.testing.assert_allclose(whole,pieces,atol=2e-5)
    assert not contact_duration(shape,0,37,120.,event(immersion_fraction=0.)).any()
    assert np.all(contact_duration(shape,0,37,120.,event(immersion_fraction=1.))==120.)
    stopped=contact_duration(shape,0,37,120.,event(rotation_rpm=0.))
    assert stopped[0,0]==120. and stopped[-1,0]==0.
    with pytest.raises(ValueError,match='pause'):contact_duration(shape,0,37,10.,event(pause_s=6.))
    with pytest.raises(ValueError,match='v12'):validate_process_events({'model':'film_process_events_v11','events':[e]})
    with pytest.raises(ValueError,match='One contact'):validate_process_events({'model':EVENT_MODEL_V12,'events':[e,{'kind':'bath_contact','bath':1}]})


def test_reaction_windows_share_contact_with_fog_and_hardening():
    from film_foundry.core.process_operations import BathContactRate,solution_hardening
    from film_foundry.contracts.process_events import validate_process_response
    from film_foundry.core.engine import develop_negative
    c=configured(pause_s=50.,pause_at_fraction=.25);shape=(72,104)
    provider=BathContactRate(c,shape);program=provider.program;integrated=np.zeros(shape,np.float64);total=0.
    for i,step in enumerate(program.steps):
        provider.contact_for_step(i,step)
        if step.formation_response:
            dose=step.formation_response.fog_dose;total+=dose
            integrated+=dose*provider.fog_rate_for_step(step.label,step.action.value)
    e=c.chemistry.process_events['events'][0];contact=contact_duration(shape,0,72,120.,e)
    np.testing.assert_allclose(integrated,total*contact/120.,atol=1e-9)
    hard=solution_hardening(c,shape,validate_process_response(None),.4,{'bath':1,'duration_s':120.,'concentration_g_l':2.})
    expected=1.-.6*np.exp(-.004*2.*contact)
    np.testing.assert_allclose(hard,expected,atol=1e-7)
    image=np.full((*shape,3),.34,np.float32)
    paused=develop_negative(image,c,rng=np.random.default_rng(19))
    continuous=deepcopy(c);continuous.chemistry.process_events['events'][0]['pause_s']=0.
    normal=develop_negative(image,continuous,rng=np.random.default_rng(19))
    assert np.max(np.abs(paused.density_cmy-normal.density_cmy))>.005
    assert np.ptp(paused.optical_density_rgb.mean((1,2)))>np.ptp(normal.optical_density_rgb.mean((1,2)))


def test_v12_checkpoint_and_neutral_old_operations_keep_pixels_and_rng(tmp_path):
    from film_foundry.tests.test_film_process_work import source,same_medium,scene
    from film_foundry.app.film_process_work import start_work,continue_work
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.core.engine import develop_negative
    c=configured(pause_s=50.,pause_at_fraction=.25)
    c.chemistry.process_events['events'].append({'kind':'silver_bath','after_bath':1,'solution_ml_m2':10.})
    initial=tmp_path/'start.ffprocess.npz';start_work(source(tmp_path,c),initial,c)
    mid=tmp_path/'mid.ffprocess.npz';continue_work(initial,mid,stop_after=3)
    a=continue_work(initial,tmp_path/'direct.npz');b=continue_work(mid,tmp_path/'resumed.npz')
    same_medium(load_developed_negative_npz(a.output_path),load_developed_negative_npz(b.output_path))
    old=configured();old.chemistry.process_events={'model':'film_process_events_v11','events':[{'kind':'bath_contact','bath':1,'fill_s':35.,'immersion_fraction':.7}]}
    new=deepcopy(old);new.chemistry.process_events['model']=EVENT_MODEL_V12
    ra=np.random.default_rng(3);rb=np.random.default_rng(3)
    x=develop_negative(scene(),old,rng=ra);y=develop_negative(scene(),new,rng=rb)
    np.testing.assert_array_equal(x.optical_density_rgb,y.optical_density_rgb)
    assert ra.bit_generator.state==rb.bit_generator.state


def test_rotary_editor_share_and_effect_mode_keep_new_contact(tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.process_controls import condition_driven_config
    from film_foundry.core.effect_intent import compile_effect_intent
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    app=_application();c=configured(pause_s=30.123456,pause_at_fraction=.321654)
    editor=ProcessOperationsEditor(c.chemistry.process_events)
    try:
        assert editor.read()==validate_process_events(c.chemistry.process_events)
        editor.controls['rotation_rpm'].setValue(15.)
        c.chemistry.process_events=editor.read();assert c.chemistry.process_events['events'][0]['rotation_rpm']==15.
        assert 'after_bath' not in editor.controls
    finally:editor.close();app.processEvents()
    shared=config_from_share_code(encode_recipe_share(c));assert shared.chemistry.process_events==c.chemistry.process_events
    assert condition_driven_config(c).chemistry.process_events==c.chemistry.process_events
    c.chemistry.effect_intent={'model':'process_effect_intent_v3','enabled':True,'amounts':{'chemical_stain':.1}}
    assert compile_effect_intent(c)[0].chemistry.process_events['model']==EVENT_MODEL_V12
