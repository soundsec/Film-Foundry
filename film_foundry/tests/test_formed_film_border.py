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
    from film_foundry.core.formed_film_border import validate_border_snapshot
    assert validate_border_snapshot(restored.film_border_snapshot)==validate_border_snapshot(latent.film_border_snapshot)
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
    cfg.film_border.factory_light_color_rgb=(.2,1.,.05)
    source_path=tmp_path/'source.png';Image.fromarray((source()*255).astype('uint8')).save(source_path)
    latent_path=tmp_path/'frame.darkroom_latent.npz';formed=tmp_path/'film.npz'
    run_request(WorkflowRequest('expose',source_path,latent_path,cfg))
    run_request(WorkflowRequest('develop_latent',latent_path,formed,cfg))
    medium=load_developed_medium_for_scan(formed)
    record=medium.metadata['developed_medium']['formed_film_rebate']
    assert record['factory_exposure_ev']==-1.25 and record['barcode']['generated_sides']==['bottom']
    assert record['factory_light_color_rgb']==[.2,1.,.05]
    assert medium.frame_shape[0]==64+2*record['rebate_rows']
    holes=np.all(medium.spectral_medium.component_amounts==0,axis=-1)
    assert holes.any()
    captured=capture_spectral_medium(medium.spectral_medium,cfg.scanner.spectral_observation_profile)
    np.testing.assert_allclose(captured[holes],1,atol=1e-6)
    scan=run_request(WorkflowRequest('scan',formed,tmp_path/'scan.png',cfg))
    assert scan.extra['scanned'].output_srgb.shape==medium.frame_shape
    assert np.isfinite(scan.extra['scanned'].output_srgb).all()


@pytest.mark.parametrize("mode", ["attached","native","sprocket_panorama"])
def test_ink_is_transmission_on_printed_stock_and_glow_is_exposure(mode):
    from dataclasses import replace
    from film_foundry.tests.test_spectral_optics import config
    cfg = config()
    cfg.film_border = replace(cfg.film_border, enabled=True, formation_mode=mode, factory_mark_type="ink",
                              factory_ink_transmission_rgb=(.25,.5,.8), factory_pattern_top="FFEM1:1x1:80", rebate_glow=.35)
    def form(border):
        return develop_negative(source(), replace(cfg, film_border=border), np.random.default_rng(31))
    film = form(cfg.film_border)
    clear = form(replace(cfg.film_border, factory_ink_transmission_rgb=(1.,1.,1.)))
    cold = form(replace(cfg.film_border, rebate_glow=0.))
    delta = film.optical_density_rgb-clear.optical_density_rgb
    np.testing.assert_array_equal(film.spectral_medium.component_amounts,clear.spectral_medium.component_amounts)
    np.testing.assert_allclose(delta,film.spectral_medium.post_process_density_rgb-clear.spectral_medium.post_process_density_rgb,atol=2e-6)
    np.testing.assert_allclose(delta.max(axis=(0,1)), -np.log10([.25,.5,.8]),atol=2e-6)
    rows = film.metadata["formed_film_rebate"]["rebate_rows"]
    np.testing.assert_array_equal(film.optical_density_rgb[rows:-rows],cold.optical_density_rgb[rows:-rows])
    holes = np.all(clear.optical_density_rgb == 0,axis=2)
    assert holes.any() and np.all(film.optical_density_rgb[holes] == 0)
    assert np.max(abs(film.optical_density_rgb-cold.optical_density_rgb)) > .001
    assert np.isfinite(scan_medium_output(film,cfg).output_srgb).all()


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


