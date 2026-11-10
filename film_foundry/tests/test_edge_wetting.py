from copy import deepcopy
import numpy as np
import pytest
from film_foundry.contracts.process_events import validate_process_events
from film_foundry.core.rotary_contact import contact_duration


def event(**kwargs):
    return validate_process_events({'model':'film_process_events_v15','events':[{
        'kind':'rotary_contact','wetting_model':'edge_front_v1','contact_edge':'bottom',
        'immersion_fraction':.6,'rotation_instability':.7,'rotation_seed':7,
        'rotation_rpm':12.,'pause_at_fraction':.25,'pause_s':50.,
        'meniscus_width_mm':.8,'meniscus_roughness_mm':1.,'meniscus_retention_s':12.,
        'residue_concentration_mg_l':500.,**kwargs}]})['events'][0]


def test_front_is_edge_connected_soft_seeded_and_windows_preserve_history(monkeypatch):
    from film_foundry.core.edge_wetting import front_plan,wet_coverage
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    shape=(97,153);extent=(36.,24.);e=event();duration=120.
    for side in ('bottom','top'):
        ev={**e,'contact_edge':side};whole=contact_duration(shape,0,97,duration,ev,extent=extent)
        # Every threshold is attached to the selected edge, never a middle strip.
        ordered=whole if side=='bottom' else whole[::-1]
        assert np.min(np.diff(ordered,axis=0))>=-1e-5
        plan=front_plan(shape,extent,duration,ev)
        for row in plan:
            wet,distance=wet_coverage(shape,extent,row[4:],ev)
            assert np.any((wet>0)&(wet<1))
            curve=row[4:];x=np.arange(len(curve));fit=np.polyval(np.polyfit(x,curve,1),x)
            assert np.std(curve-fit)>.1
        tiled=np.concatenate([contact_duration(shape,a,b,duration,ev,extent=extent) for a,b in ((0,21),(21,97))])
        np.testing.assert_array_equal(whole,tiled)
        split=sum(contact_duration(shape,0,97,duration,ev,(i/7,(i+1)/7),extent=extent) for i in range(7))
        np.testing.assert_allclose(whole,split,atol=2e-5,rtol=0)
        assert whole.min()>=0 and whole.max()<=duration
    for fraction in (0.,1.):assert np.all(contact_duration(shape,0,97,duration,event(immersion_fraction=fraction))==120*fraction)
    with pytest.raises(ValueError,match='Unknown'):validate_process_events({'model':'film_process_events_v14','events':[e]})


def test_edge_gpu_matches_cpu_and_preserves_legacy(monkeypatch):
    from film_foundry.core import desktop_compute as gpu
    from film_foundry.tests.test_contact_morphology import event as old_event
    shape=(271,433);e=event();outputs=[]
    for backend in ('cpu','cuda'):
        monkeypatch.setenv('FILM_FOUNDRY_COMPUTE',backend)
        outputs.append(contact_duration(shape,0,271,120.,e,(.2,.91)))
    if gpu.status()['failure']:pytest.skip(gpu.status()['failure'])
    assert gpu.status()['edge_contact_calls']>0
    np.testing.assert_allclose(*outputs,atol=1e-5,rtol=0)
    old=old_event(pause_s=40.)
    a=contact_duration(shape,0,271,120.,old)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    np.testing.assert_allclose(a,contact_duration(shape,0,271,120.,old),atol=1e-5,rtol=0)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda');monkeypatch.setattr(gpu,'_failure','unavailable test GPU')
    np.testing.assert_array_equal(outputs[0],contact_duration(shape,0,271,120.,e,(.2,.91)))


def test_finite_rim_load_washes_out_and_no_solute_means_no_stain():
    from film_foundry.core.edge_wetting import residue_deposit,front_plan,wet_coverage
    from film_foundry.core.film_process.surface import SurfaceProcessSession
    from film_foundry.tests.test_rotary_instability import config
    shape=(101,151);extent=(36.,24.);e=event()
    deposit,report=residue_deposit(shape,extent,120.,e)
    plan=front_plan(shape,extent,120.,e);pause=plan[plan[:,2]==0.][-1]
    wet,_=wet_coverage(shape,extent,pause[4:],e)
    np.testing.assert_allclose(deposit.mean(dtype=np.float64),(wet*10).mean(),atol=1e-6,rtol=0)
    assert report['redistributed_mg_m2']>0 and deposit.max()>10
    assert np.count_nonzero((deposit>0)&(deposit<.1))>100  # diffuse tail, no binary line
    assert np.all(residue_deposit(shape,extent,120.,event(residue_concentration_mg_l=0.))[0]==0)
    c=config();c.chemistry.process_events={'model':'film_process_events_v15','events':[e]}
    c.film.process_response={'model':'creative_process_response_v4','binding_rate_s':0.}
    before,audit=SurfaceProcessSession(c,shape).finish(generate_texture=False)
    washed=deepcopy(c);washed.chemistry.process_events['events'].append({'kind':'wash','after_bath':1,'duration_s':600.,'water_changes':20.})
    after,wa=SurfaceProcessSession(washed,shape).finish(generate_texture=False)
    assert np.mean(after['soluble_residue_mg_m2'])<np.mean(before['soluble_residue_mg_m2'])*.001
    balance=wa['balance']
    assert abs(balance['residue_added_mg_m2']-balance['residue_washed_mg_m2']-balance['residue_remaining_mg_m2'])<1e-5


def test_residue_is_deposited_once_across_checkpoint_and_editor_shares_it(tmp_path,monkeypatch):
    from film_foundry.tests.test_film_process_work import source,same_medium
    from film_foundry.tests.test_rotary_instability import config
    from film_foundry.app.film_process_work import start_work,continue_work
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    c=config();original=validate_process_events(c.chemistry.process_events)
    app=_application();editor=ProcessOperationsEditor(original)
    try:
        assert editor.read()==original
        combo=editor.controls['wetting_model'];combo.setCurrentIndex(combo.findData('edge_front_v1'))
        editor.controls['residue_concentration_mg_l'].setValue(500.)
        c.chemistry.process_events=editor.read()
        assert c.chemistry.process_events['model']=='film_process_events_v15'
        assert config_from_share_code(encode_recipe_share(c)).chemistry.process_events==c.chemistry.process_events
    finally:editor.close();app.processEvents()
    initial=tmp_path/'start.ffprocess.npz';start_work(source(tmp_path,c),initial,c)
    mid=tmp_path/'mid.ffprocess.npz';continue_work(initial,mid,stop_after=3)
    a=continue_work(initial,tmp_path/'direct.npz');b=continue_work(mid,tmp_path/'resumed.npz')
    direct=load_developed_negative_npz(a.output_path);resumed=load_developed_negative_npz(b.output_path)
    same_medium(direct,resumed)
    assert np.max(direct.process_surface['soluble_residue_mg_m2'])>0
