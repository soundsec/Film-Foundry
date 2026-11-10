from dataclasses import replace
import json
import numpy as np
import pytest
from film_foundry.core import wet_plate as wet,cyanotype as cyan,platinum_palladium as noble
from film_foundry.core.material_family import ViewingConditions
from film_foundry.app import material_families as api
from film_foundry.tests.test_sheet_materials import finish,cyan_iron,silver_total


def uniform():return np.full((120,180,3),.4,np.float32)


def test_material_structures_are_distinct_with_same_identity_and_uniform_exposure():
    modules=(wet,cyan,noble)
    states=[module.expose(uniform(),group_id='same',seed=19) for module in modules]
    maps=[state.fields['coating'][12:-12,18:-18].ravel() for state in states]
    for a,b in ((0,1),(0,2),(1,2)):
        assert abs(np.corrcoef(maps[a],maps[b])[0,1])<.65
    assert 'flow' in states[0].fields and 'binding' in states[1].fields and 'clear_access' in states[2].fields
    for module,state in zip(modules,states):
        repeated=module.expose(uniform(),group_id='repeat',seed=19)
        for key in state.fields:np.testing.assert_array_equal(state.fields[key],repeated.fields[key])


def test_wet_pour_defects_development_and_glass_observation_are_causal():
    material=wet.WetPlateMaterial(flow_ridges=.7,pinholes=0,support='ambrotype')
    state=wet.expose(uniform(),material,group_id='wet',seed=19)
    holes=wet.expose(uniform(),replace(material,pinholes=1),group_id='holes',seed=19)
    assert np.min(holes.fields['coating']-state.fields['coating'])<-.2
    assert np.max(holes.fields['coating']-state.fields['coating'])<1e-7
    smooth=wet.develop(state,pour_uniformity=1,seconds=12)
    uneven=wet.develop(state,pour_uniformity=0,seconds=12)
    assert uneven.fields['silver'].sum()<smooth.fields['silver'].sum()
    np.testing.assert_allclose(silver_total(uneven),silver_total(state),atol=2e-7)
    one=wet.develop(state,seconds=30,pour_uniformity=.4)
    split=wet.develop(wet.develop(state,seconds=10,pour_uniformity=.4),seconds=20,pour_uniformity=.4)
    np.testing.assert_allclose(one.fields['silver'],split.fields['silver'],atol=2e-7)
    body=wet.products(finish(wet,state)).bodies[0]
    before=body.fields['silver'].copy()
    direct=wet.observe(body);angled=wet.observe(body,ViewingConditions(view_direction=(.8,0,.6)))
    assert np.max(np.abs(direct-angled))>.02
    np.testing.assert_array_equal(before,body.fields['silver'])


def test_cyan_wash_competes_with_pigment_formation_and_is_segment_invariant():
    state=cyan.expose(uniform(),cyan.CyanotypeMaterial(pigment_binding=.15,paper_buffer=.5),group_id='wash',seed=7)
    short=cyan.wash(state,seconds=60);long=cyan.wash(state,seconds=600)
    assert long.fields['reduced_pigment'].sum()<short.fields['reduced_pigment'].sum()*.4
    split=cyan.wash(cyan.wash(state,seconds=30),seconds=30)
    for key in short.fields:np.testing.assert_allclose(short.fields[key],split.fields[key],atol=3e-7)
    for item in (short,long,cyan.oxidize(long)):
        np.testing.assert_allclose(cyan_iron(item),state.fields['coating'],atol=3e-7)
    bound=cyan.expose(uniform(),cyan.CyanotypeMaterial(pigment_binding=1,paper_buffer=0),group_id='bound',seed=7)
    assert cyan.wash(bound,seconds=600).fields['reduced_pigment'].sum()>long.fields['reduced_pigment'].sum()*3


def test_platinum_sizing_changes_clearing_access_without_removing_formed_metal():
    ratios=[]
    for sizing in (.05,.95):
        state=noble.develop(noble.expose(uniform(),noble.PlatinumMaterial(paper_sizing=sizing),group_id='sizing',seed=7))
        cleared=noble.clear(state,seconds=90)
        ratios.append(cleared.fields['spent_ferric'].sum()/state.fields['spent_ferric'].sum())
        for key in ('platinum','palladium'):np.testing.assert_array_equal(state.fields[key],cleared.fields[key])
    assert ratios[0]>ratios[1]*1.2


@pytest.mark.parametrize('module,material',[(wet,wet.WetPlateMaterial()),(cyan,cyan.CyanotypeMaterial(response_contrast=1.3,texture=.3)),(noble,noble.PlatinumMaterial(response_contrast=1.3,coating_variation=.15))])
def test_legacy_states_and_products_keep_version_and_observation(tmp_path,module,material):
    state=module.expose(uniform(),material,group_id='legacy',seed=7,schema_version=1)
    legacy_keys=set(state.fields)
    path=tmp_path/'legacy.ffmaterial.npz';api.save(path,state);loaded=api.load(path)
    assert loaded.schema_version==1 and set(loaded.fields)==legacy_keys
    expected=finish(module,state);actual=finish(module,loaded)
    for key in expected.fields:np.testing.assert_array_equal(actual.fields[key],expected.fields[key])
    group=module.products(actual);api.save(path,group);product=api.load(path)
    assert product.bodies[0].schema_version==1
    np.testing.assert_array_equal(api.observe(group.bodies[0]),api.observe(product.bodies[0]))
