from copy import deepcopy
import numpy as np
from film_foundry.tests.test_process_operations import config
from film_foundry.contracts.process_events import EVENT_MODEL_V4,RESPONSE_MODEL_V2,validate_process_response

def fixer_config(before=1):
    cfg=config();cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'bleach_silver'},{'action':'fix_halide'})
    cfg.chemistry.process_events={'model':EVENT_MODEL_V4,'events':[{'kind':'fixer_contact','before_bath':before,'concentration_mmol_l':40.,'duration_s':60.}]}
    return cfg

def test_premature_fixing_uses_existing_selective_operator_and_preserves_formed_dye():
    from film_foundry.tests.test_custom_process_steps import _material,_latent
    from film_foundry.core.film_process.operators import apply_process_program
    from film_foundry.core.process_operations import BathContactRate
    from film_foundry.core.film_process.recipe import program_from_develop_recipe
    cfg=fixer_config();program=program_from_develop_recipe(cfg.chemistry)
    plain=apply_process_program(_material(),_latent(),program)
    early=apply_process_program(_material(),_latent(),program,local_development_rate=BathContactRate(cfg,(1,3)))
    assert early.state.dye.sum()<plain.state.dye.sum()
    assert early.trace[0].label=='fixer_contact_before_bath_1' and early.trace[0].reacted_amount>0
    cfg.chemistry.process_events['events'][0]['before_bath']=2
    late=apply_process_program(_material(),_latent(),program,local_development_rate=BathContactRate(cfg,(1,3)))
    np.testing.assert_array_equal(plain.state.dye,late.state.dye)
    cfg.chemistry.process_events['events'][0]['before_bath']=1
    cfg.chemistry.process_events['events'][0]['concentration_mmol_l']=0
    zero=apply_process_program(_material(),_latent(),program,local_development_rate=BathContactRate(cfg,(1,3)))
    np.testing.assert_array_equal(plain.state.dye,zero.state.dye)

def test_material_response_controls_incident_and_freezes_in_latent(tmp_path):
    from film_foundry.core.engine import develop_negative,expose_image_to_latent,develop_negative_from_latent
    cfg=fixer_config();scene=np.full((24,36,3),.4,np.float32)
    slow=deepcopy(cfg);slow.film.process_response={'model':RESPONSE_MODEL_V2,'fixer_contact_rate_l_mmol_s':.00001}
    fast=deepcopy(cfg);fast.film.process_response={'model':RESPONSE_MODEL_V2,'fixer_contact_rate_l_mmol_s':.01}
    a=develop_negative(scene,slow);b=develop_negative(scene,fast)
    assert a.density_cmy.sum()>b.density_cmy.sum()
    latent=expose_image_to_latent(scene,slow)
    expected=develop_negative_from_latent(latent.copy(),slow)
    actual=develop_negative_from_latent(latent.copy(),fast)
    np.testing.assert_array_equal(actual.optical_density_rgb,expected.optical_density_rgb)
    cfg.processing.material_tile_rows=7;cfg.processing.material_tile_threshold_megapixels=.0001
    tiled=develop_negative(scene,cfg);cfg.processing.material_tile_threshold_megapixels=100
    full=develop_negative(scene,cfg)
    np.testing.assert_allclose(full.optical_density_rgb,tiled.optical_density_rgb,atol=1e-6,rtol=1e-6)
    assert full.metadata['film_process_model']['premature_fixing']['target']=='halide_and_bleached_halide'
    from film_foundry.core.process_operations import BathContactRate
    cfg.chemistry.process_events={'model':EVENT_MODEL_V4,'events':[{'kind':'developer_inhibitor','before_bath':1}]}
    cfg.film.process_response={'model':RESPONSE_MODEL_V2,'inhibitor_half_response_mmol_m2':.01}
    sensitive=BathContactRate(cfg,(24,36)).inhibitor_fields[0]
    cfg.film.process_response['inhibitor_half_response_mmol_m2']=10.
    tolerant=BathContactRate(cfg,(24,36)).inhibitor_fields[0]
    assert sensitive.mean()<tolerant.mean()

def test_domains_cover_actions_and_response_editor_persists_new_controls():
    from film_foundry.contracts.process_steps import ACTIONS
    from film_foundry.contracts.bath_domains import BATH_DOMAINS
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessResponseDialog,ProcessOperationsEditor
    assert set(ACTIONS)==set(BATH_DOMAINS)
    assert 'inhibitor_half_response_mmol_m2' not in validate_process_response(None)
    _application();dialog=ProcessResponseDialog(None)
    try:
        dialog.controls['inhibitor_half_response_mmol_m2'].setValue(.7);dialog.apply()
        assert dialog.value['model']==RESPONSE_MODEL_V2 and dialog.value['inhibitor_half_response_mmol_m2']==.7
    finally:dialog.close()
    editor=ProcessOperationsEditor(fixer_config().chemistry.process_events)
    try:assert editor.read()['model']==EVENT_MODEL_V4
    finally:editor.close()

def test_fixer_incident_survives_spectral_archive_and_readonly_scan(tmp_path):
    from PIL import Image
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.core.scan_runtime import scan_medium_output
    cfg=configuration();cfg.enable_grain=False
    cfg.chemistry.process_events=fixer_config().chemistry.process_events
    cfg.film.process_response={'model':RESPONSE_MODEL_V2,'fixer_contact_rate_l_mmol_s':.002}
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    result=run_request(WorkflowRequest('develop',source,tmp_path/'medium.npz',cfg))
    medium=load_developed_negative_npz(result.medium_path)
    assert medium.spectral_medium is not None
    assert medium.metadata['film_process_model']['premature_fixing']['material_rate_l_mmol_s']==.002
    assert medium.metadata['process_control']['bath_domains']['fix_halide']['targets']==['halide','bleached_halide']
    before=medium.optical_density_rgb.copy();stamp=result.medium_path.stat().st_mtime_ns
    assert np.isfinite(scan_medium_output(medium,cfg).output_srgb).all()
    np.testing.assert_array_equal(before,medium.optical_density_rgb)
    assert result.medium_path.stat().st_mtime_ns==stamp
