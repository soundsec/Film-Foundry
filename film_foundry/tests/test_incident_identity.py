"""Shared defects across frames, independent frame grain, and real UI/batch IO."""
from dataclasses import asdict
import copy
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import numpy as np
import pytest
from PIL import Image

from film_foundry.app.incident_batch import prepare_incident_config
from film_foundry.model.config import DarkroomConfig,ChemistryConfig
from film_foundry.core.accidents import apply_light_leak_to_exposure,apply_exposure_occlusion_to_linear,apply_density_accident_components


def config():
    cfg=DarkroomConfig(enable_mtf=False,enable_halation=False)
    cfg.chemistry.incident_identity_seed=781
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False;cfg.save_sidecar=False
    return cfg


def test_application_freezes_one_identity_and_preserves_recipe():
    cfg=DarkroomConfig();before=asdict(cfg)
    prepared=prepare_incident_config(cfg,fallback_seed=71)
    assert prepared.chemistry.incident_identity_seed==71
    assert asdict(cfg)==before
    assert prepare_incident_config(prepared,fallback_seed=99) is prepared
    a=prepare_incident_config(cfg,roll_id="ROLL-12")
    b=prepare_incident_config(cfg,roll_id="ROLL-12")
    c=prepare_incident_config(cfg,roll_id="ROLL-13")
    assert a.chemistry.incident_identity_seed==b.chemistry.incident_identity_seed
    assert a.chemistry.incident_identity_seed!=c.chemistry.incident_identity_seed


def test_shared_leak_and_gate_geometry_do_not_depend_on_frame_rng_or_other_toggles(monkeypatch):
    from film_foundry.core import accidents
    recipe=config().chemistry;recipe.light_leak_strength=.6
    image=np.full((120,180,3),.3,np.float32)
    a,field=apply_light_leak_to_exposure(image,recipe,np.random.default_rng(1))
    b,other=apply_light_leak_to_exposure(image,recipe,np.random.default_rng(99))
    np.testing.assert_array_equal(a,b);np.testing.assert_array_equal(field,other)
    recipe.exposure_occlusion_hair=1.;recipe.exposure_occlusion_dust=1.
    a,field=apply_exposure_occlusion_to_linear(image,recipe,np.random.default_rng(1))
    b,other=apply_exposure_occlusion_to_linear(image,recipe,np.random.default_rng(99))
    np.testing.assert_array_equal(a,b);np.testing.assert_array_equal(field,other)
    assert float(field.max())>.1
    fields=[];real=accidents._dust_occlusion_field
    def capture(shape,amount,rng):
        result=real(shape,amount,rng);fields.append(result);return result
    monkeypatch.setattr(accidents,"_dust_occlusion_field",capture)
    for shape in ((120,180),(240,360)):
        apply_exposure_occlusion_to_linear(np.ones(shape+(3,),np.float32),recipe,np.random.default_rng(7))
    np.testing.assert_array_equal(fields[0],fields[1])
    recipe.exposure_occlusion_hair=0
    apply_exposure_occlusion_to_linear(image,recipe,np.random.default_rng(31))
    np.testing.assert_array_equal(fields[0],fields[2])
    recipe.incident_identity_seed=782
    _,changed=apply_light_leak_to_exposure(image,recipe,np.random.default_rng(1))
    assert not np.array_equal(field,changed)


def test_shared_bath_variation_and_density_defects_leave_frame_random_stream_independent():
    from film_foundry.core.engine import _apply_process_variation
    cfg=config();cfg.chemistry.process_variation=1
    frame=np.random.default_rng(19);reference=np.random.default_rng(19)
    first=copy.deepcopy(cfg);second=copy.deepcopy(cfg)
    a=_apply_process_variation(first,frame)
    b=_apply_process_variation(second,np.random.default_rng(99))
    assert a==b and asdict(first)==asdict(second)
    assert frame.random()==reference.random()
    recipe=cfg.chemistry;recipe.chemical_stain=.7;recipe.silver_plating=.7;recipe.reticulation_strength=.7
    density=np.ones((60,90,3),np.float32)
    first=apply_density_accident_components(density,recipe,np.random.default_rng(2))
    second=apply_density_accident_components(density,recipe,np.random.default_rng(98))
    for key in first.maps:np.testing.assert_array_equal(first.maps[key],second.maps[key])
    recipe.chemical_stain=0
    third=apply_density_accident_components(density,recipe,np.random.default_rng(2))
    for key in ("silver_plating","reticulation"):np.testing.assert_array_equal(first.maps[key],third.maps[key])


