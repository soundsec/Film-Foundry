from copy import deepcopy
from dataclasses import replace,asdict
import json
import numpy as np
import pytest
from film_foundry.model.config import DarkroomConfig
from film_foundry.contracts.process_events import RESPONSE_MODEL_V3,RESPONSE_MODEL_V4,validate_process_response
from film_foundry.core.film_surface_optics import silver_transfer,extinction_coefficients,observe_surface
from film_foundry.core.material_family import ViewingConditions
from film_foundry.core.engine import develop_negative


def config():
    cfg=DarkroomConfig(enable_grain=False,enable_halation=False,enable_mtf=False,seed_strategy='fixed',random_seed=7)
    cfg.film.process_response={'model':RESPONSE_MODEL_V4}
    cfg.chemistry.process_events={'model':'film_process_events_v7','events':[{'kind':'silver_bath','temperature_c':20.,'distribution_variation':.8,'distribution_seed':12}]}
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    return cfg


def scene():return np.linspace(.02,.9,24*36*3,dtype=np.float32).reshape(24,36,3)


def test_silver_partition_is_passive_zero_safe_and_colour_complementary():
    response=validate_process_response({'model':RESPONSE_MODEL_V4})
    for path in (1.,2.,10.):
        t,r=silver_transfer(np.array([0.,1.,12.,10000.],np.float32),response,path)
        assert np.isfinite(t).all() and np.isfinite(r).all()
        assert np.min(t)>=0 and np.min(r)>=0 and np.max(t+r)<=1.000001
        np.testing.assert_array_equal(t[0],1);np.testing.assert_array_equal(r[0],0)
        assert t[2,0]>t[2,1] and r[2,1]>r[2,0]
    response.update(silver_density_per_mg_m2=0.,surface_silver_scattering_rgb=(0.,0.,0.))
    t,r=silver_transfer(np.ones((2,3),np.float32),response)
    np.testing.assert_array_equal(t,np.ones((2,3,3)));np.testing.assert_array_equal(r,np.zeros((2,3,3)))


def test_formation_and_reflection_share_frozen_coefficients_and_view_is_readonly():
    cfg=config();original=asdict(cfg);medium=develop_negative(scene(),cfg)
    old=deepcopy(cfg);old.film.process_response={'model':RESPONSE_MODEL_V3}
    legacy=develop_negative(scene(),old)
    mass=medium.process_surface['surface_silver_mg_m2']
    expected=mass[...,None]*np.array([.003,.018,.006],np.float32)
    # Subtracting two assembled float32 masters includes their rounding error.
    np.testing.assert_allclose(medium.optical_density_rgb-legacy.optical_density_rgb,expected,atol=5e-7)
    optical=medium.optical_density_rgb.copy();silver=mass.copy();metadata=deepcopy(medium.metadata)
    view=ViewingConditions(light_direction=(.5,0,.8660254),view_direction=(-.5,0,.8660254),background_rgb=(0.,0.,0.))
    bright=observe_surface(medium,view);dim=observe_surface(medium,replace(view,view_direction=(.5,0,.8660254)))
    assert bright.mean()>dim.mean()*2
    np.testing.assert_allclose(observe_surface(medium,replace(view,exposure_ev=1)),bright*2,rtol=1e-6)
    np.testing.assert_array_equal(optical,medium.optical_density_rgb);np.testing.assert_array_equal(silver,mass)
    assert metadata==medium.metadata and asdict(cfg)==original
    with pytest.raises(ValueError,match='recorded'):observe_surface(legacy,view)


def test_clearing_removes_reflection_and_no_silver_cannot_create_sheen():
    cfg=config();cfg.chemistry.process_events['events'][0]['after_bath']=1
    medium=develop_negative(scene(),cfg)
    reflected=observe_surface(medium,ViewingConditions(background_rgb=(0.,0.,0.)))
    assert np.max(reflected)==0.
    cfg.chemistry.process_events['events'][0]['after_bath']=2
    metal=develop_negative(scene(),cfg)
    assert observe_surface(metal,ViewingConditions(background_rgb=(0.,0.,0.))).max()>0


