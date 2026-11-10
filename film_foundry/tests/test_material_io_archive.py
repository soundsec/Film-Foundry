from dataclasses import replace
import json
from pathlib import Path
import numpy as np
from PIL import Image
import pytest
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.app import material_image_io,material_families
from film_foundry.app.workspace_jobs import archive_material_result
from film_foundry.core import io_utils
from film_foundry.core.color import srgb_to_linear
from film_foundry.core.preview import resize_to_long_edge
from film_foundry.core.material_family import ViewingConditions


def source16(tmp_path):
    path=tmp_path/'source.png'
    ramp=np.broadcast_to(np.linspace(100,65000,1024,dtype=np.uint16)[None,:,None],(8,1024,3)).copy()
    assert io_utils.cv_imwrite_unicode(path,ramp)
    return path


def formed(tmp_path):
    session=MaterialLabSession('paper');session.open_image(source16(tmp_path),0);session.run_standard(seed=3)
    return session


def test_input_precision_linear_resize_and_frozen_source_contract(tmp_path):
    path=source16(tmp_path);display,color=io_utils.load_image_with_color(path)
    session=MaterialLabSession('paper');session.open_image(path,0)
    np.testing.assert_array_equal(session.image,srgb_to_linear(display))
    assert np.unique(session.image[...,0]).size==1024
    assert session.source_info['color']==color and color['bit_depth']==16
    session.open_image(path,512)
    np.testing.assert_array_equal(session.image,resize_to_long_edge(srgb_to_linear(display),512))
    session.run_standard();before=session.source_info.copy()
    archive=tmp_path/'result.ffmaterial.npz';session.save_products(archive)
    reopened=MaterialLabSession('paper');reopened.load(archive)
    assert reopened.source_info==before
    assert before['source_size']==[1024,8] and before['working_size']==[512,4]


def test_profile_and_orientation_share_main_input_path(tmp_path):
    from film_foundry.tests.test_io_utils import _display_p3_icc_bytes
    path=tmp_path/'p3.png';pixels=np.zeros((4,6,3),np.uint8);pixels[:]=[180,90,30]
    exif=Image.Exif();exif[274]=6
    Image.fromarray(pixels).save(path,icc_profile=_display_p3_icc_bytes(),exif=exif)
    expected,color=io_utils.load_image_with_color(path)
    session=MaterialLabSession('cyanotype');session.open_image(path,0)
    assert session.image.shape==(6,4,3)
    np.testing.assert_array_equal(session.image,srgb_to_linear(expected))
    assert session.source_info['color']==color and color['source']!='assumed_srgb'
    assert not np.allclose(expected[0,0],pixels[0,0]/255,atol=.005)


@pytest.mark.parametrize('depth,suffix',[(8,'.png'),(16,'.png'),(16,'.tif')])
def test_observation_depth_size_metadata_and_immutable_medium(tmp_path,depth,suffix):
    session=formed(tmp_path);state=session.state;group=session.group
    view=ViewingConditions(exposure_ev=.3);expected=session.preview(view)
    path=tmp_path/('export'+suffix);session.export_image(path,view,bit_depth=depth)
    actual=io_utils.cv_imread_unicode(path)[...,::-1]
    scaled=np.clip(expected,0,1)*(255 if depth==8 else 65535)
    quantized=(scaled+.5).astype(np.uint8) if depth==8 else np.rint(scaled).astype(np.uint16)
    np.testing.assert_array_equal(actual,quantized)
    assert actual.dtype==(np.uint8 if depth==8 else np.uint16)
    record=json.loads(path.with_suffix(suffix+'.json').read_text())['FilmFoundryObservation']
    assert record['encoding']==f'display_srgb_{depth}bit' and record['output_size']==[1024,8]
    assert record['medium_size']==[1024,8] and record['source_input']['color']['bit_depth']==16
    assert session.state is state and session.group is group
    if depth==8:
        with Image.open(path) as image:assert json.loads(image.info['FilmFoundryObservation'])==record


@pytest.mark.parametrize('depth,suffix',[(8,'.png'),(16,'.tif')])
def test_export_pair_rollback_and_source_protection(tmp_path,monkeypatch,depth,suffix):
    session=formed(tmp_path);source=Path(session.source_path);before=source.read_bytes()
    with pytest.raises(ValueError,match='source'):session.export_image(source,bit_depth=depth)
    assert source.read_bytes()==before
    path=tmp_path/('previous'+suffix);sidecar=path.with_suffix(suffix+'.json')
    path.write_bytes(b'previous image');sidecar.write_bytes(b'previous metadata')
    def fail(*args,**kwargs):raise OSError('metadata write failed')
    monkeypatch.setattr(material_image_io if depth==8 else io_utils,'atomic_write_json',fail)
    with pytest.raises(OSError,match='metadata'):session.export_image(path,bit_depth=depth)
    assert path.read_bytes()==b'previous image' and sidecar.read_bytes()==b'previous metadata'


