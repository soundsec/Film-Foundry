from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from film_foundry.core.source_image import load_formation_image, exposure_preview


def write_dng(path):
    tifffile=pytest.importorskip('tifffile')
    # A real TIFF/DNG CFA fixture, decoded by LibRaw (not a mocked decoder).
    x=np.tile(np.linspace(1024,13000,96,dtype=np.uint16),(64,1))
    tags=[(50706,'B',4,(1,4,0,0),False),(50707,'B',4,(1,1,0,0),False),
    (50708,'s',0,'Film Foundry Synthetic CFA',False),(33421,'H',2,(2,2),False),
    (33422,'B',4,(0,1,1,2),False),(50710,'B',3,(0,1,2),False),(50711,'H',1,1,False),
    (50714,'H',1,512,False),(50717,'I',1,16383,False),
    (50721,'2i',9,(10000,10000,0,10000,0,10000,0,10000,10000,10000,0,10000,0,10000,0,10000,10000,10000),False),
    (50728,'2I',3,(1,1,1,1,1,1),False),(50778,'H',1,21,False)]
    tifffile.imwrite(path,x,photometric=32803,metadata=None,extratags=tags)


def test_dng_real_decode_and_dimensions(tmp_path):
    from film_foundry.core.io_utils import probe_image_dimensions, iter_images
    path=tmp_path/'sensor.dng';write_dng(path)
    pixels,color=load_formation_image(path)
    assert pixels.shape==(64,96,3) and pixels.dtype==np.float32
    assert color['encoding']=='linear_rgb' and color['auto_bright'] is False
    assert probe_image_dimensions(path)==(96,64)
    assert path in list(iter_images(tmp_path))
    assert pixels[:,20].mean()<pixels[:,70].mean()


@pytest.mark.parametrize('gamut',['BT_709','DISPLAY_P3','BT_2100'])
def test_real_gainmap_headroom_and_gamut(tmp_path,gamut):
    codec=pytest.importorskip('imagecodecs')
    source=np.ones((64,96,4),np.float16)
    source[:,:32,:3]=.2;source[:,32:64,:3]=2;source[:,64:,:3]=8
    path=tmp_path/'hdr.jpg'
    path.write_bytes(codec.ultrahdr_encode(source,gamut=getattr(codec.ULTRAHDR.CG,gamut)))
    pixels,color=load_formation_image(path)
    assert color['gain_map_applied'] and color['encoding']=='linear_rgb'
    assert color['source_gamut']=={'BT_709':'bt709','DISPLAY_P3':'display_p3','BT_2100':'bt2020'}[gamut]
    assert pixels[:,70:90].mean()>6 and pixels[:,40:50].mean()>1.5
    original=pixels.copy();preview=exposure_preview(pixels,'linear_rgb')
    assert preview.max()<=1
    np.testing.assert_array_equal(pixels,original)


def test_linear_exposure_uses_common_formation_and_preserves_headroom():
    from film_foundry.core.engine import expose_linear_to_latent,develop_negative
    from film_foundry.core.color import srgb_to_linear
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.core.mtf import apply_emulsion_mtf
    from film_foundry.core.halation import apply_halation
    c=DarkroomConfig();c.enable_grain=False;c.enable_halation=False
    x=np.linspace(.05,.8,32*48*3,dtype=np.float32).reshape(32,48,3)
    a=develop_negative(x,c,rng=np.random.default_rng(3))
    b=develop_negative(srgb_to_linear(x),c,rng=np.random.default_rng(3),input_encoding='linear_rgb')
    np.testing.assert_array_equal(a.optical_density_rgb,b.optical_density_rgb)
    bright=np.full((32,48,3),8,np.float32)
    assert apply_emulsion_mtf(bright,c.film,exposure_ceiling=None).max()>7
    c.film.halation_strength=.2
    assert apply_halation(bright,c.film,exposure_ceiling=None).max()>7
    high=expose_linear_to_latent(bright,c,rng=np.random.default_rng(3))
    low=expose_linear_to_latent(bright/2,c,rng=np.random.default_rng(3))
    assert np.any(high.developability>low.developability)


def test_expose_file_keeps_capture_record(tmp_path):
    from film_foundry.app.workflow import expose_image_file
    from film_foundry.core.latent_io import load_exposed_latent
    from film_foundry.model.config import DarkroomConfig
    path=tmp_path/'sensor.dng';write_dng(path)
    latent=expose_image_file(path,tmp_path/'test.darkroom_latent.npz',DarkroomConfig())
    state,metadata=load_exposed_latent(latent)
    assert metadata['input_color']['source']=='dng'
    assert state.developability.shape==(64,96,3)


