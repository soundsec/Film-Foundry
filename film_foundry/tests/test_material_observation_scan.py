"""Shared observation controls, ordinary image exports and retained source contracts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import json
from time import monotonic
import numpy as np
import pytest
from PIL import Image
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.core.material_family import ViewingConditions


def paper():
    session=MaterialLabSession('paper')
    ramp=np.linspace(.01,.9,24*36,dtype=np.float32).reshape(24,36)
    session.image=np.stack((ramp,ramp*.8,ramp*.6),axis=-1)
    session.run_standard()
    return session


def test_observation_formats_and_native_16bit_input(tmp_path):
    from film_foundry.app.material_image_io import load_exposure_image
    from film_foundry.core.io_utils import cv_imread_unicode,cv_imwrite_unicode
    from film_foundry.core.color import srgb_to_linear
    session=paper();state=session.state
    for suffix,depth,quality in (('png',8,100),('png',16,100),('tif',8,100),('tif',16,100),('jpg',8,40),('jpg',8,70),('jpeg',8,100)):
        path=tmp_path/f'image-{depth}-{quality}.{suffix}'
        session.export_image(path,bit_depth=depth,quality=quality)
        decoded=cv_imread_unicode(path)
        assert decoded.dtype==(np.uint16 if depth==16 else np.uint8)
        linear,record=load_exposure_image(path,0)
        assert linear.shape==(24,36,3) and record['source_size']==[36,24] and np.isfinite(linear).all()
        metadata=json.loads(path.with_suffix(path.suffix+'.json').read_text())['FilmFoundryObservation']
        if suffix in ('jpg','jpeg'):assert metadata['quality']==quality and metadata['compression']=='jpeg_lossy'
    with pytest.raises(ValueError):session.export_image(tmp_path/'invalid.jpg',bit_depth=16)
    assert session.state is state
    a=np.repeat(np.linspace(10000,12000,1024,dtype=np.uint16).reshape(32,32,1),3,axis=-1)
    for suffix in ('png','tif'):
        path=tmp_path/('native16.'+suffix);assert cv_imwrite_unicode(path,a)
        linear,_=load_exposure_image(path,0)
        np.testing.assert_allclose(linear,srgb_to_linear(a.astype(np.float32)/65535),atol=1e-7)
        assert len(np.unique(linear[...,0]))==1024
    rgba=np.zeros((12,16,4),np.uint8);path=tmp_path/'transparent.png';Image.fromarray(rgba).save(path)
    linear,_=load_exposure_image(path,0);np.testing.assert_array_equal(linear,1)
    import tifffile
    path=tmp_path/'hdr_float.tif';tifffile.imwrite(path,np.full((4,5,3),2.,np.float32),photometric='rgb')
    with pytest.raises(ValueError,match='HDR float TIFF'):load_exposure_image(path,0)


def test_shared_scan_is_readonly_and_persists_without_rewriting_medium(tmp_path):
    from film_foundry.app import material_families as api
    from film_foundry.app.material_scan import normalize_adjustments
    from film_foundry.app.workspace_jobs import archive_material_result
    from film_foundry.app.material_result_store import load_result
    from film_foundry.app.material_workspace import snapshot_document,save_material_workspace,load_material_workspace
    from film_foundry.core.color import linear_to_srgb
    session=paper();session.storage_precision='full';view=ViewingConditions();state=session.state;group=session.group
    np.testing.assert_array_equal(session.preview(),linear_to_srgb(api.observe(group.bodies[0],view)))
    record=archive_material_result(session,tmp_path/'results',view);path=Path(record['medium']);stamp=path.stat().st_mtime_ns
    session.scan_adjustments=normalize_adjustments({'scan_saturation':.2,'print_color_bias':(1.1,.9,1.),
        'scan_normalize':True,'scan_normalize_strength':.6,'tone_curve':((0,0),(.5,.6),(1,1))})
    changed=session.preview();assert session.state is state and session.group is group
    assert not np.allclose(changed,linear_to_srgb(api.observe(group.bodies[0],view)))
    with pytest.raises(ValueError):normalize_adjustments({'invert_transmission':True})
    record=archive_material_result(session,tmp_path/'results',view)
    assert path.stat().st_mtime_ns==stamp
    restored,_,_=load_result(record['artifacts']['result'])
    np.testing.assert_array_equal(restored.preview(),changed)
    document=snapshot_document(session,{'notes':'settings owned by application'})
    save_material_workspace(tmp_path/'workspace.ffworkspace',[document])
    reopened=load_material_workspace(tmp_path/'workspace.ffworkspace').documents[0].session
    np.testing.assert_array_equal(reopened.preview(),changed)


def test_border_does_not_bias_auto_endpoints():
    from film_foundry.app.material_scan import render_material_scan
    a=np.repeat(np.linspace(.1,.6,120,dtype=np.float32).reshape(10,12,1),3,axis=-1)
    settings={'scan_normalize':True,'scan_normalize_strength':1.}
    plain=render_material_scan(a.copy(),settings)
    framed=np.pad(a,((2,4),(2,2),(0,0)),constant_values=.94)
    framed=render_material_scan(framed,settings,border=(2,4,2,2))
    np.testing.assert_array_equal(framed[2:-4,2:-2],plain)


def test_batch_freezes_and_reuses_observation_settings(tmp_path):
    from dataclasses import asdict
    from film_foundry.app.material_batch_run import create_batch,run_batch
    from film_foundry.app.material_result_store import load_result
    source=tmp_path/'source.png';Image.fromarray(np.full((16,24,3),140,np.uint8)).save(source)
    session=MaterialLabSession('paper');definition=session.material_type()
    settings={'scan_saturation':.3}
    manifest=create_batch('paper',[source],tmp_path/'batch',asdict(definition),session.module.default_operations(definition),ViewingConditions(),scan_adjustments=settings)
    settings['scan_saturation']=1.8
    records=[];report=run_batch(manifest,on_result=records.append)
    assert report['completed']==1 and not report['failures']
    restored,_,_=load_result(records[0]['artifacts']['result'])
    assert restored.scan_adjustments=={'scan_saturation':.3}
    path=Path(records[0]['medium']);stamp=path.stat().st_mtime_ns
    report=run_batch(manifest)
    assert report['reused']==1 and path.stat().st_mtime_ns==stamp


def test_gui_material_choice_shared_editor_and_jpeg_without_implicit_archive(tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application,QtWidgets,QtCore
    for key in ('CATALOG','CAMERA_SETTINGS','UI_SETTINGS'):monkeypatch.setenv('FILM_FOUNDRY_'+key,str(tmp_path/(key+'.json')))
    from film_foundry.ui.preferences import _read
    from film_foundry.ui.i18n import set_language
    _read.cache_clear();set_language('zh_CN')
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    def wait(predicate):
        until=monotonic()+10
        while not predicate() and monotonic()<until:_application().processEvents()
        assert predicate()
    panel=DesktopPanel();panel.auto_preview.set(False);panel.root.qt.show()
    try:
        source=tmp_path/'source.png';Image.fromarray(np.full((24,36,3),160,np.uint8)).save(source)
        panel.open_library();library=panel.library_view;library.catalog.add([source]);library.refresh();library.grid.setCurrentRow(0)
        library.family.setCurrentIndex(library.family.findData('paper'))
        assert '相纸' in library.start.text()
        library.open_selected();page=panel.workspace_host.currentWidget();wait(lambda:not page.busy)
        assert page.kind=='paper' and page.session.image.shape==(24,36,3)
        page.session.run_standard();page._sync();state=page.session.state
        page.open_scan_controls();dialog=page.centralWidget()._inline_editor_host.frames[-1][1]
        dialog.controls['scan_saturation'].setValue(.35);dialog.accept();wait(lambda:not page.busy)
        assert page.session.scan_adjustments['scan_saturation']==.35 and page.session.state is state
        def forbidden(*a,**k):raise AssertionError('Image export must not save a material bundle')
        monkeypatch.setattr('film_foundry.app.workspace_jobs.archive_material_result',forbidden)
        target=tmp_path/'photo.jpg'
        page.save_dialog('png');dialog=page._export_dialog
        assert dialog.info['working_size']==[36,24] and dialog.info['quality']=='native_full'
        dialog.format.setCurrentIndex(dialog.format.findText('JPEG'));dialog.quality.setCurrentIndex(1)
        dialog.path.setText(str(target));dialog.apply();wait(lambda:not page.busy)
        assert page.last_error is None and target.is_file()
        assert not list(tmp_path.rglob('*.npz'))
        assert json.loads(target.with_suffix('.jpg.json').read_text())['FilmFoundryObservation']['quality']==70
        from film_foundry.app.desktop_catalog import DesktopCatalog
        assert any(a['path']==str(target.resolve()) for a in DesktopCatalog().query(collection='assets',kind='scan'))
    finally:
        wait(lambda:not panel.workspace_host.busy_pages());panel.root.qt.close();_application().sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
