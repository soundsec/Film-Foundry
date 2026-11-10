"""Catalogue joins, compound queries and real material routes touched by the UI."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from time import monotonic
import numpy as np
from PIL import Image
import pytest

from film_foundry.app.desktop_catalog import DesktopCatalog
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.app.workspace_jobs import archive_material_result,workflow_result_record
from film_foundry.app.catalog_assets import path_key,asset_actions
from film_foundry.core.material_family import ViewingConditions
from film_foundry.ui.qt import _application


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    for key in ('CATALOG','CAMERA_SETTINGS','UI_SETTINGS'):
        monkeypatch.setenv('FILM_FOUNDRY_'+key,str(tmp_path/(key+'.json')))
    from film_foundry.ui.preferences import _read
    from film_foundry.ui.i18n import set_language
    _read.cache_clear();set_language('zh_CN')


def wait(predicate):
    deadline=monotonic()+8
    while not predicate() and monotonic()<deadline:_application().processEvents()
    assert predicate()


def source(tmp_path):
    path=tmp_path/'scene.png'
    ramp=np.linspace(10,240,32*48*3).reshape(32,48,3).astype(np.uint8)
    Image.fromarray(ramp).save(path)
    return path


def peel_result(tmp_path):
    session=MaterialLabSession('peel');session.open_image(source(tmp_path),0)
    material={'transfer':'silver_compound','donor_mode':'silver_negative'}
    for op in ('expose','spread','advance','peel'):
        session.process(op,{'seconds':90} if op=='advance' else {},material,seed=3)
    session.process('clear_negative',{'strength':.2})
    record=archive_material_result(session,tmp_path/'peel',ViewingConditions())
    return session,record


def test_material_lineage_and_filters_match_the_same_body(tmp_path):
    session,parent=peel_result(tmp_path)
    donor=next(b for b in session.group.bodies if b.role=='silver_negative')
    sheet=MaterialLabSession('cyanotype')
    from film_foundry.contracts.optical_cascade import OpticalRelay
    sheet.open_medium(parent['artifacts']['silver_negative'],body_id=donor.body_id,relay=OpticalRelay(),long_edge=0)
    sheet.run_standard();child=archive_material_result(sheet,tmp_path/'cyan',ViewingConditions())
    catalog=DesktopCatalog();catalog.add([parent['source']])
    catalog.register_result(child);catalog.register_result(parent)  # Child first must still join.
    assert len(catalog.results)==1 and len(catalog.results[0]['runs'])==2
    negative=next(a for a in catalog.assets() if a['role']=='silver_negative')
    observed=next(a for a in catalog.assets() if a['kind']=='scan' and a['family']=='cyanotype')
    catalog.annotate(negative['id'],rating=3,favorite=True)
    catalog.annotate(observed['id'],rating=5,favorite=True)
    assert catalog.query(collection='results',kind='negative',relation='ge',rating=5,favorites=True)==[]
    matches=catalog.query(collection='assets',kind='auxiliary',family='peel',source='scene',relation='eq',rating=3,favorites=True)
    assert [a['id'] for a in matches]==[negative['id']]
    restored=DesktopCatalog();assert restored.query(collection='assets',kind='negative',favorites=True,relation='eq',rating=3)
    assert 'reexpose' in asset_actions(negative)
    assert sheet.optical_body_id==donor.body_id and sheet.optical_source.bodies[0].body_id==donor.body_id


def test_import_exact_members_standalone_groups_and_readonly_annotations(tmp_path):
    session,record=peel_result(tmp_path);stamp=Path(record['medium']).stat().st_mtime_ns
    standalone=tmp_path/'standalone.npz';session.save_products(standalone)
    catalog=DesktopCatalog()
    failures=catalog.import_paths([record['artifacts']['silver_negative'],standalone,tmp_path/'missing.npz'])
    assert len(failures)==1 and len(catalog.results)==2
    group=next(s for s in catalog.results if s['runs'][0]['medium']==str(standalone))
    bodies=[a for a in catalog.assets(group) if a.get('body_id')]
    assert len(bodies)==2 and len({a['id'] for a in bodies})==2
    catalog.annotate(bodies[1]['id'],rating=4,favorite=True)
    assert Path(record['medium']).stat().st_mtime_ns==stamp
    assert not catalog.import_paths([record['artifacts']['receiver:image']])
    assert len(catalog.results)==2  # Import a rendered member as its material bundle.


def test_film_separate_endpoints_and_rescan_join_without_losing_source(tmp_path):
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.core.io_utils import save_image_bundle
    cfg=DarkroomConfig();cfg.enable_grain=False;cfg.enable_mtf=False;cfg.enable_halation=False
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    image=source(tmp_path);catalog=DesktopCatalog();catalog.add([image])
    latent=run_request(WorkflowRequest('expose',image,tmp_path/'frame.darkroom_latent.npz',cfg))
    latent_record=workflow_result_record(image,latent);catalog.register_result(latent_record)
    developed=run_request(WorkflowRequest('develop_latent',latent.output_path,tmp_path/'developed',cfg))
    record=workflow_result_record(latent.output_path,developed);catalog.register_result(record)
    stamp=developed.medium_path.stat().st_mtime_ns
    scanned=run_request(WorkflowRequest('scan',developed.medium_path,tmp_path/'scan',cfg))
    target=tmp_path/'scans'/'scan.png';save_image_bundle(scanned.extra['scanned'].output_srgb,target,cfg.output,None)
    scan_record=workflow_result_record(developed.medium_path,scanned,output_path=target);catalog.register_result(scan_record)
    assert len(catalog.results)==1 and len(catalog.results[0]['runs'])==2
    medium_run=next(r for r in catalog.results[0]['runs'] if r['medium']==str(developed.medium_path.resolve()))
    assert medium_run['source']==str(latent.output_path.resolve())
    assert catalog.query(collection='assets',kind='scan')
    assert developed.medium_path.stat().st_mtime_ns==stamp
    fresh=DesktopCatalog(tmp_path/'reimport.json');assert not fresh.import_paths([target.with_name(target.name+'.result.json')])
    assert fresh.results[0]['runs'][0]['medium']==str(developed.medium_path.resolve())


def test_companion_workbench_recovers_state_and_saves_new_version(tmp_path):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    original,record=peel_result(tmp_path);stamp=Path(record['medium']).stat().st_mtime_ns
    panel=DesktopPanel();panel.auto_preview.set(False);panel.root.qt.show()
    try:
        panel.open_library();catalog=panel.library_view.catalog;catalog.register_result(record)
        donor=next(a for a in catalog.assets() if a['role']=='silver_negative')
        panel._open_result_stack(dict(catalog.results[0],focus_asset=donor['id']))
        stack=panel.workspace_host.currentWidget()
        assert stack.selected_asset()['id']==donor['id'] and stack.rework_button.isEnabled()
        stack.annotate(rating=4);stack.annotate(favorite=True)
        assert catalog.query(collection='assets',kind='auxiliary',rating=4,relation='eq',favorites=True)
        stack.rework();page=panel.workspace_host.currentWidget();wait(lambda:not page.busy)
        assert page.last_error is None and page.session.state is not None
        assert page.body_choice.currentData()[0]==donor['body_id']
        assert page.operation_buttons['clear_negative'].isEnabled()
        assert page.workflow.choice.currentData()=='clear_negative'
        page.clearing_controls['strength'].setValue(.65)
        page.process('clear_negative');wait(lambda:not page.busy)
        assert page.last_error is None and page.session.state.stage=='cleared'
        updated=archive_material_result(page.session,tmp_path/'peel',ViewingConditions());panel._record_result(updated)
        assert updated['medium']!=record['medium'] and updated['parent']['medium']==record['medium']
        assert Path(record['medium']).stat().st_mtime_ns==stamp
        assert len(catalog.results)==1 and len(catalog.results[0]['runs'])==2
        panel.library_view.collection.setCurrentIndex(2)
        panel.library_view.type_filter.setCurrentIndex(panel.library_view.type_filter.findData('auxiliary'))
        panel.library_view.favorites.setChecked(True)
        assert panel.library_view.grid.count()==1
    finally:
        wait(lambda:not panel.workspace_host.busy_pages());panel.root.qt.close()


def test_negative_optical_routes_and_latent_open_are_stage_specific(tmp_path,monkeypatch):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    session,record=peel_result(tmp_path);catalog=DesktopCatalog();catalog.register_result(record)
    donor=next(a for a in catalog.assets() if a['role']=='silver_negative')
    panel=DesktopPanel();panel.auto_preview.set(False);panel.root.qt.show()
    monkeypatch.setattr(panel,'_start_preview',lambda:None)
    try:
        for family in ('paper','cyanotype'):
            panel._open_catalog_asset(donor,'reexpose',family);page=panel.workspace_host.currentWidget()
            assert page.optical_panel.body.currentData()==donor['body_id']
            page.optical_panel.applied.emit();wait(lambda:not page.busy)
            assert page.last_error is None and page.session.optical_body_id==donor['body_id']
        panel._open_catalog_asset(donor,'reexpose','film')
        assert panel.film_workspace._inline_editor_host.frames
        latent=str(tmp_path/'frame.darkroom_latent.npz')
        from film_foundry.ui.optical_source_panel import OpticalSourcePanel
        from film_foundry.ui.qt import QtWidgets
        monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *_:(latent,'Latent (*.darkroom_latent.npz)'))
        dialog=panel.film_workspace._inline_editor_host.frames[-1][1]
        dialog.findChild(OpticalSourcePanel).applied.emit();wait(lambda:not panel._processing)
        assert Path(latent).is_file()
        assert panel.pipeline_mode.get()=='develop_latent' and panel.negative_path.get()==latent
        assert panel.workspace_host.currentWidget() is panel.film_workspace
    finally:
        wait(lambda:not panel.workspace_host.busy_pages());panel.root.qt.close()


def test_desktop_separate_expose_develop_scan_publish_to_same_stack(tmp_path,monkeypatch):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    panel=DesktopPanel();panel.auto_preview.set(False);panel.root.qt.show()
    monkeypatch.setattr(panel,'_start_preview',lambda:None)
    try:
        image=source(tmp_path);panel.open_library();panel.library_view.catalog.add([image])
        cfg=panel._config();cfg.enable_grain=False;cfg.enable_mtf=False;cfg.enable_halation=False
        cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
        panel._apply_loaded_config(cfg);panel.run_as_preview.set(False)
        panel._open_catalog_source(str(image),'film');panel.pipeline_mode.set('expose');panel._update_mode_visibility()
        def execute():
            panel._start_process();dialog=panel.film_workspace._inline_editor_host.frames[-1][1]
            dialog.path.setText(str(tmp_path/'outputs'));dialog.apply();wait(lambda:not panel._processing)
        execute();catalog=panel.library_view.catalog
        latent=next(a for a in catalog.assets() if a['kind']=='latent')
        panel._open_catalog_asset(latent,'develop');execute()
        medium=next(a for a in catalog.assets() if a['kind']=='medium')
        stamp=Path(medium['path']).stat().st_mtime_ns
        panel._open_catalog_asset(medium,'scan');execute()
        assert len(catalog.results)==1 and len(catalog.results[0]['runs'])==2
        assert catalog.query(collection='assets',kind='scan')
        assert Path(medium['path']).stat().st_mtime_ns==stamp
    finally:
        wait(lambda:not panel._processing);panel.root.qt.close()
