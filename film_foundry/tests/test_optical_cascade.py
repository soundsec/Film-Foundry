from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import numpy as np
import pytest
from film_foundry.model.config import DarkroomConfig, merge_config_presets
from film_foundry.contracts.optical_cascade import OpticalRelay, CASCADE_KEY, validate_cascade
from film_foundry.core.engine import develop_negative, expose_image_to_latent, expose_linear_to_latent
from film_foundry.app.optical_cascade import project_medium, expose_on_film, expose_on_family
from film_foundry.app.material_route import run_material_route


def config():
    cfg=DarkroomConfig(enable_grain=False,enable_halation=False,enable_mtf=False,seed_strategy='fixed',random_seed=7)
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    return cfg


def scene():
    return np.repeat(np.linspace(.02,.95,36,dtype=np.float32)[None,:,None],24,axis=0).repeat(3,axis=2)


def test_linear_entry_matches_photo_without_clipping_high_dose_or_mutating_input():
    from film_foundry.core.color import srgb_to_linear
    cfg=config();image=scene();linear=srgb_to_linear(image);before=linear.copy()
    np.testing.assert_array_equal(expose_image_to_latent(image,cfg).developability,
                                  expose_linear_to_latent(linear,cfg).developability)
    high=expose_linear_to_latent(linear*8,cfg)
    clipped=expose_linear_to_latent(np.clip(linear*8,0,1),cfg)
    assert np.max(np.abs(high.developability-clipped.developability))>1e-4
    np.testing.assert_array_equal(linear,before)
    for bad in (linear-1,linear*np.nan):
        with pytest.raises(ValueError):expose_linear_to_latent(bad,cfg)


def test_flux_resampling_filters_time_and_zero_exposure():
    from film_foundry.core.optical_relay import transmission_radiance,relay_dose
    density=np.zeros((2,2,3),np.float32);density[:,1]=2
    before=density.copy();radiance=transmission_radiance(density,(1,1,1))
    relay=OpticalRelay(output_shape=(1,1),exposure_seconds=4,filter_density_rgb=(0,1,2))
    dose=relay_dose(radiance,relay)
    np.testing.assert_allclose(dose[0,0],np.array([1,.1,.01])*.505*4,rtol=1e-6)
    assert dose[0,0,0]>1 # relative dose is not a display image
    np.testing.assert_array_equal(relay_dose(radiance,replace(relay,exposure_seconds=0)),0)
    np.testing.assert_array_equal(density,before)


def test_negative_reprinted_on_negative_reverses_scene_twice_and_ignores_scanner():
    root=Path(__file__).resolve().parents[1]/'presets'
    cfg=config();cfg.film=DarkroomConfig.from_json(root/'film/monochrome_fine_negative.json').film
    cfg.chemistry=DarkroomConfig.from_json(root/'develop/standard_bw_negative.json').chemistry
    image=scene();original=asdict(cfg)
    first=develop_negative(image,cfg);optical=first.optical_density_rgb.copy();meta=deepcopy(first.metadata)
    latent,record=expose_on_film(first,cfg,OpticalRelay(exposure_ev=2))
    direct=expose_linear_to_latent(10**(-optical)*4,cfg)
    np.testing.assert_array_equal(latent.developability,direct.developability)
    result=run_material_route(first,receiver='film',config=cfg,relay={'exposure_ev':2},stop='medium')
    assert np.corrcoef(image[0,:,0],10**(-result.value.optical_density_rgb[0,:,0]))[0,1]>.8
    cfg.scanner.invert_transmission=False;cfg.scanner.scan_normalize=True;cfg.look.print_contrast=2
    other,_=expose_on_film(first,cfg,OpticalRelay(exposure_ev=2))
    np.testing.assert_array_equal(latent.developability,other.developability)
    np.testing.assert_array_equal(first.optical_density_rgb,optical);assert first.metadata==meta
    assert record['steps'][0]['source']['encoding']=='relative_linear_rgb'


