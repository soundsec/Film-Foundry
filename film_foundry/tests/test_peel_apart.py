import json
from dataclasses import replace
import numpy as np
import pytest
from film_foundry.core import peel_apart as peel
from film_foundry.core.material_family import ViewingConditions
from film_foundry.app import material_families as api


def exposure(kind="dye"):
    image=np.linspace(0,2,8*12*3,dtype=np.float32).reshape(8,12,3)
    return peel.expose(image,peel.PeelMaterial(transfer=kind),group_id="test-pair",seed=71)


@pytest.mark.parametrize("kind",["dye","silver_compound"])
def test_transfer_conservation_time_partition_and_readonly(kind):
    exposed=exposure(kind)
    contact=peel.spread(exposed,roller_streaks=.5,edge_loss=.6)
    before=contact.fields["remaining"].copy()
    preview=peel.preview(contact)
    assert preview.shape==(8,12,3) and contact.stage=="contact"
    once=peel.advance(contact,seconds=60)
    stepped=peel.advance(peel.advance(contact,seconds=20),seconds=40)
    np.testing.assert_array_equal(once.fields["received"],stepped.fields["received"])
    np.testing.assert_allclose(once.fields["immobile"]+once.fields["remaining"]+once.fields["received"],1,atol=1e-6)
    np.testing.assert_array_equal(before,contact.fields["remaining"])
    assert not once.fields["received"].flags.writeable
    assert once.fields["received"].shape[-1]==(3 if kind=="dye" else 1)


def test_missing_reagent_contact_and_peel_stop_transfer():
    mask=np.ones((8,12),np.float32);mask[:,:4]=0
    contacted=peel.spread(exposure(),coverage=mask,spread_seconds=0)
    early=peel.advance(contacted,seconds=5);late=peel.advance(contacted,seconds=90)
    assert np.all(late.fields["received"][:,:4]==0)
    assert np.all(late.fields["received"][:,4:]>early.fields["received"][:,4:])
    with pytest.raises(ValueError):peel.advance(peel.peel(early),seconds=100)
    with pytest.raises(ValueError):peel.spread(contacted)
    no_touch=peel.advance(peel.spread(exposure(),contact=0),seconds=100)
    assert np.all(no_touch.fields["received"]==0)


def test_spread_direction_temperature_and_deterministic_defects():
    exposed=exposure()
    down=peel.spread(exposed,spread_seconds=20,roller_streaks=.6)
    again=peel.spread(exposed,spread_seconds=20,roller_streaks=.6)
    np.testing.assert_array_equal(down.fields["coverage"],again.fields["coverage"])
    up=peel.spread(exposed,direction="up",spread_seconds=20)
    assert down.fields["arrival_seconds"][0,0]<down.fields["arrival_seconds"][-1,0]
    assert up.fields["arrival_seconds"][0,0]>up.fields["arrival_seconds"][-1,0]
    cold=peel.advance(peel.spread(exposed,temperature_c=10),seconds=30)
    warm=peel.advance(peel.spread(exposed,temperature_c=30),seconds=30)
    assert np.all(warm.fields["received"]>=cold.fields["received"])


def test_archive_resume_group_independent_body_and_view(tmp_path):
    state=peel.advance(peel.spread(exposure()),seconds=20)
    path=tmp_path/'state.ffmaterial.npz';api.save(path,state);loaded=api.load(path)
    a=peel.advance(state,seconds=40);b=peel.advance(loaded,seconds=40)
    np.testing.assert_array_equal(a.fields["received"],b.fields["received"])
    group=peel.products(peel.peel(b));api.save(tmp_path/'pair.ffmaterial.npz',group)
    restored=api.load(tmp_path/'pair.ffmaterial.npz')
    assert len(restored.bodies)==2 and not any(body.usable_negative for body in restored.bodies)
    body=restored.bodies[0];before=body.fields["transferred"].copy()
    neutral=api.observe(body);brighter=api.observe(body,ViewingConditions(exposure_ev=1))
    np.testing.assert_allclose(brighter,neutral*2)
    np.testing.assert_array_equal(before,body.fields["transferred"])
    api.save_body(tmp_path/'receiver.ffmaterial.npz',restored,body.body_id)
    single=api.load(tmp_path/'receiver.ffmaterial.npz')
    assert len(single.bodies)==1 and single.group_id==group.group_id
    assert len(json.loads(single.provenance_json)["members"])==2
    np.testing.assert_array_equal(api.observe(single.bodies[0]),neutral)


def test_unknown_versions_and_invalid_fields_rejected(tmp_path):
    with pytest.raises(ValueError):replace(exposure(),schema_version=3)
    with pytest.raises(ValueError):peel.advance(exposure(),seconds=1)
    with pytest.raises(ValueError):peel.spread(exposure(),coverage=float("nan"))
    with pytest.raises(ValueError):peel.products(exposure())
    with pytest.raises(ValueError):api.family("daguerreotype")
    np.savez(tmp_path/'future.npz',manifest=np.array('{"version":2,"kind":"state"}'))
    with pytest.raises(ValueError):api.load(tmp_path/'future.npz')
