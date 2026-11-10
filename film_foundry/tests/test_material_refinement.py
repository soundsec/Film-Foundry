import json
from dataclasses import asdict
import numpy as np
import pytest
from film_foundry.core import peel_apart as peel, integral_instant as instant, silver_plate as plate
from film_foundry.core.material_family import ViewingConditions
from film_foundry.app import material_families as api

IMAGE=np.full((36,48,3),.18,np.float32)


@pytest.mark.parametrize("module,material_type",[(peel,peel.PeelMaterial),(instant,instant.InstantMaterial)])
def test_batch_identity_shared_sheet_flow_independent_and_conserved(module,material_type,tmp_path):
    material=material_type(batch_variation=.8,batch_seed=19,structure_model='relative_v1')
    a=module.expose(IMAGE,material,group_id="a",seed=31,schema_version=2)
    b=module.expose(IMAGE,material,group_id="b",seed=32,schema_version=2)
    np.testing.assert_array_equal(a.fields["exposure_response"],b.fields["exposure_response"])
    assert json.loads(a.process_json)["batch"]==json.loads(b.process_json)["batch"]
    different=module.expose(IMAGE,material_type(batch_variation=.8,batch_seed=20,structure_model='relative_v1'),group_id="c",seed=31,schema_version=2)
    assert not np.array_equal(a.fields["exposure_response"],different.fields["exposure_response"])
    if module is instant:a=instant.eject(a);b=instant.eject(b)
    a=module.spread(a,reagent_leak=.8,spread_unevenness=.7,temperature_c=5)
    b=module.spread(b,reagent_leak=.8,spread_unevenness=.7,temperature_c=5)
    assert not np.array_equal(a.fields["reagent"],b.fields["reagent"])
    assert a.fields["escaped_reagent"].mean()>0
    assert float((a.fields["reagent"]+a.fields["escaped_reagent"]).mean())==pytest.approx(1,abs=2e-6)
    first=module.advance(a,seconds=20);path=tmp_path/'state.npz';api.save(path,first)
    restored=api.load(path);assert restored.schema_version==2
    once=module.advance(a,seconds=180);stepped=module.advance(restored,seconds=160)
    np.testing.assert_array_equal(once.fields["received"],stepped.fields["received"])
    np.testing.assert_allclose(once.fields["immobile"]+once.fields["remaining"]+once.fields["received"],1,atol=2e-6)
    final=instant.stabilize(once) if module is instant else peel.peel(once)
    product=module.products(final);api.save(path,product);loaded=api.load(path)
    np.testing.assert_array_equal(api.observe(product.bodies[0]),api.observe(loaded.bodies[0]))
    assert loaded.bodies[0].schema_version==2


def test_temperature_forms_different_color_and_does_not_depend_on_view():
    def result(temperature):
        state=instant.expose(IMAGE,group_id=str(temperature))
        state=instant.stabilize(instant.spread(instant.eject(state),temperature_c=temperature))
        return state,instant.preview(state)
    cold,cold_image=result(2);normal,normal_image=result(20);hot,hot_image=result(38)
    assert cold_image[...,2].mean()>cold_image[...,0].mean()
    assert hot_image[...,0].mean()>hot_image[...,1].mean()
    np.testing.assert_allclose(normal_image[...,0],normal_image[...,2])
    before=cold.fields["received"].copy()
    instant.preview(cold,ViewingConditions(exposure_ev=1))
    np.testing.assert_array_equal(before,cold.fields["received"])
    assert cold.fields["received"].mean()<normal.fields["received"].mean()


def test_integral_uncoated_backing_differs_from_peel_receiver():
    p=peel.peel(peel.advance(peel.spread(peel.expose(IMAGE,group_id="p"),coverage=0),seconds=300))
    i=instant.stabilize(instant.spread(instant.eject(instant.expose(IMAGE,group_id="i")),coverage=0))
    assert peel.preview(p).mean()>instant.preview(i).mean()*3
    color=instant.preview(i)[0,0];assert color[0]>color[1]>color[2]