def test_file_reexposure_freezes_receiver_and_preserves_multistage_provenance(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.latent_io import load_exposed_latent,mark_exposed_latent_consumed
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    cfg=config();photo=tmp_path/'photo.png';Image.fromarray(np.uint8(scene()*255)).save(photo)
    first=run_request(WorkflowRequest('develop',photo,tmp_path/'first.npz',cfg)).medium_path
    stamp=first.stat().st_mtime_ns;next_path=tmp_path/'second.darkroom_latent.npz'
    run_request(WorkflowRequest('reexpose',first,next_path,cfg,optical_relay={'exposure_seconds':3.}))
    latent,metadata=load_exposed_latent(next_path);record=metadata[CASCADE_KEY]
    assert len(record['steps'])==1 and record['steps'][0]['source']['name']=='first.npz'
    assert not np.any(latent.metallic_silver)
    changed=deepcopy(cfg);changed.film.process_response={'model':'creative_process_response_v4'}
    second=run_request(WorkflowRequest('develop_latent',next_path,tmp_path/'second.npz',changed))
    restored=load_developed_medium_for_scan(second.medium_path)
    assert restored.metadata['film_process_model'][CASCADE_KEY]==record
    assert restored.metadata['film_process_model']['material_binding']['snapshot']==latent.material_snapshot.as_dict()
    third=tmp_path/'third.darkroom_latent.npz'
    run_request(WorkflowRequest('reexpose',second.medium_path,third,cfg))
    chain=load_exposed_latent(third)[1][CASCADE_KEY]
    assert len(chain['steps'])==2 and chain['steps'][0]==record['steps'][0]
    mark_exposed_latent_consumed(next_path)
    assert load_exposed_latent(next_path,allow_consumed=True)[1][CASCADE_KEY]==record
    assert first.stat().st_mtime_ns==stamp


def test_peel_negative_selection_and_reflective_receiver_routes(tmp_path):
    from film_foundry.core import peel_apart as peel
    from film_foundry.app import material_families as api
    material=peel.PeelMaterial(transfer='silver_compound',donor_mode='silver_negative')
    initial=run_material_route(scene(),receiver=peel.FAMILY,material=material,stop='state')
    contact=run_material_route(initial.value,stop='state',operations=({'operation':'spread'},))
    assert initial.value.stage=='exposed' and contact.value.stage=='contact'
    final=run_material_route(contact.value,operations=({'operation':'advance','seconds':90},
        {'operation':'peel'},{'operation':'clear_negative','strength':.8}),stop='medium')
    donor=next(b for b in final.value.bodies if b.usable_negative)
    with pytest.raises(ValueError,match='exactly one'):project_medium(final.value)
    dose=project_medium(final.value,body_id=donor.body_id)
    expected=api.observe(donor)
    np.testing.assert_array_equal(dose,expected)
    receiver=final.value.bodies[0]
    with pytest.raises(ValueError,match='opaque'):project_medium(receiver)
    latent,trace=expose_on_film(final.value,config(),{'mode':'reflection'},body_id=receiver.body_id)
    assert trace['steps'][0]['source']['body_id']==receiver.body_id and latent.developability.shape[:2]==(24,36)
    viewed=run_material_route(final.value,stop='image',source_body_id=receiver.body_id)
    assert viewed.medium is final.value and viewed.encoding=='display_srgb'


@pytest.mark.parametrize('kind', ['peel','integral','plate'])
def test_existing_family_receivers_resume_and_retain_cascade(tmp_path,kind):
    from film_foundry.core import peel_apart,integral_instant,silver_plate
    from film_foundry.app import material_families as api
    module,material={'peel':(peel_apart,peel_apart.PeelMaterial()),
                     'integral':(integral_instant,integral_instant.InstantMaterial()),
                     'plate':(silver_plate,silver_plate.PlateMaterial())}[kind]
    first=develop_negative(scene(),config());before=first.optical_density_rgb.copy()
    state=expose_on_family(first,module.FAMILY,material,seed=8)
    path=tmp_path/(kind+'.ffmaterial.npz');api.save(path,state);loaded=api.load(path)
    assert json.loads(loaded.process_json)[CASCADE_KEY]==json.loads(state.process_json)[CASCADE_KEY]
    if kind=='peel':operations=({'operation':'spread'},{'operation':'advance','seconds':90},{'operation':'peel'})
    elif kind=='integral':operations=({'operation':'eject'},{'operation':'spread'},{'operation':'advance','seconds':120},{'operation':'stabilize'})
    else:operations=({'operation':'develop','seconds':60},{'operation':'fix'})
    final=run_material_route(loaded,operations=operations)
    api.save(path,final.value);group=api.load(path)
    body=group.bodies[0]
    next_state,record=expose_on_film(group,config(),{'mode':'reflection'},body_id=body.body_id)
    assert len(record['steps'])==2 and next_state.developability.shape[:2]==(24,36)
    np.testing.assert_array_equal(first.optical_density_rgb,before)


def test_film_start_and_end_points_and_illegal_transitions():
    cfg=config();state=run_material_route(scene(),receiver='film',config=cfg,stop='state')
    assert not np.any(state.value.metallic_silver)
    final=run_material_route(state.value,config=cfg,stop='medium')
    observed=run_material_route(final.value,config=cfg,stop='image')
    assert observed.medium is final.value and observed.value.shape==scene().shape
    assert run_material_route(final.value).value is final.value
    assert not np.any(state.value.metallic_silver)
    with pytest.raises(ValueError,match='finished'):project_medium(state.value)
    with pytest.raises(ValueError,match='frozen'):run_material_route(state.value,receiver='new_paper')
    with pytest.raises(ValueError,match='receiver'):run_material_route(scene())
    for invalid in ({'exposure_seconds':-1},{'illuminant_rgb':(float('nan'),1,1)},
                    {'output_shape':(True,3)},{'mode':'scan'},{'unknown':1}):
        with pytest.raises(ValueError):OpticalRelay.from_value(invalid)


def test_cli_and_api_cancel_do_not_overwrite_source(tmp_path,capsys):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,WorkflowError,run_request
    from film_foundry.app.control import WorkflowControl
    from film_foundry.core.latent_io import load_exposed_latent
    from film_foundry.app.cli import main
    cfg=config();photo=tmp_path/'photo.png';Image.fromarray(np.uint8(scene()*255)).save(photo)
    first=run_request(WorkflowRequest('develop',photo,tmp_path/'first.npz',cfg)).medium_path
    target=tmp_path/'copy.darkroom_latent.npz'
    assert main(['reexpose',str(first),str(target),'--seconds','2','--output-size','18','12','--seed-strategy','fixed'])==0
    state,metadata=load_exposed_latent(target)
    assert state.developability.shape[:2]==(12,18)
    assert metadata[CASCADE_KEY]['steps'][0]['relay']['exposure_seconds']==2
    with pytest.raises(WorkflowError,match='separate'):run_request(WorkflowRequest('reexpose',first,first,cfg))
    target=tmp_path/'cancelled.darkroom_latent.npz'
    with pytest.raises(WorkflowError) as caught:
        run_request(WorkflowRequest('reexpose',first,target,cfg,control=WorkflowControl(cancel_requested=lambda:True)))
    assert caught.value.code=='cancelled' and not target.exists()


def test_selectable_file_routes_and_cli_keep_final_medium(tmp_path):
    from PIL import Image
    from film_foundry.app.cli import main
    from film_foundry.app.material_route import run_material_route_file
    from film_foundry.core.latent_io import load_exposed_latent
    from film_foundry.app.material_families import load
    from film_foundry.core.peel_apart import FAMILY
    cfg=config();photo=tmp_path/'scene.png';Image.fromarray(np.uint8(scene()*255)).save(photo)
    state=tmp_path/'film.darkroom_latent.npz'
    assert main(['route',str(photo),str(state),'--start','image','--receiver','film','--stop','state'])==0
    assert not np.any(load_exposed_latent(state)[0].metallic_silver)
    from film_foundry.core.latent_io import save_exposed_latent
    latent,metadata=load_exposed_latent(state)
    save_exposed_latent(state,latent,identity={'frame_identity':{'roll_id':'source-roll','frame_id':'source-frame','frame_index':2}})
    output=tmp_path/'result.png'
    paths=run_material_route_file(state,output,start='latent',stop='image',config=cfg)
    medium=Path(paths['medium_path']);assert medium.exists()
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    assert load_developed_medium_for_scan(medium).metadata['frame_identity']['frame_id']=='source-frame'
    with Image.open(output) as image:
        assert json.loads(image.info['FilmFoundryObservation'])['medium_name']==medium.name
    next_state=tmp_path/'peel.ffmaterial.npz'
    run_material_route_file(medium,next_state,start='medium',receiver=FAMILY,stop='state',config=cfg)
    exposed=load(next_state);assert exposed.stage=='exposed'
    contact=tmp_path/'contact.ffmaterial.npz'
    run_material_route_file(next_state,contact,start='state',stop='state',operations=[{'operation':'spread'}])
    assert load(contact).stage=='contact' and load(next_state).stage=='exposed'
    group_path=tmp_path/'products.ffmaterial.npz'
    run_material_route_file(contact,group_path,start='state',stop='medium',operations=[{'operation':'advance','seconds':90},{'operation':'peel'}])
    group=load(group_path);assert len(group.bodies)==2
    run_material_route_file(group_path,tmp_path/'print.png',start='medium',stop='image',source_body_id=group.bodies[0].body_id)
    assert (tmp_path/'print.png').exists()
    direct=run_material_route_file(photo,tmp_path/'direct-print.png',start='image',receiver=FAMILY,stop='image',
        operations=[{'operation':'spread'},{'operation':'advance','seconds':90},{'operation':'peel'}],result_body_role='receiver')
    assert len(load(direct['medium_path']).bodies)==2
    with pytest.raises(ValueError,match='relay'):run_material_route(scene(),receiver='film',relay={})
    with pytest.raises(ValueError,match='ordered'):run_material_route(exposed,operations={'operation':'spread'})


def test_reflected_film_source_uses_frozen_silver_and_bounded_chain():
    from film_foundry.tests.test_film_surface_optics import config as reflective_config
    from film_foundry.core.film_surface_optics import observe_surface
    from film_foundry.core.material_family import ViewingConditions
    medium=develop_negative(scene(),reflective_config());before=medium.process_surface['surface_silver_mg_m2'].copy()
    relay=OpticalRelay(mode='reflection',light_angle=0,view_angle=0,exposure_seconds=2)
    np.testing.assert_allclose(project_medium(medium,relay),observe_surface(medium,ViewingConditions(background_rgb=(0,0,0)))*2,rtol=1e-6)
    _,trace=expose_on_film(medium,config(),relay)
    duplicate=deepcopy(trace);duplicate['steps']*=2
    with pytest.raises(ValueError,match='distinct'):validate_cascade(duplicate)
    medium.metadata['film_process_model'][CASCADE_KEY]={'model':trace['model'],'steps':[{**trace['steps'][0],'id':str(i)} for i in range(16)]}
    with pytest.raises(ValueError,match='16'):expose_on_film(medium,config())
    np.testing.assert_array_equal(medium.process_surface['surface_silver_mg_m2'],before)
