"""Cross-entry formation/recording checks; no large-image or GUI suite."""
import json
from dataclasses import asdict
import numpy as np
import pytest
from film_foundry.app import material_families as api
from film_foundry.app.material_capabilities import describe_family
from film_foundry.app.material_lab_session import MaterialLabSession,FAMILIES
from film_foundry.app.material_route import run_material_route
from film_foundry.contracts.material_recipe import KEY,STATUS_KEY


def image():return np.random.default_rng(37).random((20,28,3),dtype=np.float32)


def operations(kind):
    return {
        'peel':[('spread',{'reagent_leak':.2}),('advance',{'seconds':35}),('advance',{'seconds':40}),('peel',{}),('handling',{'fingerprints':.3})],
        'integral':[('eject',{}),('spread',{'light_protection':.4,'solar_warming':3.}),('advance',{'seconds':25}),('stabilize',{}),('handling',{'reagent_residue':.2})],
        'plate':[('develop',{'seconds':15}),('develop',{'seconds':20}),('fix',{}),('load_gilding',{}),('gild',{}),('finish',{}),('weather',{'pitting':.2})],
        'paper':[('develop',{}),('fix',{}),('wash',{}),('handling',{'fingerprints':.2})],
        'wet':[('develop',{'pour_uniformity':.7}),('fix',{}),('wash',{}),('handling',{'fingerprints':.2})],
        'cyanotype':[('wash',{'seconds':60}),('wash',{}),('oxidize',{}),('handling',{'reagent_residue':.1})],
        'platinum':[('develop',{}),('clear',{'seconds':40}),('clear',{}),('wash',{}),('handling',{'fingerprints':.2})],
    }[kind]


def same_fields(a,b):
    assert a.stage==b.stage and a.elapsed_seconds==b.elapsed_seconds and a.seed==b.seed
    assert asdict(a.material)==asdict(b.material) and a.schema_version==b.schema_version
    assert a.fields.keys()==b.fields.keys()
    for key,value in a.fields.items():np.testing.assert_array_equal(value,b.fields[key])


@pytest.mark.parametrize('kind',FAMILIES)
def test_route_session_recipe_and_capabilities_agree(kind,tmp_path):
    module,definition=FAMILIES[kind];material=definition();pixels=image()
    route=run_material_route(pixels,receiver=module.FAMILY,material=material,stop='state',input_encoding='linear_rgb',seed=19).value
    direct=api.family(module.FAMILY).expose(pixels,material,group_id='reference',seed=19)
    same_fields(route,direct)
    session=MaterialLabSession(kind);session.image=pixels
    for op in getattr(module,'EXPOSURE_STEPS',('expose',)):session.process(op,seed=19)
    same_fields(route,session.state)
    assert json.loads(route.process_json)[KEY]==session.recipe()
    for index,(op,parameters) in enumerate(operations(kind)):
        # Querying capabilities cannot form or replace a field.
        before=route;fields=tuple(route.fields.values());cap=describe_family(module.FAMILY,state=route)
        assert {x['operation'] for x in cap['operations'] if x['available']}==set(session.operations)
        assert cap['bath_solution_supported'] is False and cap['recipe']['complete']
        json.dumps(cap,allow_nan=False)
        assert tuple(id(x) for x in fields)==tuple(id(x) for x in before.fields.values())
        route=run_material_route(route,stop='state',operations=[{'operation':op,**parameters}]).value
        direct=api.family(module.FAMILY).process(direct,op,**parameters)
        session.process(op,parameters)
        same_fields(route,direct);same_fields(route,session.state)
        assert json.loads(route.process_json)[KEY]==session.recipe()
        if index==0:
            path=tmp_path/'paused.npz';api.save(path,route);route=api.load(path)
    replay=MaterialLabSession(kind);replay.image=pixels;replay.replay_recipe(json.loads(route.process_json)[KEY])
    same_fields(route,replay.state)
    assert route.group_id!=replay.state.group_id
    products=api.family(module.FAMILY).finish(route)
    cap=describe_family(module.FAMILY,group=products)
    assert cap['stage']=='finished_products' and not any(op['available'] for op in cap['operations'])
    assert {p['role'] for p in cap['products']}=={b.role for b in products.bodies}
    assert cap['recipe']['complete'] and cap['material']==asdict(route.material)


