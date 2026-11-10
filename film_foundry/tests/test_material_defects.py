import json
from dataclasses import replace
import numpy as np
import pytest
from film_foundry.core import peel_apart as peel, integral_instant as instant, silver_plate as plate
from film_foundry.core.material_family import ViewingConditions
from film_foundry.app import material_families as api


IMAGE=np.full((48,64,3),.18,np.float32)


@pytest.mark.parametrize("module",[peel,instant,plate])
def test_opening_changes_unprocessed_material_and_survives_archive(module,tmp_path):
    clean=module.expose(IMAGE,group_id="opening",seed=18)
    before={k:v.copy() for k,v in clean.fields.items()}
    leaked=module.light_leak(clean,strength=.8,edge="left")
    key="latent" if module is plate else "immobile"
    delta=leaked.fields[key]-clean.fields[key]
    assert delta.min()>=0 and delta[:,:4].mean()>delta[:,-4:].mean()
    for k,v in before.items():np.testing.assert_array_equal(v,clean.fields[k])
    if module is plate:
        clean_end=plate.fix(plate.develop(clean,seconds=120))
        final=plate.fix(plate.develop(leaked,seconds=120))
        assert final.fields["particles"].mean()>clean_end.fields["particles"].mean()
    else:
        def finish(state):
            if module is instant:return instant.stabilize(instant.spread(instant.eject(state)))
            return peel.peel(peel.advance(peel.spread(state),seconds=180))
        clean_end=finish(clean);final=finish(leaked)
        assert final.fields["received"].mean()<clean_end.fields["received"].mean()
        np.testing.assert_allclose(final.fields["immobile"]+final.fields["remaining"]+final.fields["received"],1,atol=2e-6)
    assert json.loads(final.process_json)["events"][0]["edge"]=="left"
    with pytest.raises(ValueError):module.light_leak(final,strength=.5)
    path=tmp_path/'material.npz';api.save(path,final);restored=api.load(path)
    np.testing.assert_array_equal(module.preview(final),module.preview(restored))
    assert restored.process_json==final.process_json


@pytest.mark.parametrize("transfer",["dye","silver_compound"])
def test_decayed_reagent_and_contact_voids_preserve_untransferred_material(transfer,tmp_path):
    exposed=peel.expose(IMAGE,peel.PeelMaterial(transfer=transfer),group_id="decay",seed=23)
    spread=peel.spread(exposed,reagent_decay=.8,contact_defects=.9)
    again=peel.spread(exposed,reagent_decay=.8,contact_defects=.9)
    np.testing.assert_array_equal(spread.fields["coverage"],again.fields["coverage"])
    assert np.std(spread.fields["coverage"])>.05 and np.std(spread.fields["contact"])>.05
    first=peel.advance(spread,seconds=30);path=tmp_path/'contact.npz';api.save(path,first)
    resumed=peel.advance(api.load(path),seconds=150)
    once=peel.advance(spread,seconds=180)
    np.testing.assert_array_equal(resumed.fields["received"],once.fields["received"])
    dead=(spread.fields["coverage"]==0)|(spread.fields["contact"]==0)
    assert dead.any()
    long_wait=peel.advance(spread,seconds=100000)
    assert np.all(long_wait.fields["received"][dead]==0)
    assert np.all(long_wait.fields["remaining"][dead]>0)
    clean=peel.advance(peel.spread(exposed),seconds=180)
    assert once.fields["received"].mean()<clean.fields["received"].mean()
    np.testing.assert_allclose(once.fields["immobile"]+once.fields["remaining"]+once.fields["received"],1,atol=2e-6)
    with pytest.raises(ValueError):peel.spread(exposed,reagent_decay=float("nan"))


def test_surface_wear_is_local_accumulative_readonly_observation_and_saved(tmp_path):
    state=plate.fix(plate.develop(plate.expose(IMAGE,group_id="wear",seed=5),seconds=120))
    assert plate.weather(state) is state
    damaged=plate.weather(state,scratches=.8,pitting=.7,edge_tarnish=.8)
    again=plate.weather(state,scratches=.8,pitting=.7,edge_tarnish=.8)
    np.testing.assert_array_equal(damaged.fields["damage"],again.fields["damage"])
    np.testing.assert_array_equal(state.fields["particles"],damaged.fields["particles"])
    assert damaged.fields["damage"].std()>.03
    assert damaged.fields["tarnish"][:3].mean()>damaged.fields["tarnish"][20:28,28:36].mean()
    twice=plate.weather(damaged,scratches=.8,pitting=.7,edge_tarnish=.8)
    assert np.all(twice.fields["damage"]>=damaged.fields["damage"]-1e-7)
    body=plate.products(damaged).bodies[0];before={k:v.copy() for k,v in body.fields.items()}
    api.observe(body,ViewingConditions(view_direction=(.5,0,.866)))
    for k,v in before.items():np.testing.assert_array_equal(v,body.fields[k])
    path=tmp_path/'plate.npz';api.save(path,plate.products(damaged))
    np.testing.assert_array_equal(api.observe(api.load(path).bodies[0]),api.observe(body))
    with pytest.raises(ValueError):plate.weather(plate.expose(IMAGE,group_id="unfixed"),scratches=1)
    with pytest.raises(ValueError):plate.weather(state,pitting=float("nan"))


def test_new_controls_off_preserve_default_and_legacy_snapshot():
    exposed=peel.expose(IMAGE,group_id="legacy")
    original=peel.spread(exposed)
    legacy=json.loads(original.process_json);legacy.pop("reagent_decay");legacy.pop("contact_defects")
    loaded=replace(original,process_json=json.dumps(legacy))
    expected=peel.advance(loaded,seconds=60)
    current=peel.advance(peel.spread(exposed,reagent_decay=0,contact_defects=0),seconds=60)
    np.testing.assert_array_equal(expected.fields["received"],current.fields["received"])
    assert peel.light_leak(exposed,strength=0) is exposed
