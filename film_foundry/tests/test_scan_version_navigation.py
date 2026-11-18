"""A saved scan reopens its medium and recipe, without editing the old result."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image

from film_foundry.app.desktop_catalog import DesktopCatalog
from film_foundry.app.film_task import scan_version_context, rescan_config
from film_foundry.app.workspace_jobs import scan_record, workflow_result_record
from film_foundry.app.api import WorkflowRequest, run_request
from film_foundry.core.atomic_io import atomic_write_json, strict_json_load
from film_foundry.tests.test_film_task_navigation import formed
from film_foundry.tests.test_desktop_usability import desktop, wait
from film_foundry.ui.qt import QtCore


def saved_scans(tmp_path):
    source,latent,medium,cfg=formed(tmp_path)
    cfg.scanner.transmission_light_ev=.65
    cfg.scanner.tone_curve=((0.,0.),(.45,.57),(1.,1.))
    cfg.scanner.scan_saturation=1.3
    cfg.output.rotation_quarters=1;cfg.output.render_long_edge=30
    cfg.output.anti_banding_strength=0
    record=workflow_result_record(latent.output_path,medium)
    record=scan_record(record,cfg,output=tmp_path/'scans')
    first=Path(record['cover'])
    second_cfg=deepcopy(cfg);second_cfg.scanner.transmission_light_ev=-.8
    record=scan_record(record,second_cfg,output=tmp_path/'scans')
    return source,medium,cfg,record,first


def test_saved_scan_recipe_reproduces_pixels_and_requires_real_source(tmp_path):
    source,medium,cfg,record,first=saved_scans(tmp_path)
    context=scan_version_context(first)
    assert context['problem'] is None and Path(context['medium'])==medium.medium_path
    current=deepcopy(cfg);current.processing.quality_mode='high'
    current.scanner.scan_saturation=.1;current.output.rotation_quarters=0
    restored=rescan_config(context,current)
    assert restored.processing.quality_mode=='high'
    assert asdict(restored.scanner)==asdict(cfg.scanner)
    replay=scan_record(deepcopy(record),restored,output=tmp_path/'replay')
    np.testing.assert_array_equal(np.asarray(Image.open(first)),np.asarray(Image.open(replay['cover'])))
    # Full jobs locate the saved medium through their explicit result membership.
    full=run_request(WorkflowRequest('full',source,tmp_path/'full.png',cfg))
    workflow_result_record(source,full)
    assert Path(scan_version_context(full.output_path)['medium'])==full.medium_path
    sidecar=first.with_suffix(first.suffix+'.json');data=strict_json_load(sidecar)
    data['scan_source_path']='absent.npz';atomic_write_json(sidecar,data)
    assert scan_version_context(first)['problem']=='missing_medium'
    # An explicit missing source must not silently use a different catalogue run.
    assert Path(record['medium']).is_file()
    sidecar.unlink()
    assert scan_version_context(first)['problem']=='missing_record'


def test_scan_version_actions_preserve_files_and_gallery_browsing(desktop,tmp_path,monkeypatch):
    panel=desktop;_,medium,cfg,record,first=saved_scans(tmp_path)
    panel.open_library();catalog=panel.library_view.catalog;catalog.register_result(record)
    originals=[medium.medium_path,first,Path(record['cover'])]
    stamps={p:p.stat().st_mtime_ns for p in originals}
    asset=next(a for a in catalog.assets() if a['path']==str(first))
    panel._open_result_stack(dict(catalog.results[0],focus_asset=asset['id']))
    page=panel.workspace_host.currentWidget()
    assert page.scan_version_button.isEnabled() and not page.scan_version_button.isHidden()
    panel._apply_loaded_config(cfg);panel.light_table_ev.set('-2')
    before=panel.work_sessions.film_snapshot()
    monkeypatch.setattr(panel.work_sessions,'decision',lambda:'cancel')
    page.scan_version_button.click()
    assert panel.work_sessions.film_snapshot()==before
    monkeypatch.setattr(panel.work_sessions,'decision',lambda:'discard')
    page.scan_version_button.click();wait(lambda:not panel._preview_running)
    assert panel.pipeline_mode.get()=='scan' and Path(panel.negative_path.get())==medium.medium_path
    assert panel._scan_version_path==str(first)
    assert panel.scanner_preset.get()=='' and panel.scanner_preset_combo.qt.currentIndex()==-1
    assert panel._config().scanner.transmission_light_ev==.65
    assert panel._config().output.rotation_quarters==1
    panel.output_path.set(str(tmp_path/'adjusted'));panel.scan_output_near_source.set(False)
    panel._execute_process();wait(lambda:not panel._processing)
    wait(lambda:len(catalog.query(collection='assets',kind='scan'))==3)
    assert stamps=={p:p.stat().st_mtime_ns for p in originals}
    new=next(a for a in catalog.query(collection='assets',kind='scan') if Path(a['path']) not in originals)
    np.testing.assert_array_equal(np.asarray(Image.open(first)),np.asarray(Image.open(new['path'])))
    panel.open_library();library=panel.library_view
    library.collection.setCurrentIndex(2);library.refresh()
    item=next(library.grid.item(i) for i in range(library.grid.count()) if library.grid.item(i).data(QtCore.Qt.ItemDataRole.UserRole)['id']==asset['id'])
    library.grid.setCurrentItem(item)
    assert library.scan_version_button.isEnabled() and not library.scan_version_button.isHidden()
    library.browse_selected();browser=panel.workspace_host.currentWidget()
    assert browser.browse_only and browser.scan_version_button.isHidden()


def test_saved_scan_session_restores_navigation_without_prior_undo(desktop,tmp_path,monkeypatch):
    from film_foundry.app.desktop_settings import load_settings
    panel=desktop;source,medium,cfg,record,first=saved_scans(tmp_path)
    panel._open_scan_version(str(first));wait(lambda:not panel._preview_running)
    panel._navigate_workspace(panel.page_index['output'])
    panel.edit_tools.comparison.setCurrentIndex(panel.edit_tools.comparison.findData('wipe'))
    panel.result_view.divider=.3
    assert panel.work_sessions.save([panel.film_workspace])
    saved=load_settings()['recent_work'][0]
    assert saved['label']==medium.medium_path.name
    panel._navigate_workspace(panel.page_index['scan'])
    panel.edit_tools.comparison.setCurrentIndex(0)
    assert panel.film_workspace not in panel.work_sessions.dirty([panel.film_workspace])
    panel.open_library();panel.show_workbench()
    assert panel._scan_version_path==str(first)
    panel._replace_film_sources([str(source)]);panel.edit_tools.record()
    panel.light_table_ev.set('2');panel.edit_tools.record()
    assert panel.edit_tools.history.can_undo
    panel.work_sessions.open_work(saved['path'],'session')
    wait(lambda:not panel._preview_running)
    assert panel._scan_version_path==str(first) and panel.pipeline_mode.get()=='scan'
    assert panel.scanner_preset.get()=='' and panel.scanner_preset_combo.qt.currentIndex()==-1
    assert panel.navigation.currentRow()==panel.page_index['output']
    assert panel.result_view.mode=='wipe' and panel.result_view.divider==.3
    assert not panel.edit_tools.history.can_undo
    assert not panel.work_sessions.dirty([panel.film_workspace])