def test_missing_or_nonportable_history_is_explicit_and_preserves_execution(tmp_path):
    from film_foundry.core import peel_apart as peel
    raw=peel.expose(image(),group_id='legacy',seed=3)
    old=run_material_route(raw,stop='state',operations=[{'operation':'spread'}]).value
    assert KEY not in json.loads(old.process_json)
    assert json.loads(old.process_json)[STATUS_KEY]['reason']=='missing_history'
    tracked=run_material_route(image(),receiver=peel.FAMILY,material=peel.PeelMaterial(),stop='state',input_encoding='linear_rgb',seed=3).value
    coverage=np.linspace(.2,1,20,dtype=np.float32)[:,None]
    spatial=run_material_route(tracked,stop='state',operations=[{'operation':'spread','coverage':coverage}]).value
    expected=peel.spread(raw,coverage=coverage);same_fields(spatial,expected)
    assert json.loads(spatial.process_json)[STATUS_KEY]['reason']=='nonportable_parameters'
    assert KEY not in json.loads(spatial.process_json) and KEY in json.loads(tracked.process_json)
    path=tmp_path/'spatial.npz';api.save(path,spatial)
    continued=run_material_route(api.load(path),stop='state',operations=[{'operation':'advance','seconds':30}]).value
    assert json.loads(continued.process_json)[STATUS_KEY]==json.loads(spatial.process_json)[STATUS_KEY]
    session=MaterialLabSession('peel');session.state=continued
    session.process('peel')
    with pytest.raises(ValueError,match='完整'):session.recipe()


def test_optical_exposure_records_preparation_and_preserves_parent():
    from film_foundry.core import photographic_paper as paper,silver_plate as plate
    from film_foundry.app.optical_cascade import project_medium
    from film_foundry.contracts.optical_cascade import CASCADE_KEY,OpticalRelay
    source=run_material_route(image(),receiver=paper.FAMILY,material=paper.PaperMaterial(),input_encoding='linear_rgb',
        operations=[{'operation':op,**p} for op,p in operations('paper')]).value
    original_provenance=source.provenance_json;relay=OpticalRelay(mode='reflection')
    exposed=run_material_route(source,receiver=plate.FAMILY,material=plate.PlateMaterial(),relay=relay,stop='state',seed=29).value
    process=json.loads(exposed.process_json)
    assert [s['operation'] for s in process[KEY]['steps']]==['prepare','polish','sensitize','expose']
    assert process[KEY]['steps'][1]['parameters']=={'streaks':.15,'angle_degrees':0.}
    final=run_material_route(exposed,stop='state',operations=[{'operation':'develop','seconds':30},{'operation':'fix'}]).value
    assert json.loads(final.process_json)[CASCADE_KEY]==process[CASCADE_KEY]
    replay=MaterialLabSession('plate');replay.image=project_medium(source,relay)
    replay.replay_recipe(json.loads(final.process_json)[KEY]);same_fields(final,replay.state)
    assert source.provenance_json==original_provenance


def test_version_limits_conditional_products_and_cli(tmp_path,capsys):
    from film_foundry.core import wet_plate as wet,peel_apart as peel
    from film_foundry.app.cli import main
    old=wet.prepare((20,28),group_id='old',schema_version=1)
    descriptor=describe_family(wet.FAMILY,state=old)
    assert descriptor['material_version']==1 and descriptor['current_version']==4
    assert 'v1_no_pour_unevenness_or_residue_exposure_filter' in descriptor['limitations']
    assert descriptor['products'][0]['usable_negative']
    assert not describe_family(wet.FAMILY,material={'support':'tintype'})['products'][0]['usable_negative']
    declared=describe_family(peel.FAMILY,material={'transfer':'silver_compound','donor_mode':'silver_negative'})
    assert declared['products'][-1]['replaces']=='donor_residue'
    assert not any(p['usable_negative'] for p in describe_family(peel.FAMILY)['products'])
    path=tmp_path/'old.npz';api.save(path,old)
    assert main(['material-info',wet.FAMILY,'--state',str(path)])==0
    report=json.loads(capsys.readouterr().out)
    assert report==descriptor
    assert main(['material-info',peel.FAMILY,'--state',str(path)])==1
    assert 'another material family' in capsys.readouterr().err


def test_route_recipe_limit_keeps_material_but_not_a_stale_complete_recipe(monkeypatch):
    import film_foundry.contracts.material_recipe as contract
    from film_foundry.core import photographic_paper as paper
    monkeypatch.setattr(contract,'MAX_STEPS',3)
    result=run_material_route(image(),receiver=paper.FAMILY,material=paper.PaperMaterial(),input_encoding='linear_rgb',stop='state',
        operations=[{'operation':'develop'},{'operation':'fix'},{'operation':'wash'}]).value
    assert result.stage=='washed'
    process=json.loads(result.process_json)
    assert KEY not in process and process[STATUS_KEY]['reason']=='recipe_limit'
