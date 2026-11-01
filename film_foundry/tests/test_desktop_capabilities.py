import copy
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
import pytest
from PIL import Image

from film_foundry.app.config_assets import load_recipe_file, load_profile_file
from film_foundry.app.workflow import encode_recipe_share
from film_foundry.contracts.spectral_exposure import SpectralExposureProfile
from film_foundry.contracts.spectral_optics import SpectralMaterialProfile, SpectralObservationProfile
from film_foundry.model.config import DarkroomConfig


def spectral_config():
    root=Path(__file__).resolve().parents[2]/'android/kernel/src/test/resources'
    raw=json.loads((root/'spectral_optics_sample.json').read_text(encoding='utf-8'))
    exp=json.loads((root/'spectral_exposure_sample.json').read_text(encoding='utf-8'))
    cfg=DarkroomConfig(enable_grain=False,enable_halation=False,enable_mtf=False)
    cfg.film.spectral_exposure_profile=SpectralExposureProfile.from_value(exp['profile'])
    cfg.film.spectral_material_profile=SpectralMaterialProfile.from_value(raw['material'])
    cfg.scanner.spectral_observation_profile=SpectralObservationProfile.from_value(raw['observer'])
    cfg.film.spatial_reference_short_edge_mm=24.125
    cfg.chemistry.frame_long_edge_mm=36.75
    cfg.chemistry.program_key='color_negative'
    cfg.scanner.device_response_rgb=(((0,0),(.5,.6),(1,1)),)*3
    cfg.scanner.print_color_bias=(.93,1.01,1.04)
    cfg.scanner.highlight_color_bias=(1.08,.96,1.05)
    cfg.output.scanner_raw_border_min_px=17
    cfg.output.save_scanner_raw=False
    cfg.output.export_transparent_plate=False
    cfg.output.export_layer_pack=False
    cfg.save_sidecar=False
    cfg.seed_strategy='fixed';cfg.random_seed=83
    return cfg


def test_recipe_json_ffc2_and_profile_values_use_public_definitions(tmp_path):
    from dataclasses import asdict
    cfg=spectral_config()
    recipe=tmp_path/'recipe.json';recipe.write_text(json.dumps(asdict(cfg)),encoding='utf-8')
    share=tmp_path/'recipe.ffc2';share.write_text(encode_recipe_share(cfg),encoding='utf-8')
    for path in (recipe,share):
        loaded=load_recipe_file(path)
        assert loaded.film.spectral_material_profile==cfg.film.spectral_material_profile
        assert loaded.scanner.spectral_observation_profile==cfg.scanner.spectral_observation_profile
    for key,profile in [('spectral_exposure_profile',cfg.film.spectral_exposure_profile),
                        ('spectral_material_profile',cfg.film.spectral_material_profile),
                        ('spectral_observation_profile',cfg.scanner.spectral_observation_profile)]:
        standalone=tmp_path/(key+'.json');standalone.write_text(json.dumps(profile.as_dict()),encoding='utf-8')
        assert load_profile_file(standalone,key)==profile
        assert load_profile_file(recipe,key)==profile


def test_existing_ui_keeps_profiles_geometry_and_hidden_recipe_fields_in_all_modes(tmp_path):
    from film_foundry.tools.run_darkroom_gui import DarkroomPanel
    from film_foundry.model.session import save_session,load_session
    panel=DarkroomPanel()
    cfg=spectral_config()
    try:
        panel._apply_loaded_config(cfg)
        for mode in ('full','develop','scan','expose','develop_latent'):
            panel.pipeline_mode.set(mode);panel._update_mode_visibility()
            actual=panel._config()
            assert actual.film.spectral_exposure_profile==cfg.film.spectral_exposure_profile
            assert actual.film.spectral_material_profile==cfg.film.spectral_material_profile
            assert actual.scanner.spectral_observation_profile==cfg.scanner.spectral_observation_profile
            assert actual.film.spatial_reference_short_edge_mm==24.125
            assert actual.chemistry.frame_long_edge_mm==36.75
            assert actual.chemistry.program_key=='color_negative'
            assert actual.scanner.device_response_rgb==cfg.scanner.device_response_rgb
            assert actual.scanner.print_color_bias==cfg.scanner.print_color_bias
            assert actual.scanner.highlight_color_bias==cfg.scanner.highlight_color_bias
            assert actual.output.scanner_raw_border_min_px==17
        saved=tmp_path/'session.json';save_session(saved,panel._session_payload())
        loaded=DarkroomConfig.from_dict(load_session(saved)['config'])
        assert loaded.film.spectral_material_profile==cfg.film.spectral_material_profile
        panel._set_profile('spectral_observation_profile',None)
        assert panel._config().scanner.spectral_observation_profile is None
    finally:
        panel._preview_session.clear();panel.root.destroy()


