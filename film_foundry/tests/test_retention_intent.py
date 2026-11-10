from copy import deepcopy
from dataclasses import asdict,replace
import numpy as np
import pytest
from film_foundry.tests.test_bath_solution import configured,solution
from film_foundry.tests.test_custom_process_steps import _material,_latent
from film_foundry.contracts.effect_intent import MODEL_V3,MODEL_V2
from film_foundry.core.effect_intent import compile_effect_intent
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.core.film_process.operators import apply_process_program


def config(amount=.5,native=True):
    cfg=configured();cfg.process_control_mode='expected'
    if native:cfg.chemistry.process_steps=None
    cfg.chemistry.effect_intent={'model':MODEL_V3,'enabled':True,'amounts':{'silver_retention':amount}}
    return cfg


@pytest.mark.parametrize('kind',('native','custom','coupled','repeated'))
def test_retention_is_linear_in_surviving_metal_and_does_not_change_dye(kind):
    values=[];dyes=[]
    for amount in (0.,.25,.5,.75,1.):
        cfg=config(amount,kind=='native')
        if kind=='coupled':
            cfg.chemistry.process_steps[1]['solution'].update(model='bath_solution_v2',secondary={'action':'fix_halide','rate_per_minute':.3})
        if kind=='repeated':cfg.chemistry.process_steps=(*cfg.chemistry.process_steps,{'action':'bleach_silver','solution':solution('bleach_silver')},{'action':'fix_halide'})
        original=asdict(cfg);compiled,audit=compile_effect_intent(cfg,(16,24))
        state=apply_process_program(_material(),_latent(),program_from_develop_recipe(compiled.chemistry)).state
        values.append(float(state.metallic_silver.mean()));dyes.append(state.dye.copy())
        assert asdict(cfg)==original and compiled.chemistry.silver_retention==0.
        if amount:assert audit['generated_bath_overrides']
    np.testing.assert_allclose(values,np.linspace(values[0],values[-1],5),atol=1e-7,rtol=2e-6)
    for dye in dyes[1:]:np.testing.assert_array_equal(dye,dyes[0])


def test_native_override_preserves_other_stage_semantics_and_has_matching_contact():
    from film_foundry.core.process_operations import BathContactRate,source_bath_rows
    cfg=config();cfg.chemistry.program_key='color_reversal';cfg.chemistry.temperature_c=30.
    original=program_from_develop_recipe(cfg.chemistry)
    changed,_=compile_effect_intent(cfg)
    actual=program_from_develop_recipe(changed.chemistry)
    for a,b in zip(original.steps,actual.steps):
        if a.action.value!='bleach_silver':assert a==replace(b,bath_index=a.bath_index)
    index=next(i for i,row in enumerate(source_bath_rows(changed,actual)) if row['action']=='bleach_silver')
    changed.chemistry.process_events={'model':'film_process_events_v6','events':[{'kind':'bath_contact','bath':index+1,'fill_s':200.}]}
    provider=BathContactRate(changed,(12,18));step=next(s for s in actual.steps if s.action.value=='bleach_silver')
    field=provider.contact_for_step(index,step)
    assert field[0].mean()>field[-1].mean() and np.max(field)<=1.


def test_retention_modes_zero_off_old_versions_and_roundtrip(tmp_path):
    from film_foundry.core.engine import develop_negative
    from film_foundry.core.process_controls import condition_driven_config
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    scene=np.linspace(.02,.9,16*24*3,dtype=np.float32).reshape(16,24,3)
    cfg=config();compiled,_=compile_effect_intent(cfg,scene.shape[:2])
    np.testing.assert_array_equal(develop_negative(scene,cfg).optical_density_rgb,develop_negative(scene,condition_driven_config(compiled)).optical_density_rgb)
    shared=config_from_share_code(encode_recipe_share(compiled))
    assert shared.chemistry.bath_overrides==compiled.chemistry.bath_overrides
    cfg.chemistry.effect_intent['enabled']=False;off,_=compile_effect_intent(cfg)
    zero,_=compile_effect_intent(config(0.))
    assert off.chemistry.bath_overrides is None and program_from_develop_recipe(off.chemistry)==program_from_develop_recipe(zero.chemistry)
    cfg.chemistry.silver_retention=.7;cfg.chemistry.effect_intent={'model':MODEL_V2,'amounts':{}}
    old,_=compile_effect_intent(cfg);assert old.chemistry.silver_retention==.7