def test_v4_freezes_at_exposure_and_zero_scattering_matches_old_transmission():
    from film_foundry.core.engine import expose_image_to_latent,develop_negative_from_latent
    cfg=config();latent=expose_image_to_latent(scene(),cfg)
    first=develop_negative_from_latent(latent.copy(),cfg)
    cfg.film.process_response={'model':RESPONSE_MODEL_V3}
    second=develop_negative_from_latent(latent.copy(),cfg)
    np.testing.assert_array_equal(first.optical_density_rgb,second.optical_density_rgb)
    np.testing.assert_array_equal(observe_surface(first),observe_surface(second))
    legacy=develop_negative(scene(),cfg)
    cfg.film.process_response={'model':RESPONSE_MODEL_V4,'surface_silver_scattering_rgb':(0.,0.,0.)}
    clear=develop_negative(scene(),cfg)
    np.testing.assert_array_equal(legacy.optical_density_rgb,clear.optical_density_rgb)


def test_spectral_archive_minimal_surface_load_and_original_size_export(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan
    from film_foundry.app.film_observation import FilmViewingSettings,observe,export_observation
    from film_foundry.app.roll_presentation import RollLightTable
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.tests.test_spectral_grain import configuration
    cfg=config();cfg.film.spectral_material_profile=configuration().film.spectral_material_profile
    cfg.scanner.spectral_observation_profile=configuration().scanner.spectral_observation_profile
    source=tmp_path/'chart.png';Image.fromarray(np.full((18,1700,3),105,np.uint8)).save(source)
    result=run_request(WorkflowRequest('full',source,tmp_path/'scan.png',cfg))
    full=load_developed_negative_npz(result.medium_path)
    minimal=load_developed_medium_for_scan(result.medium_path,include_process_surface=True)
    scanner=load_developed_medium_for_scan(result.medium_path)
    assert scanner.process_surface is None and not minimal.layer_masters_available
    np.testing.assert_array_equal(observe_surface(full),observe_surface(minimal))
    np.testing.assert_array_equal(scan_medium_output(full,cfg).output_srgb,scan_medium_output(scanner,cfg).output_srgb)
    settings=FilmViewingSettings(mode='reflection',view_angle=-30.)
    viewer=RollLightTable();preview=viewer.observe(str(result.medium_path),mode='reflection',view_angle=-30.)
    assert max(preview.shape[:2])==1600
    stamp=result.medium_path.stat().st_mtime_ns;target=tmp_path/'reflected.png'
    export_observation(result.medium_path,target,settings)
    with Image.open(target) as image:
        assert image.size==(1700,18)
        record=json.loads(image.info['FilmFoundryObservation'])
        assert record['view']['view_angle']==-30. and record['resolution']=='original_medium'
        np.testing.assert_array_equal(np.asarray(image),np.uint8(np.rint(observe(full,settings)*255)))
    assert result.medium_path.stat().st_mtime_ns==stamp
    with pytest.raises(ValueError,match='Surface fields'):observe_surface(scanner)


def test_material_editor_opt_in_precision_sharing_and_invalid_conditions():
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessResponseDialog
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.app.film_observation import FilmViewingSettings
    app=_application();old=ProcessResponseDialog({'model':RESPONSE_MODEL_V3})
    try:
        old.apply();assert old.value['model']==RESPONSE_MODEL_V3
    finally:old.close()
    editor=ProcessResponseDialog({'model':RESPONSE_MODEL_V3})
    try:
        editor.surface_optics.setChecked(True);editor.controls['scatter_G'].setValue(.023);editor.apply()
        assert editor.value['model']==RESPONSE_MODEL_V4
        cfg=config();cfg.film.process_response=editor.value
        restored=config_from_share_code(encode_recipe_share(cfg))
        assert restored.film.process_response==editor.value
        response={**editor.value,'surface_silver_roughness':.123456789}
        exact=ProcessResponseDialog(response)
        try:exact.apply();assert exact.value==response
        finally:exact.close()
    finally:editor.close();app.processEvents()
    for bad in ({'view_angle':90.},{'temperature':float('nan')},{'background':-1.}):
        with pytest.raises(ValueError):FilmViewingSettings(**bad)
    for bad in ({'surface_silver_scattering_rgb':(-1.,0.,1.)},{'surface_silver_roughness':0.}):
        with pytest.raises(ValueError):validate_process_response({'model':RESPONSE_MODEL_V4,**bad})
