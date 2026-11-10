from copy import deepcopy
from dataclasses import asdict
import numpy as np
import pytest
from film_foundry.model.config import DarkroomConfig
from film_foundry.app.material_designs import DESIGNS,compose_material

def cfg():return DarkroomConfig(enable_mtf=False,enable_halation=False,enable_grain=False,seed_strategy='fixed',random_seed=17)

@pytest.mark.parametrize('design',tuple(DESIGNS))
def test_material_starts_form_and_preserve_chemistry(design):
    from film_foundry.core.engine import develop_negative
    source=cfg();source.chemistry.time_min=9.;before=asdict(source)
    configured=compose_material(source,design)
    assert asdict(source)==before and asdict(configured.chemistry)==before['chemistry']
    restored=DarkroomConfig.from_dict(asdict(configured))
    image=np.linspace(.02,.9,18*24*3,dtype=np.float32).reshape(18,24,3)
    result=develop_negative(image,restored,rng=np.random.default_rng(17))
    assert np.isfinite(result.optical_density_rgb).all()
    assert result.image_polarity==('positive' if 'reversal' in design else 'negative')

def test_sensitivity_and_material_traits_have_actual_formation_consequences():
    from film_foundry.core.spectral_exposure import map_layer_exposure
    from film_foundry.core.process_operations import form_process_surface
    from film_foundry.contracts.process_events import EVENT_MODEL
    primaries=np.eye(3,dtype=np.float32).reshape(1,3,3)
    pan=map_layer_exposure(primaries,compose_material(cfg(),'panchromatic').film)[0,:,0]
    red=map_layer_exposure(primaries,compose_material(cfg(),'red_weighted_visible').film)[0,:,0]
    assert red[0]/red[2]>pan[0]/pan[2]
    source=cfg();source.chemistry.process_events={'model':EVENT_MODEL,'events':[{'kind':'temperature','temperature_c':60.,'transfer_s':0.}]}
    soft=compose_material(source,'orthochromatic',emulsion='soft')
    hard=compose_material(source,'orthochromatic',emulsion='hardened')
    a,_=form_process_surface(soft,(20,30),np.random.default_rng(2));b,_=form_process_surface(hard,(20,30),np.random.default_rng(2))
    assert a['emulsion_damage'].mean()>b['emulsion_damage'].mean()
    from film_foundry.core.engine import develop_negative
    image=np.full((20,30,3),.3,np.float32)
    backed=compose_material(cfg(),'color_negative',backing='strong');backed.chemistry.auxiliary_removal=0
    bare=compose_material(backed,'color_negative',backing='absent')
    assert develop_negative(image,backed).optical_density_rgb.mean()>develop_negative(image,bare).optical_density_rgb.mean()

def test_chromogenic_mono_is_dye_material_and_snapshot_freezes_it(tmp_path):
    from film_foundry.core.engine import develop_negative,expose_image_to_latent,develop_negative_from_latent
    from film_foundry.core.latent_io import save_exposed_latent,load_exposed_latent,latent_identity_from_film
    from film_foundry.core.media import is_monochrome_material
    source=compose_material(cfg(),'chromogenic_mono');source.enable_grain=True
    image=np.linspace(.03,.9,20*30*3,dtype=np.float32).reshape(20,30,3)
    assert not is_monochrome_material(source)
    medium=develop_negative(image,source,rng=np.random.default_rng(8))
    assert medium.color_system=='monochrome_dye' and medium.base_type=='clear_base'
    np.testing.assert_allclose(medium.optical_density_rgb[...,0],medium.optical_density_rgb[...,2],atol=2e-6)
    latent=expose_image_to_latent(image,source)
    path=tmp_path/'material.darkroom_latent.npz';save_exposed_latent(path,latent,identity=latent_identity_from_film(source.film))
    wrong=deepcopy(source);wrong.film=compose_material(source,'blue_sensitive').film
    actual=develop_negative_from_latent(load_exposed_latent(path)[0],wrong,rng=np.random.default_rng(8))
    expected=develop_negative_from_latent(load_exposed_latent(path)[0],source,rng=np.random.default_rng(8))
    np.testing.assert_array_equal(actual.optical_density_rgb,expected.optical_density_rgb)
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_negative_npz
    result=run_request(WorkflowRequest('develop_latent',path,tmp_path/'formed',source))
    reloaded=load_developed_negative_npz(result.medium_path)
    assert reloaded.color_system=='monochrome_dye' and reloaded.base_type=='clear_base'
    from film_foundry.core.scan_runtime import scan_medium_output
    assert np.isfinite(scan_medium_output(reloaded,source).output_srgb).all()

def test_composition_dialog_and_editor_preserve_resolved_material(tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.material_design_dialog import MaterialDesignDialog
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    for name in ('UI','CAMERA','CATALOG'):
        key='FILM_FOUNDRY_'+('CATALOG' if name=='CATALOG' else name+'_SETTINGS');monkeypatch.setenv(key,str(tmp_path/(name+'.json')))
    _application();dialog=MaterialDesignDialog(cfg())
    for axis,key in {'design':'chromogenic_mono','emulsion':'soft','backing':'strong'}.items():
        dialog.choices[axis].setCurrentIndex(dialog.choices[axis].findData(key))
    dialog.apply();editor=FilmMaterialEditor(config=dialog.config)
    try:
        result=editor._config_from_controls()
        assert result.film.color_process=='chromogenic_monochrome'
        assert result.film.process_response==dialog.config.film.process_response
        np.testing.assert_allclose(result.film.dye_absorption_matrix,dialog.config.film.dye_absorption_matrix,atol=.001)
    finally:editor.root.destroy();dialog.close()