def test_plate_v2_response_direction_corrosion_and_readonly(tmp_path):
    resolved_image=np.full((256,384,3),.18,np.float32)
    a=plate.prepare(resolved_image.shape[:2],group_id="plate",seed=71,schema_version=2)
    horizontal=plate.polish(a,streaks=.6,angle_degrees=0)
    vertical=plate.polish(a,streaks=.6,angle_degrees=90)
    assert np.std(horizontal.fields["polish_x"])>.001
    assert np.std(horizontal.fields["polish_y"])==0
    assert np.std(vertical.fields["polish_y"])>.001
    ramp=np.tile(np.array([.02,.18,1.],np.float32)[None,:,None],(4,1,3))
    state=plate.expose(ramp,group_id="curve",schema_version=2)
    assert state.fields["latent"][0,0]<.02/(.02+.18)
    assert state.fields["latent"][0,2]>1/(1+.18)
    state=plate.fix(plate.develop(plate.expose_surface(plate.sensitize(horizontal),image=resolved_image),seconds=120))
    damaged=plate.weather(state,pitting=.8,edge_tarnish=.9,scratches=.4)
    assert damaged.fields["pit_depth"].max()>0
    np.testing.assert_array_equal(state.fields["particles"],damaged.fields["particles"])
    body=plate.products(damaged).bodies[0]
    assert body.observation_model=="metal_particle_reflection_v2"
    tilt=ViewingConditions(view_direction=(.45,0,.89))
    rgb=api.observe(body,tilt)
    assert np.max(np.abs(rgb[...,0]-rgb[...,2]))>.005
    api.save(tmp_path/'plate.npz',damaged);loaded=api.load(tmp_path/'plate.npz')
    assert loaded.schema_version==2
    np.testing.assert_array_equal(plate.preview(loaded,tilt),rgb)


def test_v1_archives_keep_original_observer_and_resume(tmp_path):
    modern=peel.expose(IMAGE,group_id="old")
    fields={k:modern.fields[k] for k in peel.BASE_FIELDS}
    material=asdict(peel.PeelMaterial());material.pop("batch_variation");material.pop("batch_seed")
    meta=dict(kind="state",family=peel.FAMILY,version=1,group_id="old",seed=0,material=material,stage="exposed",elapsed_seconds=0,process_json="{}")
    path=tmp_path/'legacy.npz';np.savez(path,manifest=np.array(json.dumps(meta)),**fields)
    loaded=api.load(path);assert loaded.schema_version==1
    final=peel.peel(peel.advance(peel.spread(loaded,spread_seconds=0),seconds=60))
    expected=.92*10**(-2*.5*(1-np.exp(-1)))
    np.testing.assert_allclose(peel.preview(final),expected,rtol=2e-6)
    with pytest.raises(ValueError):peel.spread(loaded,reagent_leak=.5)
    shape=(4,6);fields={k:np.zeros(shape,np.float32) for k in plate.FIELDS}
    fields.update(latent=np.full(shape,.5,np.float32),particles=np.full(shape,.3,np.float32),roughness=np.full(shape,.12,np.float32))
    old=plate.PlateState("old-plate",0,plate.PlateMaterial(),"fixed",60,fields,schema_version=1)
    api.save(path,old);restored=api.load(path)
    assert plate.products(restored).bodies[0].observation_model=="metal_particle_reflection_v1"
    np.testing.assert_allclose(plate.preview(restored,ViewingConditions(background_rgb=(0,0,0))),.9*.7+.7*.3,rtol=2e-6)


def test_integral_early_light_protection_and_darkness_preserve_pools(tmp_path):
    exposed=instant.eject(instant.expose(IMAGE,group_id="shield",seed=8))
    protected=instant.stabilize(instant.spread(exposed,light_protection=1,ambient_light=1))
    dark=instant.stabilize(instant.spread(exposed,light_protection=0,ambient_light=0))
    np.testing.assert_array_equal(protected.fields["received"],dark.fields["received"])
    open_sheet=instant.spread(exposed,light_protection=0,ambient_light=1)
    path=tmp_path/'open.npz';api.save(path,open_sheet)
    open_final=instant.stabilize(api.load(path))
    picture=instant.preview(open_final);normal=instant.preview(protected)
    assert np.mean(picture[:5,:,2]-normal[:5,:,2])>.01
    assert np.mean(picture[:5,:,2]-normal[:5,:,2])>np.mean(picture[-5:,:,2]-normal[-5:,:,2])
    np.testing.assert_allclose(open_final.fields["immobile"]+open_final.fields["remaining"]+open_final.fields["received"],1,atol=2e-6)
    assert json.loads(open_final.process_json)["events"][-1]["operation"]=="early_light_exposure"


