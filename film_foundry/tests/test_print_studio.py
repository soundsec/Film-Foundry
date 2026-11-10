"""Targeted print semantics, portable plates and family/UI integration."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from dataclasses import replace
from time import monotonic
import numpy as np
import pytest
import tifffile
from PIL import Image, ImageCms
from film_foundry.core.printmaking import PlateStyle, screen
from film_foundry.app.printmaking import PrintmakingSession
from film_foundry.app.print_export import export_print


def test_screens_preserve_endpoints_and_register_without_wrap():
    for pattern in ('dots','lines','grid','continuous'):
        style=PlateStyle(pattern=pattern,angle=0,spacing_mm=2.54)
        assert not screen(np.zeros((96,128)),style).any()
        np.testing.assert_array_equal(screen(np.ones((96,128)),style),1)
        level=screen(np.full((300,300),.25),style)
        assert abs(float(level.mean())-.25)<.035
    plate=np.zeros((60,80));plate[:,-8:]=1
    moved=screen(plate,PlateStyle(pattern='continuous',offset_x_mm=2.54),100)
    assert not moved.any()  # right edge leaves the frame, never wraps to the left


def test_print_tiff_metadata_cmyk_and_source_protection(tmp_path):
    image=np.linspace(0,1,24*32*3,dtype=np.float32).reshape(24,32,3)
    target=tmp_path/'rgb.tif';record=export_print(image,target,dpi=254)
    with tifffile.TiffFile(target) as f:
        page=f.pages[0]
        assert page.dtype==np.uint16 and page.shape==image.shape
        assert page.tags['XResolution'].value==(254,1) and page.tags[34675].value
    assert record['size_mm']==pytest.approx([3.2,2.4])
    with pytest.raises(ValueError):export_print(image,target,protected_paths=(target,))
    wrong=tmp_path/'rgb.icc';wrong.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
    with pytest.raises(ValueError):export_print(image,tmp_path/'wrong.tif',cmyk_profile=wrong)
    profile=Path('C:/Windows/System32/spool/drivers/color/CoatedFOGRA39.icc')
    if profile.exists():
        cmyk=tmp_path/'cmyk.tif';export_print(image,cmyk,cmyk_profile=profile)
        with Image.open(cmyk) as f:
            assert f.mode=='CMYK' and f.size==(32,24) and f.info['icc_profile']==profile.read_bytes()
            assert f.info['dpi']==(300,300)


def test_portable_plate_project_and_distinct_catalog_results(tmp_path,monkeypatch):
    from film_foundry.app.print_sources import print_result_record
    from film_foundry.app.desktop_catalog import DesktopCatalog
    monkeypatch.setenv('FILM_FOUNDRY_CATALOG',str(tmp_path/'catalog.json'))
    session=PrintmakingSession();session.set_image(np.full((48,64,3),(.2,.4,.8),np.float32))
    session.styles[0]=replace(session.styles[0],pattern='grid',angle=35,offset_x_mm=.1)
    session.dpi=150
    project=session.save(tmp_path/'plates')
    restored=PrintmakingSession();restored.load(project)
    np.testing.assert_allclose(restored.result,session.result,atol=.0001)
    np.testing.assert_array_equal(restored.styles[0].ink,session.styles[0].ink)
    assert restored.dpi==150 and restored.styles[0].offset_x_mm==.1
    with pytest.raises(ValueError):restored.save(project.parent)
    margin=round(8*150/25.4)
    plate=tifffile.imread(project.parent/'plate_01.tif')
    assert plate.shape==(48+2*margin,64+2*margin)
    assert plate[margin//2,margin//2]==0 and plate[0,0]==65535
    catalog=DesktopCatalog()
    for path in (project.parent/'composite.tif',project.parent/'source.tif'):
        catalog.register_result(print_result_record(path,{}))
    assert len(catalog.results)==2
    assert len(catalog.query(collection='assets',kind='scan'))==2


@pytest.mark.parametrize('kind',('peel','integral','plate','paper','wet','cyanotype','platinum'))
def test_family_target_formation_reused_and_body_observation_readonly(kind,tmp_path,monkeypatch):
    from film_foundry.app.family_preview import FamilyPreviewSession
    from film_foundry.app.print_sources import load_print_source
    from film_foundry.app.diagnostic_targets import diagnostic_target
    import film_foundry.app.family_preview as module
    monkeypatch.setattr(module,'diagnostic_target',lambda target:diagnostic_target(target,long_edge=96))
    renderer=FamilyPreviewSession(kind)
    source,result,reused=renderer.render_target('color24',{})
    assert not reused and result.shape==source.shape and np.isfinite(result).all()
    assert renderer.session.group is not None
    state=renderer.session.state
    _,brighter,reused=renderer.render_target('color24',{'view':{'exposure_ev':.5}})
    assert reused and renderer.session.state is state
    assert np.mean(brighter)>=np.mean(result)
    path=tmp_path/(kind+'.npz');renderer.session.save_products(path);stamp=path.stat().st_mtime_ns
    body=renderer.session.group.bodies[0]
    payload=load_print_source({'path':str(path),'kind':'material','family':kind,'body_id':body.body_id})
    np.testing.assert_array_equal(payload['image'],result)
    assert path.stat().st_mtime_ns==stamp and payload['provenance']['body_id']==body.body_id
    if kind=='peel':
        from film_foundry.core.material_family import ViewingConditions
        framed=load_print_source({'path':str(path),'kind':'material','family':kind,'body_id':body.body_id},view=ViewingConditions(include_border=True,border_style='pod_short'))
        assert framed['image'].shape[1]>result.shape[1]
        assert all(a.shape==framed['image'].shape[:2] for a in framed['components'].values())
        assert all(not a[0].any() for a in framed['components'].values())


def wait(predicate):
    from film_foundry.ui.qt import _application
    until=monotonic()+15
    while not predicate() and monotonic()<until:_application().processEvents()
    assert predicate()


def test_film_print_uses_scan_pipeline_and_actual_density_components(tmp_path):
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.app.print_sources import load_print_source
    from film_foundry.model.config import DarkroomConfig
    cfg=DarkroomConfig();cfg.enable_grain=False;cfg.enable_mtf=False;cfg.enable_halation=False
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    path=tmp_path/'source.png';Image.fromarray(np.full((24,36,3),145,np.uint8)).save(path)
    result=run_request(WorkflowRequest('full',path,tmp_path/'scan.png',cfg))
    stamp=result.medium_path.stat().st_mtime_ns
    payload=load_print_source({'path':str(result.medium_path),'kind':'medium','family':'film'},film_config=cfg)
    scan=run_request(WorkflowRequest('scan',result.medium_path,tmp_path/'unused',cfg))
    np.testing.assert_array_equal(payload['image'],scan.extra['scanned'].output_srgb)
    assert payload['image'].shape==(24,36,3) and payload['components']
    assert result.medium_path.stat().st_mtime_ns==stamp


def test_material_definition_roundtrip_and_frozen_state(tmp_path,monkeypatch):
    from film_foundry.ui.material_lab_window import create_material_window
    from film_foundry.ui.qt import QtWidgets
    from film_foundry.core.atomic_io import strict_json_load,atomic_write_json
    path=tmp_path/'material.json'
    monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *_:(str(path),''))
    monkeypatch.setattr(QtWidgets.QFileDialog,'getOpenFileName',lambda *_:(str(path),''))
    page=create_material_window('peel')
    try:
        control=page.material_controls['batch_variation'];control.setValue(.23)
        page.save_definition();control.setValue(.7);page.load_definition()
        assert control.value()==.23
        bad=strict_json_load(path);bad['material']['maximum_density']=1e6;atomic_write_json(path,bad)
        prior=page.material_values();page.load_definition();assert page.material_values()==prior
        page.session.image=np.full((24,36,3),.18,np.float32);page.session.process('expose',material=prior)
        page._sync();assert not page.load_definition_action.isEnabled()
        page.load_definition();assert page.material_values()==prior
    finally:page.close()


def test_single_window_preview_print_and_plate_reload(tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application,QtCore,QtWidgets
    for key in ('CATALOG','CAMERA_SETTINGS','UI_SETTINGS'):monkeypatch.setenv('FILM_FOUNDRY_'+key,str(tmp_path/(key+'.json')))
    from film_foundry.ui.preferences import _read
    _read.cache_clear()
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    panel=DesktopPanel();panel.auto_preview.set(False);panel.root.qt.show()
    try:
        for kind in ('peel','integral','plate','paper','wet','cyanotype','platinum'):
            page=panel._show_material_workspace(kind)
            assert page.load_definition_action.isEnabled() and not page.print_action.isEnabled()
        page=panel._material_windows['cyanotype'];panel.workspace_host.setCurrentWidget(page)
        page.open_target_preview();preview=page._target_preview;preview.request()
        wait(lambda:not preview.busy and preview.frames is not None)
        assert not preview.isWindow()
        page.centralWidget()._inline_editor_host.frames[-1][1].reject()
        from film_foundry.app.family_preview import FamilyPreviewSession
        reference=FamilyPreviewSession('cyanotype');reference.render_target('color24',{})
        page.session=reference.session;page._sync();page.send_to_print()
        wait(lambda:not page.busy)
        assert page.last_error is None
        studio=panel.workspace_host.currentWidget()
        assert studio.__class__.__name__=='PrintmakingWorkspace' and not studio.isWindow()
        assert studio.session.components
        studio.mode.setCurrentIndex(1);wait(lambda:not studio.busy)
        studio.pattern.setCurrentIndex(studio.pattern.findData('lines'));studio.numbers['angle'].setValue(37)
        studio.dpi.setValue(150);studio.render();wait(lambda:not studio.busy)
        assert studio.session.styles[0].angle==37
        output=tmp_path/'print.tif'
        monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *_:(str(output),'RGB 16-bit TIFF (*.tif)'))
        studio.export_dialog();wait(lambda:not studio.busy)
        assert tifffile.imread(output).dtype==np.uint16
        folder=tmp_path/'ui-plates'
        monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *_:(str(folder),'Folder (*)'))
        studio.save_project();wait(lambda:not studio.busy)
        project=folder/'project.json';assert project.is_file()
        from film_foundry.app.desktop_catalog import DesktopCatalog
        assert len(DesktopCatalog().query(collection='assets',kind='scan'))==2
        monkeypatch.setattr(QtWidgets.QFileDialog,'getOpenFileName',lambda *_:(str(project),''))
        studio.dpi.setValue(300);studio.load_project();wait(lambda:not studio.busy)
        assert studio.dpi.value()==150 and studio.mode.currentIndex()==1 and studio.numbers['angle'].value()==37
        assert [w for w in _application().topLevelWidgets() if w.isVisible()]==[panel.root.qt]
    finally:
        wait(lambda:not panel.workspace_host.busy_pages())
        panel.root.qt.close();_application().sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