def test_native_editor_preserves_override_precision_and_program():
    from film_foundry.ui.qt import _application
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    from film_foundry.ui.bath_override_dialog import BathOverrideDialog
    from film_foundry.core.process_operations import source_bath_rows
    app=_application();cfg,_=compile_effect_intent(config())
    cfg.chemistry.time_min=6.123456789
    dialog=RollProcessDialog(cfg)
    try:
        assert not dialog.custom.isChecked()
        dialog.apply();assert dialog.config.chemistry.process_steps is None
        assert dialog.config.chemistry.bath_overrides==cfg.chemistry.bath_overrides
        assert dialog.config.chemistry.time_min==cfg.chemistry.time_min
        rows=source_bath_rows(cfg,program_from_develop_recipe(cfg.chemistry))
        override=BathOverrideDialog(2,rows[1],cfg.chemistry)
        try:
            override.apply();assert override.value['solution']==cfg.chemistry.bath_overrides[0]['solution']
            from film_foundry.ui.bath_solution_dialog import BathSolutionDialog
            capability=BathSolutionDialog('bleach_silver',override.value['solution'])
            try:
                capability.apply();assert capability.value==override.value['solution']
            finally:capability.close()
        finally:override.close()
    finally:dialog.close();app.processEvents()


def test_unreachable_retention_and_stale_bath_override_rejected():
    cfg=config();cfg.chemistry.program_key='bw_negative'
    resolved,audit=compile_effect_intent(cfg)
    assert resolved.chemistry.bath_overrides is None and 'retention_unreachable' in audit['targets']
    cfg,_=compile_effect_intent(config());cfg.chemistry.program_key='bw_negative'
    with pytest.raises(ValueError,match='number/action'):program_from_develop_recipe(cfg.chemistry)


def test_six_effects_freeze_material_and_archive_actual_native_bath(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.engine import develop_negative,expose_image_to_latent,develop_negative_from_latent
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.core.process_controls import condition_driven_config
    from film_foundry.contracts.effect_intent import EFFECTS_V3
    cfg=config();cfg.chemistry.effect_intent['amounts']={key:.4 for key in EFFECTS_V3}
    scene=np.linspace(.03,.95,16*24*3,dtype=np.float32).reshape(16,24,3)
    resolved,_=compile_effect_intent(cfg,scene.shape[:2])
    expected=develop_negative(scene,cfg)
    np.testing.assert_array_equal(expected.optical_density_rgb,develop_negative(scene,condition_driven_config(resolved)).optical_density_rgb)
    latent=expose_image_to_latent(scene,cfg);first=develop_negative_from_latent(latent.copy(),cfg)
    changed=deepcopy(cfg);changed.film.cross_process_silver_bleach=0.;changed.film.image_polarity='positive'
    np.testing.assert_array_equal(first.optical_density_rgb,develop_negative_from_latent(latent.copy(),changed).optical_density_rgb)
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene*255)).save(source)
    result=run_request(WorkflowRequest('full',source,tmp_path/'scan.png',cfg))
    medium=load_developed_negative_npz(result.medium_path)
    trace=medium.metadata['process_control']
    assert trace['bath_overrides'][0]['bath']==2 and trace['bath_kinetics'][1]['model']=='bath_solution_v1'
    assert trace['effect_intent']['intent']['model']==MODEL_V3