def test_ui_preview_respects_selected_model_and_latent_file_remains_undeveloped(tmp_path,monkeypatch):
    from film_foundry.tools import run_darkroom_gui as gui
    from film_foundry.core.latent_io import save_exposed_latent,load_exposed_latent,latent_identity_from_film
    from film_foundry.core.engine import expose_image_to_latent
    from film_foundry.ui.i18n import tr
    panel=gui.DarkroomPanel()
    cfg=spectral_config()
    image=np.full((8,12,3),.4,dtype=np.float32)
    path=tmp_path/'frame.darkroom_latent.npz'
    state=expose_image_to_latent(image,cfg)
    save_exposed_latent(path,state,identity=latent_identity_from_film(cfg.film))
    try:
        panel._apply_loaded_config(cfg)
        panel.fast_mode.set(False)
        panel.pipeline_mode.set('develop_latent');panel.negative_path.set(str(path))
        requests=[]
        class DeferredThread:
            def __init__(self,**kwargs):requests.append(kwargs)
            def start(self):pass
        monkeypatch.setattr(gui,'Thread',DeferredThread)
        panel._start_preview()
        request=requests[0]['args'][0]
        assert not request.config.fast_mode
        shown=[]
        monkeypatch.setattr(panel,'_post_preview',lambda token,callback: callback())
        monkeypatch.setattr(panel,'_show_preview',lambda *args: shown.append(args))
        panel._render_preview(request)
        assert shown and shown[0][3]==tr('preview.latent')
        reloaded,meta=load_exposed_latent(path)
        assert not meta['consumed']
        np.testing.assert_array_equal(reloaded.developability,state.developability)
    finally:
        panel.root.destroy()


def test_real_headless_material_batch_exposes_then_develops_and_records(tmp_path):
    from film_foundry.app.material_batch import run_material_batch
    from film_foundry.core.latent_io import load_exposed_latent
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    cfg=spectral_config()
    source=tmp_path/'images';source.mkdir()
    for name in ('a','b'):Image.fromarray(np.full((8,12,3),128,dtype=np.uint8)).save(source/(name+'.png'))
    exposed=run_material_batch('expose',source,tmp_path/'output',cfg)
    assert exposed.completed==2 and not exposed.failures
    developed=run_material_batch('develop_latent',exposed.medium_root,tmp_path/'output',cfg)
    assert developed.completed==2 and not developed.failures
    assert not developed.record_error
    for path in exposed.medium_root.glob('*.npz'):
        with pytest.raises(ValueError,match='已消耗'):load_exposed_latent(path)
    for path in developed.medium_root.glob('*.npz'):
        medium=load_developed_medium_for_scan(path)
        assert medium.spectral_medium.profile==cfg.film.spectral_material_profile
    manifest=json.loads((developed.run_root/'film_foundry_run.json').read_text(encoding='utf-8'))
    assert manifest['batch']['completed']==2
    assert manifest['batch']['recording_strategy']=='memory_then_single_atomic_write'


@pytest.mark.parametrize('model', ('canonical', 'legacy_density', 'spectral'))
def test_preview_cache_rescans_without_reforming_and_invalidates_source_and_material(tmp_path,monkeypatch,model):
    from film_foundry.app import preview_session as preview
    from film_foundry.app.workflow import rng_for_develop
    from film_foundry.core.engine import process_array
    path=tmp_path/'photo.png';Image.fromarray(np.full((24,36,3),128,dtype=np.uint8)).save(path)
    cfg=spectral_config() if model=='spectral' else DarkroomConfig()
    if model=='legacy_density':cfg.chemistry.program_key=model
    cfg.seed_strategy='fixed';cfg.random_seed=93
    session=preview.PreviewSession()
    real=preview.develop_negative;calls=[]
    monkeypatch.setattr(preview,'develop_negative',lambda *args,**kw: (calls.append(1),real(*args,**kw))[1])
    original,first,hit=session.render(path,cfg,long_edge=100)
    assert not hit and len(calls)==1
    np.testing.assert_array_equal(first,process_array(original,cfg,rng=rng_for_develop(path,cfg)))
    optical=session._medium.optical_density_rgb
    if optical is not None:optical=optical.copy()
    else:assert session._medium.layer_masters_available
    changed=copy.deepcopy(cfg);changed.look.print_exposure_ev=.8
    _original,second,hit=session.render(path,changed,long_edge=100)
    assert hit and len(calls)==1
    np.testing.assert_array_equal(second,process_array(original,changed,rng=rng_for_develop(path,changed)))
    if optical is not None:np.testing.assert_array_equal(optical,session._medium.optical_density_rgb)
    changed.look.exposure_ev+=1
    session.render(path,changed,long_edge=100);assert len(calls)==2
    Image.fromarray(np.full((24,36,3),129,dtype=np.uint8)).save(path)
    session.render(path,changed,long_edge=100);assert len(calls)==3
    changed.seed_strategy='random'
    session.render(path,changed,long_edge=100);session.render(path,changed,long_edge=100)
    assert len(calls)==5 and session._medium is None


def test_material_batch_cancel_does_not_start_next_frame(tmp_path,monkeypatch):
    from film_foundry.app import material_batch as batch
    from film_foundry.app.control import CancelFlag,WorkflowControl
    cfg=spectral_config()
    source=tmp_path/'images';source.mkdir()
    for name in ('a','b'):Image.fromarray(np.full((8,12,3),128,dtype=np.uint8)).save(source/(name+'.png'))
    flag=CancelFlag();control=WorkflowControl(cancel_requested=flag.requested)
    real=batch.run_request;started=[]
    def request(value):
        started.append(value.input_path)
        result=real(value)
        flag.cancel()
        return result
    monkeypatch.setattr(batch,'run_request',request)
    result=batch.run_material_batch('expose',source,tmp_path/'output',cfg,control=control)
    assert result.cancelled and result.completed==1 and len(started)==1
    assert len(list(result.medium_root.glob('*.npz')))==1
    manifest=json.loads((result.run_root/'film_foundry_run.json').read_text(encoding='utf-8'))
    assert manifest['run']['status']==manifest['batch']['status']=='cancelled'
