from dataclasses import asdict
import numpy as np
import pytest

from film_foundry.app.camera_profile import CameraIdentity,CameraProfileStore,prepare_camera_config,preserve_legacy_camera_identity
from film_foundry.app.incident_batch import prepare_incident_config
from film_foundry.model.config import DarkroomConfig
from film_foundry.core.accidents import apply_light_leak_to_exposure,apply_exposure_occlusion_to_linear


def test_profile_restarts_and_replaces_only_selected_identity(tmp_path,monkeypatch):
    values=iter((101,202,303,404))
    monkeypatch.setattr("film_foundry.app.camera_profile.secrets.randbits",lambda _:next(values))
    path=tmp_path/"camera.json";store=CameraProfileStore(path)
    assert store.current()==CameraIdentity(101,202)
    assert CameraProfileStore(path).current()==CameraIdentity(101,202)
    assert store.replace(gate=True)==CameraIdentity(101,303)
    assert store.replace(structure=True)==CameraIdentity(404,303)
    original=path.read_bytes()
    def fail(*_):raise OSError("cannot publish")
    monkeypatch.setattr("film_foundry.app.camera_profile.os.replace",fail)
    with pytest.raises(OSError):store.save(CameraIdentity(6,7))
    assert path.read_bytes()==original
    assert store.current()==CameraIdentity(404,303)


def test_camera_is_separate_from_process_batch_and_explicit_recipe_wins():
    config=DarkroomConfig();before=asdict(config)
    a=prepare_camera_config(prepare_incident_config(config,fallback_seed=11),CameraIdentity(17,23))
    b=prepare_camera_config(prepare_incident_config(config,fallback_seed=99),CameraIdentity(17,23))
    assert a.chemistry.incident_identity_seed!=b.chemistry.incident_identity_seed
    assert a.chemistry.light_leak_identity_seed==b.chemistry.light_leak_identity_seed==17
    assert a.chemistry.exposure_occlusion_identity_seed==b.chemistry.exposure_occlusion_identity_seed==23
    assert asdict(config)==before
    a.chemistry.light_leak_identity_seed=101
    assert prepare_camera_config(a,CameraIdentity(5,7)) is a


def test_legacy_common_identity_preserves_leak_and_gate_pixels():
    config=DarkroomConfig();recipe=config.chemistry
    recipe.incident_identity_seed=41;recipe.light_leak_strength=.6
    recipe.exposure_occlusion_dust=.8;recipe.exposure_occlusion_debris=.7
    prepared=preserve_legacy_camera_identity(config)
    assert recipe.light_leak_identity_seed is None and recipe.exposure_occlusion_identity_seed is None
    image=np.full((80,120,3),.4,np.float32)
    for apply in (apply_light_leak_to_exposure,apply_exposure_occlusion_to_linear):
        old,old_field=apply(image,recipe,rng=np.random.default_rng(19))
        new,new_field=apply(image,prepared.chemistry,rng=np.random.default_rng(19))
        np.testing.assert_array_equal(old,new);np.testing.assert_array_equal(old_field,new_field)


def test_explicit_gate_seed_stabilizes_dust_debris_and_does_not_consume_frame_rng():
    image=np.ones((80,120,3),np.float32)
    recipe=DarkroomConfig().chemistry
    recipe.exposure_occlusion_dust=.8;recipe.exposure_occlusion_debris=.7
    recipe.exposure_occlusion_identity_seed=17
    rng=np.random.default_rng(3);untouched=np.random.default_rng(3)
    a,mask_a=apply_exposure_occlusion_to_linear(image,recipe,rng=rng)
    b,mask_b=apply_exposure_occlusion_to_linear(image,recipe,rng=np.random.default_rng(999))
    np.testing.assert_array_equal(a,b);np.testing.assert_array_equal(mask_a,mask_b)
    assert rng.random()==untouched.random()
    recipe.exposure_occlusion_identity_seed=18
    _,other=apply_exposure_occlusion_to_linear(image,recipe,rng=np.random.default_rng(3))
    assert not np.array_equal(mask_a,other)


def test_bad_profile_is_preserved_and_never_silently_replaces_camera(tmp_path):
    path=tmp_path/"camera.json";path.write_text('{"version":999}',encoding="utf-8")
    original=path.read_bytes()
    with pytest.raises(ValueError):CameraProfileStore(path).current()
    assert path.read_bytes()==original