def test_bath_variation_does_not_change_identified_capture_obstruction():
    from film_foundry.core.engine import develop_negative
    cfg=config();cfg.enable_grain=False;cfg.chemistry.exposure_occlusion_hair=1.
    image=np.full((60,90,3),.4,np.float32)
    baseline=develop_negative(image,cfg,np.random.default_rng(1))
    cfg.chemistry.process_variation=1.
    changed=develop_negative(image,cfg,np.random.default_rng(2))
    np.testing.assert_array_equal(baseline.after_mtf,changed.after_mtf)
    assert changed.metadata["runtime_config"].chemistry.exposure_occlusion_hair==1.
    assert changed.metadata["process_variation"]["exposure_occlusion_hair_delta"]==0.


def test_shared_roll_does_not_freeze_grain_and_npz_records_identity(tmp_path):
    from film_foundry.core.engine import develop_negative,save_developed_medium_at_path
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    cfg=config();cfg.chemistry.light_leak_strength=.7;cfg.chemistry.process_variation=.8
    cfg.chemistry.exposure_occlusion_hair=1
    image=np.full((80,120,3),.4,np.float32);original=image.copy()
    first=develop_negative(image,cfg,np.random.default_rng(3));second=develop_negative(image,cfg,np.random.default_rng(98))
    assert first.metadata["light_leak_pattern"]==second.metadata["light_leak_pattern"]
    assert first.metadata["process_variation"]==second.metadata["process_variation"]
    np.testing.assert_array_equal(first.after_halation,second.after_halation)
    assert not np.array_equal(first.density_grain,second.density_grain)
    np.testing.assert_array_equal(image,original)
    path=tmp_path/"frame.darkroom_negative.npz"
    save_developed_medium_at_path(tmp_path/"source.png",path,first,cfg)
    restored=load_developed_medium_for_scan(path)
    assert restored.metadata["film_process_model"]["incident_identity"]["seed"]==781


def test_exposure_batch_and_replay_keep_saved_incident_identity(tmp_path):
    from film_foundry.app.material_batch import run_material_batch
    from film_foundry.app.replay_batch import load_resume_plan,run_resume_batch
    from film_foundry.core.latent_io import load_exposed_latent
    source=tmp_path/"source";source.mkdir()
    for name in ("a","b"):Image.fromarray(np.full((24,36,3),110,np.uint8)).save(source/(name+".png"))
    cfg=config();cfg.chemistry.incident_identity_seed=None;cfg.chemistry.light_leak_strength=.7
    settings={"pipeline_mode":"expose","frame_identity":{"roll_id":"R12","frame_index":1}}
    batch=run_material_batch("expose",source,tmp_path/"out",cfg,settings=settings)
    assert batch.completed==2 and not batch.failures
    paths=sorted(batch.medium_root.glob("*.npz"));first,_=load_exposed_latent(paths[0]);second,_=load_exposed_latent(paths[1])
    np.testing.assert_array_equal(first.developability,second.developability)
    manifest=batch.run_root/"film_foundry_run.json";payload=json.loads(manifest.read_text(encoding="utf-8"))
    seed=payload["config"]["chemistry"]["incident_identity_seed"]
    paths[1].unlink()
    plan=load_resume_plan(manifest);assert plan.config.chemistry.incident_identity_seed==seed
    replay=run_resume_batch(plan);assert replay.reused==1 and replay.completed==1 and not replay.failures
    second,_=load_exposed_latent(paths[1]);np.testing.assert_array_equal(first.developability,second.developability)
    assert cfg.chemistry.incident_identity_seed is None


