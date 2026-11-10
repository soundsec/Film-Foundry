from copy import deepcopy
import numpy as np
import pytest
from film_foundry.contracts.process_events import validate_process_events
from film_foundry.core.rotary_contact import contact_duration,motion_intervals


def event(**values):
    return validate_process_events({'model':'film_process_events_v14','events':[{
        'kind':'rotary_contact','rotation_instability':.7,'rotation_seed':7,
        'immersion_fraction':.4,'rotation_rpm':12.,'pause_s':6.,'pause_at_fraction':.25,
        'liquid_level_gradient':.3,'meniscus_retention_s':12.,**values}]})['events'][0]


def test_meniscus_integral_history_and_native_tiles(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    shape=(7,11);duration=20.;dt=.0005;e=event()
    y=((np.arange(7)+.5)/7-.5)[:,None];u=((np.arange(11)+.5)/11-.5)[None,:]
    activity=np.zeros(shape);dose=np.zeros(shape);part=np.zeros(shape)
    # Independent temporal integration of the reduced wet/dry contact law.
    for begin,end,moving,rpm,shift,angle in motion_intervals(duration,e):
        count=int(np.ceil((end-begin)/dt));step=(end-begin)/count
        for t in begin+(np.arange(count)+.5)*step:
            phase=np.mod(.5+y*np.cos(angle)+u*11/7*np.sin(angle)+shift+(moving*12+(t-begin)*rpm)/60,1.)
            fraction=.4+.3*u;wet=phase<fraction
            tau=5.+(12*np.exp(-np.maximum(phase-fraction,0)/.012) if rpm==0 else 0)
            delta=np.where(wet,step,activity*tau*(-np.expm1(-step/tau)))
            dose+=delta
            if 8<t<18:part+=delta
            activity=np.where(wet,1.,activity*np.exp(-step/tau))
    np.testing.assert_allclose(contact_duration(shape,0,7,duration,e),dose,atol=.003,rtol=0)
    np.testing.assert_allclose(contact_duration(shape,0,7,duration,e,(.4,.9)),part,atol=.003,rtol=0)
    full=contact_duration((71,113),0,71,duration,e)
    split=np.concatenate([contact_duration((71,113),a,b,duration,e) for a,b in ((0,23),(23,71))])
    np.testing.assert_array_equal(full,split)
    np.testing.assert_allclose(full,sum(contact_duration((71,113),0,71,duration,e,(i/4,(i+1)/4)) for i in range(4)),atol=3e-6)
    for fraction in (0.,1.):assert np.all(contact_duration(shape,0,7,duration,{**e,'immersion_fraction':fraction})==duration*fraction)
    old={k:v for k,v in e.items() if k not in ('liquid_level_gradient','meniscus_retention_s')}
    zero={**old,'liquid_level_gradient':0.,'meniscus_retention_s':0.}
    np.testing.assert_array_equal(contact_duration(shape,0,7,duration,old),contact_duration(shape,0,7,duration,zero))
    with pytest.raises(ValueError,match='Unknown'):validate_process_events({'model':'film_process_events_v13','events':[e]})


def test_cuda_contact_matches_cpu_and_falls_back(monkeypatch):
    from film_foundry.core import desktop_compute as gpu
    shape=(401,769);e=event();duration=20.
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    expected=contact_duration(shape,0,401,duration,e,(.17,.93))
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda')
    before=gpu.status()['rotary_calls']
    actual=contact_duration(shape,0,401,duration,e,(.17,.93))
    if gpu.status()['failure']:pytest.skip(gpu.status()['failure'])
    assert gpu.status()['rotary_calls']>before
    np.testing.assert_allclose(actual,expected,atol=2e-6,rtol=0)
    # Failure before publication recomputes the same entire CPU result.
    monkeypatch.setattr(gpu,'_failure','test unavailable backend')
    np.testing.assert_array_equal(contact_duration(shape,0,401,duration,e,(.17,.93)),expected)


def test_tapered_empty_and_full_arcs_do_not_create_liquid(monkeypatch):
    shape=(97,769);u=(np.arange(769)+.5)/769-.5
    for fraction in (.2,.8):
        e=event(immersion_fraction=fraction,liquid_level_gradient=1.)
        results=[]
        for backend in ('cpu','cuda'):
            monkeypatch.setenv('FILM_FOUNDRY_COMPUTE',backend)
            results.append(contact_duration(shape,0,97,20.,e))
        np.testing.assert_allclose(results[0],results[1],atol=2e-6,rtol=0)
        assert np.all(results[0][:,fraction+u<=0]==0.)
        assert np.all(results[0][:,fraction+u>=1]==20.)


def test_irregular_contact_and_checkpoint_preserve_actual_material(tmp_path):
    from film_foundry.core.process_operations import _footprint
    from film_foundry.tests.test_rotary_instability import config
    from film_foundry.tests.test_film_process_work import source,same_medium
    from film_foundry.app.film_process_work import start_work,continue_work
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.contracts.emulsion_integrity import validate_emulsion_integrity
    c=config();c.film.emulsion_integrity=validate_emulsion_integrity({'model':'wet_emulsion_integrity_v2'})
    c.chemistry.process_events=validate_process_events({'model':'film_process_events_v14','events':[
        event(pause_s=50.),{'kind':'emulsion_wear','after_bath':1,'pressure':.8,'temperature_c':43.,
            'radius_mm':4.,'edge_width_mm':1.,'boundary_variation':.8,'distribution_seed':19}]})
    wear=c.chemistry.process_events['events'][1]
    a=_footprint((301,401),(36.,24.),wear)
    np.testing.assert_array_equal(a,_footprint((301,401),(36.,24.),wear))
    assert np.count_nonzero((a>0)&(a<1))>1000
    assert not np.array_equal(a,_footprint((301,401),(36.,24.),{**wear,'distribution_seed':20}))
    initial=tmp_path/'start.ffprocess.npz';start_work(source(tmp_path,c),initial,c)
    mid=tmp_path/'mid.ffprocess.npz';continue_work(initial,mid,stop_after=3)
    direct=continue_work(initial,tmp_path/'direct.npz');resumed=continue_work(mid,tmp_path/'resumed.npz')
    a=load_developed_negative_npz(direct.output_path);b=load_developed_negative_npz(resumed.output_path)
    same_medium(a,b)
    assert np.min(a.process_surface['emulsion_coverage'])<.99


def test_editor_preserves_old_recipe_and_exposes_new_contact(tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor
    from film_foundry.tests.test_rotary_instability import config
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    app=_application();c=config();old=validate_process_events(c.chemistry.process_events)
    editor=ProcessOperationsEditor(old)
    try:
        assert editor.read()==old
        editor.controls['meniscus_retention_s'].setValue(12.)
        new=editor.read();assert new['model']=='film_process_events_v14'
        c.chemistry.process_events=new
        assert config_from_share_code(encode_recipe_share(c)).chemistry.process_events==new
        editor.kind.setCurrentIndex(editor.kind.findData('emulsion_wear'));editor.add()
        assert editor.read()['events'][-1]['boundary_variation']>.5
    finally:editor.close();app.processEvents()
