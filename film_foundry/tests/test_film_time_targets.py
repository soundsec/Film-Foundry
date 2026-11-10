"""Target inversion must reproduce actual clocks and preserve UI ownership."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
import json
import numpy as np
import pytest
from film_foundry.app.film_time_targets import describe_targets, resolve_target
from film_foundry.contracts.film_time import MODELS, validate_history, validate_response
from film_foundry.core.film_process.time_intent import describe_time_target, resolve_time_segment
from film_foundry.core.film_process.time_history import storage_response, age_latent, light_residual_halide, light_surface_halide
from film_foundry.tests.test_film_time_history import config, history
from film_foundry.tests.test_custom_process_steps import _latent


def clock(kind, segment):
    return validate_history({'model': MODELS[kind], 'segments': [segment]}, kind)


@pytest.mark.parametrize('layers', [1, 3])
def test_linear_targets_match_actual_storage_wait_and_light(layers):
    response=validate_response({'model':'film_time_response_v2',
        'latent_fade_rate_per_day':[.02,.005,0.],
        'latent_fog_rate_per_day':[.001,.003,.007]})
    conditions={'temperature_c':33.,'relative_humidity':.8}
    for target in (0.,.15,.3,.6):
        solved=resolve_time_segment(response,'storage',target,conditions=conditions,layers=layers)
        speed,fog=storage_response(SimpleNamespace(time_response=response,storage_history=clock('storage',solved['segment'])),layers) or (np.ones(layers),np.zeros(layers))
        assert float((-np.log2(speed)).mean())==pytest.approx(target,abs=1e-7)
        np.testing.assert_allclose(fog,solved['linked_response']['storage_fog_fraction'],atol=1e-8)
        solved=resolve_time_segment(response,'latent',target,conditions=conditions,layers=layers)
        state=SimpleNamespace(halide=np.ones((2,1,layers),np.float32),
            developability=np.broadcast_to(np.array([0.,1.],np.float32)[:,None,None],(2,1,layers)).copy())
        age_latent(state,clock('latent',solved['segment']),response)
        retained=state.developability[1,0]-state.developability[0,0]
        assert 1-float(retained.mean())==pytest.approx(target,abs=1e-7)
        np.testing.assert_allclose(state.developability[0,0],solved['linked_response']['latent_activation_offset'],atol=1e-7)
        np.testing.assert_array_equal(state.halide,1.)
        solved=resolve_time_segment(response,'light',target,
            conditions={'relative_illuminance':2.,'light_rgb':[.2,1.,2.]},layers=layers)
        # Equal reference salt in each layer; actual unequal pools keep their mass.
        state=_latent()
        for key in ('halide','developability','metallic_silver','coupler','dye'):
            setattr(state,key,getattr(state,key)[...,:layers].copy())
        state.bleached_halide=np.full_like(state.halide,.3)
        before=state.halide.copy();dye=state.dye.copy()
        light_residual_halide(state,clock('light',solved['segment']),response)
        assert float((1-state.halide/before).mean())==pytest.approx(target,abs=1e-7)
        np.testing.assert_allclose(1-state.bleached_halide/.3,np.broadcast_to(solved['linked_response']['bleached_halide_conversion'],state.halide.shape),atol=1e-7)
        np.testing.assert_allclose(state.halide+state.bleached_halide+state.metallic_silver,1.3,atol=2e-7)
        np.testing.assert_array_equal(state.dye,dye)
        fields={'surface_halide_mg_m2':np.ones((2,3),np.float32),'surface_silver_mg_m2':np.zeros((2,3),np.float32)}
        light_surface_halide(fields,clock('light',solved['segment']),response)
        np.testing.assert_allclose(fields['surface_silver_mg_m2'],solved['linked_response']['surface_halide_conversion'],atol=1e-7)


def test_reachability_zero_rates_capacity_and_high_rate_precision():
    disabled=describe_time_target(None,'latent');assert not disabled['available']
    with pytest.raises(ValueError):resolve_time_segment(None,'latent',0.)
    zero=validate_response({'model':'film_time_response_v1','latent_fade_rate_per_day':[0.]*3,'latent_fog_rate_per_day':[0.]*3})
    assert not describe_time_target(zero,'latent')['available']
    assert resolve_time_segment(zero,'latent',0.)['segment']['duration_hours']==0.
    with pytest.raises(ValueError):resolve_time_segment(zero,'latent',.1)
    limited={**zero,'printout_rate_per_light_hour':[1.,0.,0.]}
    report=describe_time_target(limited,'light');assert report['reachable']['maximum']==pytest.approx(1/3)
    assert not report['reachable']['maximum_inclusive']
    with pytest.raises(ValueError):resolve_time_segment(limited,'light',1/3)
    slow={**zero,'latent_fade_rate_per_day':[1e-8]*3}
    report=describe_time_target(slow,'latent')
    with pytest.raises(ValueError):resolve_time_segment(slow,'latent',report['reachable']['maximum']*2)
    solved=resolve_time_segment(slow,'latent',report['reachable']['maximum'])
    assert solved['segment']['duration_hours']==pytest.approx(876000.)
    high={**zero,'printout_rate_per_light_hour':[10.]*3}
    solved=resolve_time_segment(high,'light',1e-7,conditions={'relative_illuminance':10000.,'light_rgb':[4.]*3})
    assert solved['achieved']==pytest.approx(1e-7,rel=1e-10)
    assert solved['segment']['duration_hours']<1e-10
    for bad in (True,float('nan'),-1.,1.1):
        with pytest.raises(ValueError):resolve_time_segment(high,'light',bad)


def test_headless_readonly_modes_share_and_actual_pipeline():
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.engine import develop_negative
    from film_foundry.core.process_controls import condition_driven_config
    from film_foundry.core.process_couplings import describe_process_couplings
    c=config();before=asdict(c)
    for kind in MODELS:
        json.dumps(describe_targets(c,kind),allow_nan=False)
    solved=resolve_target(c,'latent',.2)
    assert asdict(c)==before
    c.chemistry.latent_wait=clock('latent',solved['segment'])
    c.chemistry.post_process_light=clock('light',resolve_target(c,'light',.25)['segment'])
    c.chemistry.fixer_exhaustion=.7
    c.process_control_mode='expected'
    restored=config_from_share_code(encode_recipe_share(c))
    assert restored.chemistry.latent_wait==c.chemistry.latent_wait
    image=np.linspace(.02,.7,9*13*3,dtype=np.float32).reshape(9,13,3)
    a_rng=np.random.default_rng(41);b_rng=np.random.default_rng(41)
    a=develop_negative(image,c,rng=a_rng)
    b=develop_negative(image,condition_driven_config(restored),rng=b_rng)
    np.testing.assert_array_equal(a.optical_density_rgb,b.optical_density_rgb)
    assert a_rng.bit_generator.state==b_rng.bit_generator.state
    codes={v['code'] for v in describe_process_couplings(c)['findings']}
    assert {'film_latent_time','film_light_time'}<=codes
    c.chemistry.program_key='legacy_density'
    assert not describe_targets(c,'light')['available']
    with pytest.raises(ValueError):resolve_target(c,'light',.2)


def test_actual_editors_resolve_only_selected_segment_and_use_live_stock(tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application,QtWidgets
    from film_foundry.ui.film_time_editor import FilmTimeDialog
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    for name in ('UI_SETTINGS','CAMERA_SETTINGS','CATALOG'):
        monkeypatch.setenv('FILM_FOUNDRY_'+name,str(tmp_path/(name+'.json')))
    app=_application();c=config();c.film.storage_history=history('storage',100.)
    c.chemistry.latent_wait=history('latent',10.);c.chemistry.post_process_light=history('light',1.)
    original=asdict(c);stock=FilmTimeDialog(c,material=True);process=FilmTimeDialog(c);roll=RollProcessDialog(c)
    try:
        editor=stock.storage;editor.target.setValue(.5);editor.solve.click()
        first=editor.read()['segments'][0]['duration_days']
        for w in stock.controls['storage_speed_loss_stops_per_year']:w.setValue(w.value()*2)
        editor.solve.click();assert editor.read()['segments'][0]['duration_days']==pytest.approx(first/2)
        stock.enabled.setChecked(False);assert not editor.solve.isEnabled()
        stock.enabled.setChecked(True)
        editor=process.waiting;original_row=deepcopy(editor.read()['segments'][0])
        editor.add_row({'duration_hours':20.,'temperature_c':32.,'relative_humidity':.75})
        editor.target.setValue(.25);editor.solve.click()
        rows=editor.read()['segments'];assert rows[0]==original_row
        assert rows[1]['temperature_c']==32. and rows[1]['relative_humidity']==.75
        assert rows[1]['duration_hours']>20.
        editor.table.setItem(1,1,QtWidgets.QTableWidgetItem('bad'));assert not editor.solve.isEnabled()
        editor.table.item(1,1).setText('32');assert editor.solve.isEnabled()
        editor.enabled.setChecked(False);assert editor.read() is None and not editor.solve.isEnabled()
        editor.enabled.setChecked(True)
        roll.post_light.target.setValue(.3);roll.post_light.solve.click()
        assert roll.post_light.read()['segments'][0]['duration_hours']>1.
        stock.apply();process.apply();roll.apply()
        assert stock.result()==process.result()==roll.result()==1
        assert asdict(c)==original
        assert 'mean layer' in process.waiting.target.toolTip() or '平均' in process.waiting.target.toolTip()
    finally:
        stock.close();process.close();roll.close();app.processEvents()
