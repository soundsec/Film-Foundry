import copy
import numpy as np
import pytest

from film_foundry.core.engine import develop_negative, expose_image_to_latent, develop_negative_from_latent, scan_medium_output, scan_negative
from film_foundry.core.latent_io import save_exposed_latent, load_exposed_latent, latent_identity_from_film
from film_foundry.model.config import DarkroomConfig


def source():
    return np.linspace(.1,.85,64*120*3,dtype=np.float32).reshape(64,120,3)


def recipe():
    cfg=DarkroomConfig(enable_mtf=False,enable_halation=False,enable_grain=False)
    cfg.film_border.enabled=True;cfg.film_border.stock_label='FOUNDRY'
    return cfg


def test_rebate_scans_preserve_gate_normalization_and_are_read_only():
    cfg=recipe();clean=copy.deepcopy(cfg);clean.film_border.enabled=False
    a=develop_negative(source(),clean);b=develop_negative(source(),cfg)
    rows=b.metadata['formed_film_rebate']['rebate_rows']
    original=b.optical_density_rgb.copy()
    np.testing.assert_array_equal(a.optical_density_rgb,b.optical_density_rgb[rows:-rows])
    for reference in (False,True):
        cfg.scanner.include_clear_base_border=clean.scanner.include_clear_base_border=reference
        plain=scan_medium_output(a,clean).output_srgb
        bordered=scan_medium_output(b,cfg).output_srgb
        if not reference:
            np.testing.assert_allclose(plain,bordered[rows:-rows],atol=2e-6)
            np.testing.assert_allclose(scan_negative(b,cfg).output_srgb,bordered,atol=2e-6)
    np.testing.assert_array_equal(original,b.optical_density_rgb)
    assert not b.optical_density_rgb.flags.writeable
    assert np.count_nonzero(np.all(b.optical_density_rgb[:rows]==0,axis=-1))>0
    from film_foundry.app.preview_inputs import preview_medium
    from film_foundry.core.formed_film_border import image_gate_rows
    before=copy.deepcopy(b.metadata)
    small=preview_medium(b,60);gate=image_gate_rows(small)
    assert gate is not None and 0<gate[0]<gate[1]<small.frame_shape[0]
    assert b.metadata==before


def test_latent_freezes_edges_and_checkpoint_roundtrip_keeps_old_compatibility(tmp_path):
    from film_foundry.core.film_process.checkpoint_io import pack_state,unpack_state
    cfg=recipe();cfg.film_border.frame_number=8;cfg.film_border.factory_layout='text_code'
    latent=expose_image_to_latent(source(),cfg)
    path=tmp_path/'frame.darkroom_latent.npz'
    save_exposed_latent(path,latent,identity=latent_identity_from_film(cfg.film))
    loaded,_=load_exposed_latent(path)
    assert loaded.film_border_snapshot['frame_number']==8
    assert loaded.film_border_snapshot['factory_layout']=='text_code'
    cfg.film_border.enabled=False
    result=develop_negative_from_latent(loaded.copy(),cfg)
    assert result.frame_shape[0]>64
    arrays={};record=pack_state(loaded,'latent_',arrays)
    restored=unpack_state(arrays,record,'latent_')
    assert restored.film_border_snapshot==latent.film_border_snapshot
    record.pop('film_border_snapshot')
    old=unpack_state(arrays,record,'latent_')
    cfg.film_border.enabled=True
    assert develop_negative_from_latent(old,cfg).frame_shape==source().shape
    copied=loaded.copy();copied.film_border_snapshot['enabled']=False
    assert loaded.film_border_snapshot['enabled']


def test_spectral_rebate_survives_public_save_load_scan(tmp_path):
    from film_foundry.tests.test_spectral_optics import config
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    from film_foundry.core.spectral_optics import capture_spectral_medium
    from PIL import Image
    cfg=config();cfg.film_border.enabled=True
    cfg.film_border.factory_layout='cinema';cfg.film_border.factory_barcode='code128c'
    cfg.film_border.factory_exposure_ev=-1.25;cfg.film_border.factory_exposure_variation=.2
    source_path=tmp_path/'source.png';Image.fromarray((source()*255).astype('uint8')).save(source_path)
    latent_path=tmp_path/'frame.darkroom_latent.npz';formed=tmp_path/'film.npz'
    run_request(WorkflowRequest('expose',source_path,latent_path,cfg))
    run_request(WorkflowRequest('develop_latent',latent_path,formed,cfg))
    medium=load_developed_medium_for_scan(formed)
    record=medium.metadata['developed_medium']['formed_film_rebate']
    assert record['factory_exposure_ev']==-1.25 and record['barcode']['generated_sides']==['bottom']
    assert medium.frame_shape[0]==64+2*record['rebate_rows']
    holes=np.all(medium.spectral_medium.component_amounts==0,axis=-1)
    assert holes.any()
    captured=capture_spectral_medium(medium.spectral_medium,cfg.scanner.spectral_observation_profile)
    np.testing.assert_allclose(captured[holes],1,atol=1e-6)
    scan=run_request(WorkflowRequest('scan',formed,tmp_path/'scan.png',cfg))
    assert scan.extra['scanned'].output_srgb.shape==medium.frame_shape
    assert np.isfinite(scan.extra['scanned'].output_srgb).all()


@pytest.mark.parametrize('program', ['color_negative','color_reversal','bw_negative','bw_reversal'])
def test_rebate_response_and_tiled_scan_follow_actual_process(program):
    cfg=recipe();cfg.chemistry.program_key=program
    if program.startswith('bw'):
        cfg.film.color_process='bw'
    medium=develop_negative(source(),cfg)
    plain=scan_medium_output(medium,cfg).output_srgb
    cfg.processing.scan_tile_threshold_megapixels=0;cfg.processing.scan_tile_rows=13
    tiled=scan_medium_output(medium,cfg).output_srgb
    np.testing.assert_allclose(plain,tiled,atol=2e-6)
    assert np.isfinite(plain).all()
    assert medium.image_polarity==('positive' if program.endswith('reversal') else 'negative')
