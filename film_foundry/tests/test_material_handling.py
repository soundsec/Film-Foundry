from dataclasses import replace
import numpy as np
import pytest
from film_foundry.core import peel_apart as peel,integral_instant as instant
from film_foundry.app import material_families as api


def exposed():
    image=np.linspace(.02,.9,96*128*3,dtype=np.float32).reshape(96,128,3)
    return peel.expose(image,peel.PeelMaterial(transfer='silver_compound',donor_mode='silver_negative'),group_id='handling',seed=8)


def test_clearing_strength_marks_and_receiver_independence(tmp_path):
    state=peel.peel(peel.advance(peel.spread(exposed()),seconds=90))
    clean=peel.products(peel.clear_negative(state))
    dirty=peel.products(peel.clear_negative(state,strength=.2,marks=.7,angle_degrees=35))
    assert np.mean(dirty.bodies[1].fields['density'])>np.mean(clean.bodies[1].fields['density'])
    np.testing.assert_array_equal(peel.observe(clean.bodies[0]),peel.observe(dirty.bodies[0]))
    brushed=peel.products(peel.clear_negative(state,marks=1))
    assert np.std(brushed.bodies[1].fields['density']-clean.bodies[1].fields['density'])>.01
    path=tmp_path/'cleared.ffmaterial.npz';api.save(path,dirty)
    np.testing.assert_array_equal(api.observe(api.load(path).bodies[1]),api.observe(dirty.bodies[1]))


@pytest.mark.parametrize('kind',['peel','integral'])
def test_handling_changes_only_surface_and_is_persistent(kind,tmp_path):
    if kind=='peel':
        module=peel;state=peel.peel(peel.advance(peel.spread(exposed()),seconds=90))
    else:
        module=instant;state=instant.stabilize(instant.spread(instant.eject(instant.expose(np.full((96,128,3),.3,np.float32),group_id='instant'))))
    changed=module.handling(state,fingerprints=.8,reagent_residue=.6,position_x=.4,position_y=.6,angle_degrees=20)
    for key in ('received','remaining','immobile'):np.testing.assert_array_equal(changed.fields[key],state.fields[key])
    assert np.any(changed.fields['stain_density']>state.fields['stain_density'])
    path=tmp_path/'handled.ffmaterial.npz';api.save(path,changed);restored=api.load(path)
    before=module.observe(module.products(changed).bodies[0])
    np.testing.assert_array_equal(before,module.observe(module.products(restored).bodies[0]))
    np.testing.assert_array_equal(before,module.observe(module.products(changed).bodies[0]))


def test_historical_exposure_response_is_material_input():
    from film_foundry.contracts.spectral_exposure import make_historical_exposure_profile
    from film_foundry.core.spectral_exposure import map_layer_exposure
    from film_foundry.model.config import FilmStockConfig
    inputs=np.eye(3,dtype=np.float32).reshape(1,3,3)
    film=FilmStockConfig(spectral_exposure_profile=make_historical_exposure_profile('orthochromatic'))
    response=map_layer_exposure(inputs,film)[0,:,0]
    assert response[0]<response[1]*.2 and response[0]<response[2]*.2
    film.spectral_exposure_profile=make_historical_exposure_profile('blue_sensitive')
    blue=map_layer_exposure(inputs,film)[0,:,0]
    assert blue[2]>blue[1]*3


@pytest.mark.parametrize('kind',['peel','integral','plate'])
def test_material_batch_preserves_all_related_bodies(kind,tmp_path):
    from PIL import Image
    from film_foundry.app.workspace_jobs import process_material_photos
    from film_foundry.core.material_family import ViewingConditions
    path=tmp_path/'input.png';Image.fromarray(np.full((12,16,3),128,np.uint8)).save(path)
    if kind=='plate':steps=[('prepare',{}),('polish',{}),('sensitize',{}),('expose',{}),('develop',{'seconds':60}),('fix',{})];material={}
    elif kind=='integral':steps=[('expose',{}),('eject',{}),('spread',{}),('stabilize',{}),('handling',{'fingerprints':.6})];material={}
    else:steps=[('expose',{}),('spread',{}),('advance',{'seconds':90}),('peel',{}),('clear_negative',{'marks':.5})];material={'transfer':'silver_compound','donor_mode':'silver_negative'}
    records=[];process_material_photos(kind,[path,path],tmp_path/'result',material,steps,ViewingConditions(),on_result=records.append)
    assert len(records)==2 and records[0]['medium']!=records[1]['medium']
    from pathlib import Path
    for record in records:
        assert all(Path(p).is_file() for p in record['artifacts'].values())
        group=api.load(record['medium']);assert len(group.bodies)==(2 if kind=='peel' else 1)
        if kind=='peel':assert group.bodies[1].usable_negative