def test_declared_positive_negative_clear_preserves_receiver_and_separate_density(tmp_path):
    image=np.tile(np.linspace(.005,1,48,dtype=np.float32)[None,:,None],(36,1,3))
    material=peel.PeelMaterial(transfer="silver_compound",donor_mode="silver_negative")
    state=peel.peel(peel.advance(peel.spread(peel.expose(image,material,group_id="pair"),spread_seconds=0),seconds=180))
    uncleared=peel.products(state);assert not uncleared.bodies[1].usable_negative
    cleared=peel.clear_negative(state);group=peel.products(cleared)
    positive,negative=group.bodies
    assert negative.usable_negative and negative.role=="silver_negative"
    assert negative.body_id==uncleared.bodies[1].body_id
    assert positive.group_id==negative.group_id=="pair"
    np.testing.assert_array_equal(api.observe(positive),api.observe(uncleared.bodies[0]))
    assert api.observe(positive)[0,0,0]<api.observe(positive)[0,-1,0]
    assert api.observe(negative)[0,0,0]>api.observe(negative)[0,-1,0]
    api.save_body(tmp_path/'negative.npz',group,negative.body_id)
    restored=api.load(tmp_path/'negative.npz');assert len(restored.bodies)==1
    np.testing.assert_array_equal(api.observe(restored.bodies[0]),api.observe(negative))
    with pytest.raises(ValueError):peel.PeelMaterial(transfer="dye",donor_mode="silver_negative")
    with pytest.raises(ValueError):peel.clear_negative(cleared)


@pytest.mark.parametrize("style",["equal","pod"])
def test_white_border_preserves_image_pixels_and_saved_presentation(style,tmp_path):
    state=instant.stabilize(instant.spread(instant.eject(instant.expose(IMAGE,instant.InstantMaterial(border_style=style),group_id="frame"))))
    group=instant.products(state);body=group.bodies[0]
    raw=api.observe(body);framed=api.observe(body,ViewingConditions(include_border=True))
    edge=round(min(raw.shape[:2])*.055);bottom=edge if style=="equal" else round(min(raw.shape[:2])*.20)
    assert framed.shape==(raw.shape[0]+edge+bottom,raw.shape[1]+2*edge,3)
    np.testing.assert_array_equal(framed[edge:edge+raw.shape[0],edge:edge+raw.shape[1]],raw)
    np.testing.assert_allclose(framed[:edge],.94)
    api.save(tmp_path/'framed.npz',group)
    np.testing.assert_array_equal(api.observe(api.load(tmp_path/'framed.npz').bodies[0],ViewingConditions(include_border=True)),framed)


@pytest.mark.parametrize("shape",[(40,80),(80,40),(40,40)])
def test_optional_pod_border_follows_short_edge_without_resampling(shape):
    image=np.full(shape+(3,),.18,np.float32)
    state=instant.stabilize(instant.spread(instant.eject(instant.expose(image,group_id="orientation"))))
    body=instant.products(state).bodies[0];raw=api.observe(body)
    assert raw.shape==image.shape
    edge=round(min(shape)*.055);wide=round(min(shape)*.20)
    framed=api.observe(body,ViewingConditions(include_border=True,border_style="pod_short"))
    h,w=shape
    assert framed.shape==((h+2*edge,w+edge+wide,3) if w>h else (h+edge+wide,w+2*edge,3))
    np.testing.assert_array_equal(framed[edge:edge+h,edge:edge+w],raw)
    equal=api.observe(body,ViewingConditions(include_border=True,border_style="equal"))
    assert equal.shape==(h+2*edge,w+2*edge,3)


def test_solar_warming_and_strong_light_are_separate_preserved_conditions(tmp_path):
    start=instant.eject(instant.expose(IMAGE,group_id="sun",seed=31))
    def develop(**params):return instant.stabilize(instant.spread(start,temperature_c=20,**params))
    normal=develop()
    light_only=develop(light_protection=0,ambient_light=4)
    warm_only=develop(light_protection=1,ambient_light=4,solar_warming=18)
    sun=develop(light_protection=0,ambient_light=4,solar_warming=18)
    settings=json.loads(sun.process_json)
    assert settings["initial_temperature_c"]==20 and settings["temperature_c"]==38
    assert settings["solar_warming"]==18
    warm_image=instant.preview(warm_only)
    # Warm chemistry may lean pink or yellow; it need not enforce G > B.
    warm_mean=warm_image.mean(axis=(0,1));normal_mean=instant.preview(normal).mean(axis=(0,1))
    assert warm_mean[0]/warm_mean[1]>normal_mean[0]/normal_mean[1]
    assert warm_mean[0]/warm_mean[2]>normal_mean[0]/normal_mean[2]
    assert instant.preview(light_only)[-5:].mean()>instant.preview(normal)[-5:].mean()
    assert not np.array_equal(sun.fields["received"],light_only.fields["received"])
    np.testing.assert_allclose(sun.fields["immobile"]+sun.fields["remaining"]+sun.fields["received"],1,atol=2e-6)
    api.save(tmp_path/'sun.npz',sun);loaded=api.load(tmp_path/'sun.npz')
    np.testing.assert_array_equal(instant.preview(loaded),instant.preview(sun))
    with pytest.raises(ValueError):instant.spread(start,solar_warming=float("nan"))