def test_custom_plate_survives_sharing_and_exposes_or_removes_emulsion():
    from dataclasses import asdict, replace
    from film_foundry.contracts.config_share import encode_config_share, decode_config_share
    from film_foundry.core.film_border import resolve_film_border_layout, validate_film_border_config
    from film_foundry.core.formed_film_border import _strip_masks
    from film_foundry.tests.test_spectral_optics import config as spectral_config

    cfg = spectral_config()
    plate = {"font": "sans", "top": [["barcode", 22, 96], ["number", 2, 14]], "bottom": []}
    cfg.film_border = replace(cfg.film_border, enabled=True, factory_composition=plate,
                              frame_number=7, factory_barcode="code128c", stock_label="FF COLOR 400")
    share = encode_config_share({"film_border": asdict(cfg.film_border)})
    cfg.film_border = DarkroomConfig.from_dict(decode_config_share(share)).film_border
    cfg.film_border = validate_film_border_config(cfg.film_border)
    assert cfg.film_border.factory_composition["top"][0][0] == "number"
    with pytest.raises(ValueError):
        validate_film_border_config(replace(cfg.film_border, factory_composition=dict(plate, top=[["number", 8, 20], ["text", 10, 40]])))
    frame = source()
    layout = resolve_film_border_layout(cfg.film_border, frame.shape)
    mask, holes = _strip_masks(cfg.film_border, layout, "FF COLOR 400")
    rw = layout.image_rect[1]
    marked = (mask[:rw] == 1) & (holes[:rw] == 0)
    assert marked.any() and not mask[rw:].any()
    first = develop_negative(frame, cfg)
    cfg.film_border = replace(cfg.film_border, factory_mark_type="laser_etched")
    etched = develop_negative(frame, cfg)
    assert first.metadata["formed_film_rebate"]["barcode"]["generated_sides"] == ["top"]
    assert etched.metadata["formed_film_rebate"]["factory_response"] == "bare_support_ablation_v1"
    assert not np.allclose(first.optical_density_rgb[:rw][marked], etched.optical_density_rgb[:rw][marked])
    np.testing.assert_array_equal(first.optical_density_rgb[rw:-rw], etched.optical_density_rgb[rw:-rw])
    assert np.all(etched.spectral_medium.emulsion_coverage[:rw][marked] == 0)
    assert np.all(etched.spectral_medium.component_amounts[:rw,:,6][marked] == 1)
    # Adjacent frames keep their own number/code when a custom plate crosses a boundary.
    def dose(border):
        return _strip_masks(border, layout, "FF COLOR 400")[0]
    width = frame.shape[1]
    shift = width // 4
    slipped = dose(replace(cfg.film_border, loading_offset=-.25))
    expected = np.concatenate((mask[:,shift:], dose(replace(cfg.film_border,frame_number=8))[:,:shift]),axis=1)
    np.testing.assert_array_equal(slipped, expected)


def test_edge_leak_is_frozen_with_latent_and_respects_perforations(tmp_path):
    from dataclasses import replace
    from film_foundry.core.film_border import resolve_film_border_layout
    from film_foundry.core.formed_film_border import _strip_masks
    c=recipe()
    c.chemistry.light_leak_strength=.8
    c.chemistry.light_leak_identity_seed=55
    c.chemistry.light_leak_template='perimeter'
    c.film_border=replace(c.film_border,factory_barcode='blocks',factory_mark_type='hard')
    image=source()
    exposed=expose_image_to_latent(image,c,np.random.default_rng(3))
    path=tmp_path/'frame.darkroom_latent.npz'
    save_exposed_latent(path,exposed,identity=latent_identity_from_film(c.film))
    loaded,_=load_exposed_latent(path)
    c.chemistry.light_leak_strength=0
    original=loaded.copy()
    formed=develop_negative_from_latent(loaded,c,np.random.default_rng(5))
    original.film_border_snapshot.pop('edge_leak_rgb_v1')
    old=develop_negative_from_latent(original,c,np.random.default_rng(5))
    rows=formed.metadata['formed_film_rebate']['rebate_rows']
    np.testing.assert_array_equal(formed.optical_density_rgb[rows:-rows],old.optical_density_rgb[rows:-rows])
    dose,hole=_strip_masks(c.film_border,resolve_film_border_layout(c.film_border,image.shape),'FOUNDRY')
    def edges(m):
        return np.concatenate([m.optical_density_rgb[:rows],m.optical_density_rgb[-rows:]])
    current=edges(formed)
    assert np.all(current[hole==1]==0)
    for zone in (dose==0,dose>.25):
        assert np.any(zone & (hole==0))
        assert np.max(abs(current-edges(old))[zone & (hole==0)])>.001