def test_edge_cpu_private_buffers_match_reference(monkeypatch):
    from film_foundry.tests.test_edge_wetting import event
    from film_foundry.core.edge_wetting import contact_duration
    from film_foundry.core.execution_topology import execution_topology
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    for window in [(0,1),(.23,.79)]:
        with execution_topology('reference'):
            a=contact_duration((131,211),(36.,24.),0,131,120.,event(),window)
        b=contact_duration((131,211),(36.,24.),0,131,120.,event(),window)
        np.testing.assert_array_equal(a,b)


@pytest.mark.parametrize('kind',['dng','hdr'])
def test_capture_file_full_pipeline_and_preview(tmp_path,kind):
    from film_foundry.core.engine import process_file,develop_negative
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.app.preview_session import PreviewSession
    from film_foundry.core.negative_io import load_developed_negative_npz
    path=tmp_path/('input.dng' if kind=='dng' else 'input.jpg')
    if kind=='dng':write_dng(path)
    else:
        codec=pytest.importorskip('imagecodecs')
        pixels=np.ones((64,96,4),np.float16);pixels[:,48:,:3]=8
        path.write_bytes(codec.ultrahdr_encode(pixels,gamut=codec.ULTRAHDR.CG.BT_709))
    c=DarkroomConfig();c.seed_strategy='fixed';c.random_seed=17
    c.enable_grain=False;c.output.save_scanner_raw=False
    pixels,color=load_formation_image(path)
    expected=develop_negative(pixels,c,rng=np.random.default_rng(17),input_encoding=color['encoding'])
    artifacts={};process_file(path,tmp_path/'render.png',c,artifact_paths=artifacts,resolved_seed_override=17)
    medium_path=next(p for p in tmp_path.rglob('*.darkroom_negative.npz'))
    actual=load_developed_negative_npz(medium_path)
    np.testing.assert_array_equal(expected.optical_density_rgb,actual.optical_density_rgb)
    assert actual.metadata['input_color']['source']==color['source']
    preview=PreviewSession();left,right,hit=preview.render(path,c,long_edge=96)
    assert left.max()<=1 and not hit
    np.testing.assert_array_equal(preview._medium.optical_density_rgb,expected.optical_density_rgb)
    _,again,hit=preview.render(path,c,long_edge=96)
    assert hit
    np.testing.assert_array_equal(right,again)


def test_dng_desktop_catalog_and_thumbnail(tmp_path):
    from film_foundry.ui.qt import QtWidgets
    from film_foundry.ui.desktop_library import thumbnail
    from film_foundry.app.desktop_catalog import IMAGE_SUFFIXES
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path=tmp_path/'input.dng';write_dng(path)
    assert '.dng' in IMAGE_SUFFIXES and not thumbnail(str(path)).isNull()


def test_hdr_orientation_and_invalid_gainmap_are_explicit(tmp_path):
    import struct
    codec=pytest.importorskip('imagecodecs')
    from film_foundry.core.io_utils import probe_image_dimensions
    pixels=np.ones((64,96,4),np.float16);pixels[:,:48,:3]=8
    data=codec.ultrahdr_encode(pixels,gamut=codec.ULTRAHDR.CG.BT_709)
    exif=Image.Exif();exif[274]=6;payload=exif.tobytes()
    oriented=data[:2]+b'\xff\xe1'+struct.pack('>H',len(payload)+2)+payload+data[2:]
    path=tmp_path/'oriented.jpg';path.write_bytes(oriented)
    image,_=load_formation_image(path)
    assert image.shape==(96,64,3) and probe_image_dimensions(path)==(64,96)
    assert image[:32].mean()>image[-32:].mean()*3
    bad=tmp_path/'bad.jpg';Image.new('RGB',(8,8)).save(bad)
    data=bad.read_bytes();payload=b'http://ns.adobe.com/xap/1.0/\x00hdr-gain-map'
    bad.write_bytes(data[:2]+b'\xff\xe1'+struct.pack('>H',len(payload)+2)+payload+data[2:])
    with pytest.raises(ValueError,match='gain-map'):load_formation_image(bad)
    with pytest.raises(ValueError,match='Only DNG'):load_formation_image(tmp_path/'unsupported.cr3')
