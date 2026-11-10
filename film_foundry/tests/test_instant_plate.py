from dataclasses import replace
import numpy as np
import pytest
from film_foundry.app import material_families as api
from film_foundry.core import integral_instant as instant, silver_plate as plate
from film_foundry.core.material_family import ViewingConditions


IMAGE=np.linspace(0,2,6*9*3,dtype=np.float32).reshape(6,9,3)


@pytest.mark.parametrize("transfer",["dye","silver_compound"])
def test_integral_layers_progress_stabilization_and_one_product(transfer,tmp_path):
    engine=api.family(instant.FAMILY)
    initial=engine.expose(IMAGE,instant.InstantMaterial(transfer=transfer),group_id="integral-test",seed=3)
    with pytest.raises(ValueError):engine.process(initial,"spread")
    with pytest.raises(ValueError):engine.process(initial,"peel")
    reacted=engine.process(engine.process(initial,"eject"),"spread",coverage=.7,roller_streaks=.2)
    a=engine.process(reacted,"advance",seconds=60)
    b=engine.process(engine.process(reacted,"advance",seconds=20),"advance",seconds=40)
    np.testing.assert_array_equal(a.fields["received"],b.fields["received"])
    assert np.all(a.fields["opacifier"]<reacted.fields["opacifier"])
    api.save(tmp_path/'developing.npz',a);loaded=api.load(tmp_path/'developing.npz')
    final=engine.process(loaded,"stabilize")
    assert final.stage=="stable" and np.all(final.fields["stabilization"]==1)
    assert np.all(final.fields["opacifier"]==0)
    np.testing.assert_allclose(final.fields["immobile"]+final.fields["remaining"]+final.fields["received"],1,atol=1e-6)
    with pytest.raises(ValueError):engine.process(final,"advance",seconds=1)
    with pytest.raises(ValueError):engine.finish(a)
    group=engine.finish(final)
    assert len(group.bodies)==1 and group.bodies[0].role=="integral_print"
    api.save(tmp_path/'products.npz',group);restored=api.load(tmp_path/'products.npz')
    np.testing.assert_array_equal(api.observe(restored.bodies[0]),instant.preview(final))
    assert "immobile" in restored.bodies[0].fields  # Sealed donor still belongs to the print.


def test_integral_missing_chemistry_and_preview_are_readonly():
    state=instant.spread(instant.eject(instant.expose(IMAGE,group_id="dry")),coverage=0)
    before=state.fields["received"].copy();preview=instant.preview(state)
    np.testing.assert_array_equal(state.fields["received"],before)
    assert state.elapsed_seconds==0 and np.isfinite(preview).all()
    final=instant.stabilize(state)
    assert np.all(final.fields["received"]==0)
    with pytest.raises(ValueError):replace(state,schema_version=5)


def test_plate_stages_restore_and_surface_only(tmp_path):
    state=plate.prepare((6,9),group_id="plate-test",seed=9,schema_version=3)
    with pytest.raises(ValueError):plate.expose_surface(state,image=IMAGE)
    state=plate.expose_surface(plate.sensitize(plate.polish(state)),image=IMAGE)
    first=plate.develop(state,seconds=20)
    api.save(tmp_path/'exposed.npz',first)
    continued=plate.develop(api.load(tmp_path/'exposed.npz'),seconds=40)
    once=plate.develop(state,seconds=60)
    np.testing.assert_allclose(continued.fields["particles"],once.fields["particles"],atol=1e-7,rtol=1e-6)
    fixed=plate.fix(continued)
    assert np.max(fixed.fields["sensitivity"])<=1e-4
    with pytest.raises(ValueError):plate.develop(fixed,seconds=10)
    gold=plate.gild(fixed,amount=.6)
    aged=plate.surface(gold,damage=.2,tarnish=.3)
    np.testing.assert_array_equal(fixed.fields["particles"],aged.fields["particles"])
    product=plate.products(aged);api.save(tmp_path/'plate.npz',product)
    restored=api.load(tmp_path/'plate.npz')
    np.testing.assert_array_equal(api.observe(product.bodies[0]),api.observe(restored.bodies[0]))
    assert len(restored.bodies)==1 and restored.bodies[0].role=="silver_plate"


def test_plate_angle_background_and_gilding_change_view_not_particles():
    image=np.array([[[0.,0.,0.],[2.,2.,2.]]],np.float32)
    state=plate.fix(plate.develop(plate.expose(image,group_id="angle"),seconds=200))
    body=plate.products(state).bodies[0];before=body.fields["particles"].copy()
    frontal=api.observe(body,ViewingConditions(background_rgb=(0,0,0)))
    tilted=api.observe(body,ViewingConditions(view_direction=(.5,0,.866),background_rgb=(0,0,0)))
    assert frontal[0,0].mean()>frontal[0,1].mean()  # Mirror glare reverses apparent contrast.
    assert tilted[0,0].mean()<tilted[0,1].mean()
    brighter_background=api.observe(body,ViewingConditions(view_direction=(.5,0,.866),background_rgb=(.7,.7,.7)))
    assert brighter_background[0,0].mean()>tilted[0,0].mean()
    np.testing.assert_array_equal(before,body.fields["particles"])
    gilded=plate.products(plate.gild(state,amount=1,seconds=240)).bodies[0]
    assert not np.array_equal(frontal,api.observe(gilded,ViewingConditions(background_rgb=(0,0,0))))
    with pytest.raises(ValueError):api.observe(body,ViewingConditions(view_direction=(0,0,0)))
    with pytest.raises(ValueError):replace(state,schema_version=99)


def test_families_reject_each_others_bodies():
    sheet=instant.products(instant.stabilize(instant.spread(instant.eject(instant.expose(IMAGE,group_id="sealed"))))).bodies[0]
    with pytest.raises(ValueError):plate.observe(sheet)
    metal=plate.products(plate.fix(plate.develop(plate.expose(IMAGE,group_id="metal"),seconds=30))).bodies[0]
    with pytest.raises(ValueError):instant.observe(metal)