def test_incident_seed_recipe_json_share_and_cli(tmp_path):
    from film_foundry.app.workflow import encode_recipe_share
    from film_foundry.app.config_assets import load_recipe_file
    from film_foundry.app.cli import build_parser,_apply_common_config
    cfg=config();path=tmp_path/"recipe.json";path.write_text(json.dumps(asdict(cfg)),encoding="utf-8")
    assert load_recipe_file(path).chemistry.incident_identity_seed==781
    share=tmp_path/"recipe.ffc2";share.write_text(encode_recipe_share(cfg),encoding="utf-8")
    assert load_recipe_file(share).chemistry.incident_identity_seed==781
    args=build_parser().parse_args(["expose","input","output","--incident-seed","782"])
    assert _apply_common_config(args).chemistry.incident_identity_seed==782


def test_real_cli_exposure_keeps_one_batch_identity_and_capture_provenance(tmp_path):
    from film_foundry.app.cli import main
    from film_foundry.core.latent_io import load_exposed_latent
    source=tmp_path/"input";source.mkdir();output=tmp_path/"latent"
    for name in ("a","b"):Image.fromarray(np.full((18,27,3),120,np.uint8)).save(source/(name+".png"))
    assert main(["expose",str(source),str(output),"--incident-seed","63","--light-leak",".6","--seed-strategy","random"])==0
    paths=sorted(output.glob("*.npz"));a,ameta=load_exposed_latent(paths[0]);b,bmeta=load_exposed_latent(paths[1])
    assert ameta["capture_incident_identity"]["seed"]==bmeta["capture_incident_identity"]["seed"]==63
    np.testing.assert_array_equal(a.developability,b.developability)


@pytest.mark.parametrize("seed",[-1,2**32,True,1.5])
def test_incident_seed_rejected_before_exposure(seed):
    from film_foundry.core.engine import expose_image_to_latent
    cfg=config();cfg.chemistry.incident_identity_seed=seed
    with pytest.raises(ValueError,match="incident_identity_seed"):
        expose_image_to_latent(np.ones((4,6,3),np.float32),cfg)


def test_obstruction_switches_preserve_imported_values_and_preview_formal_identity(tmp_path,monkeypatch):
    from film_foundry.tools import run_darkroom_gui as legacy
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.qt import QtWidgets
    monkeypatch.setenv("FILM_FOUNDRY_UI_SETTINGS",str(tmp_path/"ui.json"))
    panel=DesktopPanel();panel.auto_preview.set(False);panel._change_auto_preview();requests=[]
    class DeferredThread:
        def __init__(self,**kwargs):requests.append(kwargs)
        def start(self):pass
    monkeypatch.setattr(legacy,"Thread",DeferredThread)
    try:
        cfg=panel._config();cfg.chemistry.exposure_occlusion_dust=.4;panel._apply_loaded_config(cfg)
        toggle=panel.fields["exposure_occlusion_dust"].qt.findChild(QtWidgets.QCheckBox)
        assert toggle.isChecked() and not panel.fields["exposure_occlusion_dust"].qt.findChildren(QtWidgets.QSlider)
        assert panel._config().chemistry.exposure_occlusion_dust==.4
        toggle.click();assert panel._config().chemistry.exposure_occlusion_dust==0
        toggle.click();assert panel._config().chemistry.exposure_occlusion_dust==1
        preview=panel._fast_preview_config();panel._start_process()
        formal=requests[-1]["args"][1]
        assert preview.chemistry.incident_identity_seed==formal.chemistry.incident_identity_seed
        assert formal.seed_strategy==panel._config().seed_strategy
        assert requests[-1]["args"][7]["config"]["chemistry"]["incident_identity_seed"]==formal.chemistry.incident_identity_seed
        assert panel._session_payload()["config"]["chemistry"]["incident_identity_seed"]==formal.chemistry.incident_identity_seed
        panel._finish_process();panel._new_incident_identity()
        assert panel._fast_preview_config().chemistry.incident_identity_seed!=preview.chemistry.incident_identity_seed
        panel._open_editor("film_foundry.tools.run_develop_process_editor")
        editor=panel._editor_windows["develop"]
        assert editor._config_from_controls().chemistry.incident_identity_seed==panel._config().chemistry.incident_identity_seed
    finally:panel.root.destroy()