def test_reopening_bordered_npz_with_companion_uses_the_material(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest, run_request
    from film_foundry.app.workflow import transmission_raw_path_for_medium
    from film_foundry.core.negative_io import load_developed_medium_for_scan

    cfg = recipe()
    cfg.film_border.format_key = "half_frame"
    cfg.film_border.formation_mode = "native"
    cfg.output.save_scanner_raw = True
    src = tmp_path / "half.png"
    Image.fromarray((source() * 255).astype("uint8")).save(src)
    full = run_request(WorkflowRequest("full", src, tmp_path / "print.png", cfg))
    archive = full.medium_path
    companion = transmission_raw_path_for_medium(archive)
    assert companion.is_file()

    cfg.film_border.enabled = False
    cfg.scanner.transmission_light_temperature_k = 3200
    cfg.scanner.transmission_light_ev = -1.5
    reopened = run_request(WorkflowRequest("scan", archive, tmp_path / "rescan.png", cfg))
    assert reopened.source_kind == "density_npz" and reopened.source_path == archive
    medium = load_developed_medium_for_scan(archive)
    expected = scan_medium_output(medium, cfg, cfg.scanner.interpretation_mode)
    np.testing.assert_allclose(reopened.extra["scanned"].output_srgb, expected.output_srgb, atol=2e-6)

    captured = run_request(WorkflowRequest("scan", companion, tmp_path / "capture.png", cfg))
    assert captured.source_kind == "scanner_raw_tiff" and captured.source_path == companion


def test_registered_perforations_show_actual_lamp_before_crop():
    from dataclasses import replace
    from film_foundry.core.color import linear_to_srgb
    from film_foundry.core.scanner import capture_optical_density, transmission_illuminant_rgb
    from film_foundry.core.scan_framing import frame_scan
    cfg = recipe()
    cfg.film_border.loading_offset = .44
    cfg.film_border.sprocket_rows = "bottom"
    film = develop_negative(source(), cfg)
    record = film.metadata["formed_film_rebate"]
    w = film.frame_shape[1]
    left, top, right, bottom = record["perforation_windows_v1"][0]
    x = int((((left+right)/2 + .44) % 1)*w)
    y = int((top+bottom)/2*film.frame_shape[0])
    original = film.optical_density_rgb.copy()
    old = copy.deepcopy(film.metadata)
    del old["formed_film_rebate"]["perforation_windows_v1"]
    for ev, kelvin in [(0., 6500.), (-1.5, 3200.)]:
        cfg.scanner.transmission_light_ev = ev
        cfg.scanner.transmission_light_temperature_k = kelvin
        lamp = linear_to_srgb(capture_optical_density(np.zeros((1,1,3), np.float32),
                 cfg.scanner, illuminant_rgb=transmission_illuminant_rgb(cfg.scanner)))[0,0]
        scan = scan_negative(film,cfg)
        np.testing.assert_allclose(scan.output_srgb[y,x],lamp,atol=1e-6)
        optimized = scan_medium_output(film,cfg).output_srgb
        np.testing.assert_allclose(optimized,scan.output_srgb,atol=2e-6)
        np.testing.assert_allclose(scan_medium_output(replace(film,metadata=old),cfg).output_srgb,optimized,atol=2e-6)
        assert np.max(abs(optimized[-1-y,x]-lamp))>.05
    cfg.scanner.crop_rect = (.03,.02,.97,.98)
    cfg.scanner.straighten_degrees = 3.
    np.testing.assert_allclose(scan_negative(film,cfg).output_srgb,frame_scan(optimized,cfg.scanner),atol=2e-6)
    np.testing.assert_array_equal(original,film.optical_density_rgb)


@pytest.mark.parametrize("mode", ["attached","native","sprocket_panorama"])
def test_whole_strip_transport_keeps_gate_holes_and_frozen_orientation(mode):
    from dataclasses import replace
    from film_foundry.core.film_border import resolve_film_border_layout
    from film_foundry.core.formed_film_border import image_gate_rows
    from film_foundry.core.color import linear_to_srgb
    from film_foundry.core.scanner import capture_optical_density, transmission_illuminant_rgb
    c = recipe()
    image = source()[:60,:80]
    key = "half_frame"
    if mode=="sprocket_panorama":
        with pytest.raises(ValueError,match="135"):
            resolve_film_border_layout(replace(c.film_border,formation_mode=mode,format_key=key),image.shape)
        key = "135"
        image = np.rot90(image)
    c.film_border = replace(c.film_border,formation_mode=mode,format_key=key,loading_offset=.14)
    layout = resolve_film_border_layout(c.film_border,image.shape)
    assert layout.image_rect[0]>0 and layout.image_rect[1]==0
    direct = develop_negative(image,c,np.random.default_rng(17))
    latent = expose_image_to_latent(image,c,np.random.default_rng(17))
    assert latent.film_border_snapshot["transport_axis"]=="vertical"
    frozen = develop_negative_from_latent(latent,replace(c,film_border=replace(c.film_border,enabled=False)),np.random.default_rng(17))
    np.testing.assert_allclose(direct.optical_density_rgb,frozen.optical_density_rgb,atol=1e-6)
    h,w = frozen.frame_shape[:2]
    assert (w,h)==layout.canvas_size
    assert image_gate_rows(frozen)==(0,h,layout.image_rect[0],w-layout.image_rect[0])
    if mode=="sprocket_panorama":
        assert (h,w)==image.shape[:2]
    else:
        assert h==image.shape[0] and w>image.shape[1]
    c.scanner.transmission_light_temperature_k=3200
    c.scanner.transmission_light_ev=-1.5
    lamp = linear_to_srgb(capture_optical_density(np.zeros((1,1,3),np.float32),c.scanner,
                         illuminant_rgb=transmission_illuminant_rgb(c.scanner)))[0,0]
    scan = scan_medium_output(frozen,c).output_srgb
    for x0,y0,x1,y1 in frozen.metadata["formed_film_rebate"]["perforation_windows_v1"]:
        x=int((x0+x1)*w/2)
        y=int(((.5*(y0+y1)+.14)%1)*h)
        np.testing.assert_allclose(scan[y,x],lamp,atol=1e-6)
    if mode!="attached":
        c.processing.material_tile_threshold_megapixels=.001
        c.processing.material_tile_rows=9
        tiled = develop_negative(image,c,np.random.default_rng(17))
        np.testing.assert_allclose(tiled.optical_density_rgb,direct.optical_density_rgb,atol=2e-6)


@pytest.mark.parametrize("mode",["attached","native"])
def test_two_half_frames_reconstruct_the_eight_perforation_plate(mode):
    from dataclasses import replace
    from film_foundry.core.film_border import resolve_film_border_layout
    from film_foundry.core.formed_film_border import _strip_masks
    from film_foundry.app.workspace_jobs import numbered_photo_config
    c=recipe()
    h,w=192,144
    full=replace(c.film_border,formation_mode=mode,frame_number=12,stock_label="SILVER SERIES",
                 factory_barcode="code128c",factory_mark_type="laser",factory_exposure_variation=.25,
                 factory_composition={"font":"rounded","top":[["text",4,79],["number",84,98]],
                                      "bottom":[["barcode",17,89]]})
    def masks(border,width):
        layout=resolve_film_border_layout(border,(h,width),strip_coordinates=True)
        return _strip_masks(border,layout,full.stock_label)
    for offset in (0.,.25,-.25):
        reference=masks(replace(full,loading_offset=offset/2),w*2)
        first,second=[masks(replace(full,format_key="half_frame",frame_number=i,loading_offset=offset),w)
                      for i in (23,24)]
        for a,b,complete in zip(first,second,reference):
            np.testing.assert_array_equal(np.concatenate((a,b),axis=1),complete)
    c.film_border=replace(full,format_key="half_frame",frame_number=None,factory_barcode="none")
    assert numbered_photo_config(c,2).film_border.strip_frame_index==2