def test_repeated_saves_share_formation_but_views_and_new_processing_update(tmp_path,monkeypatch):
    session=formed(tmp_path);root=tmp_path/'library';calls=[];renders=[]
    save=material_families.save;export=session.export_png
    def tracked(path,value):calls.append(path);return save(path,value)
    def rendered(*args):renders.append(args[0]);return export(*args)
    monkeypatch.setattr(material_families,'save',tracked);monkeypatch.setattr(session,'export_png',rendered)
    view=ViewingConditions();first=archive_material_result(session,root,view);count=len(calls)
    same=archive_material_result(session,root,view)
    assert first==same and count>0 and len(calls)==count and len(renders)==1
    updated=archive_material_result(session,root,replace(view,exposure_ev=1))
    assert updated['medium']==first['medium'] and len(calls)==count and len(renders)==2
    session.process('wash',{'seconds':10})
    new=archive_material_result(session,root,view)
    assert new['medium']!=first['medium'] and len(calls)>count


def test_failed_archive_is_not_cached_and_result_exports_remain_indexed(tmp_path,monkeypatch):
    session=formed(tmp_path);original=session.save_products
    def fail(*args):raise OSError('archive unavailable')
    monkeypatch.setattr(session,'save_products',fail)
    with pytest.raises(OSError):archive_material_result(session,tmp_path/'library',ViewingConditions())
    assert getattr(session,'_result_archive_cache',None) is None
    monkeypatch.setattr(session,'save_products',original)
    record=archive_material_result(session,tmp_path/'library',ViewingConditions())
    from film_foundry.app.desktop_catalog import DesktopCatalog
    catalog=DesktopCatalog(tmp_path/'catalog.json')
    first={**record,'artifacts':{**record['artifacts'],'export:a.png':'a.png'}}
    second={**record,'artifacts':{**record['artifacts'],'export:b.tif':'b.tif'}}
    catalog.register_result(first);catalog.register_result(second)
    assert len(catalog.results)==1 and len(catalog.results[0]['runs'])==1
    assert {'export:a.png','export:b.tif'}<=set(catalog.results[0]['runs'][0]['artifacts'])


def test_cli_route_observation_supports_rgb16(tmp_path):
    from film_foundry.app.cli import main
    session=formed(tmp_path);medium=tmp_path/'medium.ffmaterial.npz';session.save_products(medium)
    target=tmp_path/'route.tif'
    assert main(['route',str(medium),str(target),'--start','medium','--stop','image','--bit-depth','16'])==0
    assert io_utils.cv_imread_unicode(target).dtype==np.uint16
    assert json.loads(target.with_suffix('.tif.json').read_text())['FilmFoundryObservation']['encoding']=='display_srgb_16bit'


def test_failed_view_refresh_keeps_previous_archive_generation(tmp_path,monkeypatch):
    session=formed(tmp_path);view=ViewingConditions();root=tmp_path/'library'
    record=archive_material_result(session,root,view)
    image=Path(record['cover']);sidecar=image.with_suffix('.png.json')
    previous=image.read_bytes(),sidecar.read_bytes()
    original=session.export_png
    def fail(*args):original(*args);raise OSError('observation failed after write')
    monkeypatch.setattr(session,'export_png',fail)
    with pytest.raises(OSError):archive_material_result(session,root,replace(view,exposure_ev=1))
    assert (image.read_bytes(),sidecar.read_bytes())==previous
    assert session._result_archive_cache['view']['exposure_ev']==0
    monkeypatch.setattr(session,'export_png',original)
    assert archive_material_result(session,root,view)['medium']==record['medium']


@pytest.mark.parametrize('kind',['paper','peel'])
def test_both_window_types_export_tiff16_without_duplicate_catalog_runs(tmp_path,monkeypatch,kind):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from film_foundry.ui.qt import _application,QtWidgets
    from film_foundry.ui.material_lab_window import create_material_window
    from film_foundry.tests.test_sheet_windows import wait
    from film_foundry.app.desktop_catalog import DesktopCatalog
    monkeypatch.setenv('FILM_FOUNDRY_CATALOG',str(tmp_path/'catalog.json'))
    source=source16(tmp_path);window=create_material_window(kind)
    try:
        window.session.open_image(source,128)
        if kind=='paper':window.session.run_standard()
        else:
            for op in ('expose','spread','advance','peel'):
                window.session.process(op,{'seconds':60} if op=='advance' else {})
        window._sync();catalog=DesktopCatalog();window.result_ready.connect(catalog.register_result)
        for name in ('first','second'):
            path=tmp_path/(name+'.tif')
            monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *a,**kw:(str(path),'TIFF 16-bit (*.tif *.tiff)'))
            window.save_dialog('png');wait(window)
            assert io_utils.cv_imread_unicode(path).dtype==np.uint16
        assert len(catalog.results)==1 and len(catalog.results[0]['runs'])==1
        assert {'export:first.tif','export:second.tif'}<=set(catalog.results[0]['runs'][0]['artifacts'])
    finally:window.close();_application().processEvents()
