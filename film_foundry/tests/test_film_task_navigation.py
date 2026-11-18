"""Saved medium -> scans -> fresh latent branch, without changing prior files."""
from pathlib import Path
from copy import deepcopy
import shutil

from PIL import Image

from film_foundry.app.api import WorkflowRequest, run_request
from film_foundry.app.desktop_catalog import DesktopCatalog
from film_foundry.app.workspace_jobs import workflow_result_record
from film_foundry.app.film_task import film_task_context, redevelopment_config
from film_foundry.model.config import DarkroomConfig
from film_foundry.tests.test_desktop_usability import desktop, wait
from film_foundry.ui.qt import _application


def formed(tmp_path):
    source=tmp_path/'source.png';Image.new('RGB',(36,24),(100,80,60)).save(source)
    cfg=DarkroomConfig(enable_grain=False,enable_halation=False,enable_mtf=False)
    cfg.chemistry.temperature_c=23
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False;cfg.output.export_layer_pack=False
    latent=run_request(WorkflowRequest('expose',source,tmp_path/'source.darkroom_latent.npz',cfg))
    developed=run_request(WorkflowRequest('develop_latent',latent.output_path,tmp_path/'first',cfg))
    return source,latent,developed,cfg


def test_context_uses_recorded_parent_and_keeps_local_execution_settings(tmp_path):
    source,latent,developed,cfg=formed(tmp_path)
    context=film_task_context(developed.medium_path)
    assert context['latents']==[str(latent.output_path.resolve())]
    current=deepcopy(cfg);current.processing.quality_mode='high';current.output.quality=40
    resumed=redevelopment_config(context,current)
    assert resumed.chemistry.temperature_c==23
    assert resumed.processing.quality_mode=='high' and resumed.output.quality==40
    assert context['config']!=None
    # Even an adjacent, identically named exposure must not be guessed.
    orphan=tmp_path/'orphan.npz';shutil.copy2(developed.medium_path,orphan)
    orphan_context=film_task_context(orphan)
    assert orphan_context['latents']==[] and orphan_context['config'] is None
    latent.output_path.unlink()
    assert film_task_context(developed.medium_path)['latents']==[]


def test_scan_task_layout_readonly_record_and_redevelopment(desktop,tmp_path):
    panel=desktop;source,latent,developed,cfg=formed(tmp_path)
    catalog=DesktopCatalog();catalog.register_result(workflow_result_record(latent.output_path,developed))
    before={p:p.stat().st_mtime_ns for p in (latent.output_path,developed.medium_path)}
    panel.develop_temperature.set('39')
    panel._open_result_artifact(str(developed.medium_path),'film')
    wait(lambda:not panel._preview_running)
    assert panel.pipeline_mode.get()=='scan'
    assert not panel.legacy_task_row.isVisible()
    assert not panel.edit_tools.history.can_undo
    assert panel._current_film_context()['config']['chemistry']['temperature_c']==23
    assert panel.redevelop_button.qt.isEnabled()
    assert panel.pipeline_bar.states=={'film':'frozen','develop':'frozen','scan':'observe','output':'output'}
    assert not panel.pipeline_bar.endpoints['scan'].isEnabled()
    assert panel.pipeline_bar.endpoints['film'].isHidden()
    assert panel.pipeline_bar.summaries['film']==cfg.film.name
    assert panel.pipeline_bar.summaries['develop']==cfg.chemistry.developer_name
    panel._select_pipeline_endpoint('expose')
    assert panel.pipeline_mode.get()=='scan' and Path(panel.negative_path.get())==developed.medium_path
    assert panel.develop_source_group.qt.title().endswith('已冻结')
    assert panel.sections['scan.details'].impact_badge.text()=='仅观察'
    for layout in ('studio','compact','studio'):
        panel.set_workspace_layout(layout,persist=False);_application().processEvents()
        assert panel.stack.currentIndex()==panel.page_index['scan']
        assert all(panel.navigation.item(panel.page_index[k]).isHidden() for k in ('film','develop'))
        assert not panel.navigation.item(panel.page_index['compute']).isHidden()
        assert not panel.studio_tabs.isVisible()
        assert not panel.navigator_panel.isVisible() and not panel.task_navigation.isVisible()
        assert not panel.source_record_button.qt.isVisible() and not panel.redevelop_button.qt.isVisible()
        assert not panel.formation_badge.isVisible() and not panel.scan_link_row.qt.isVisible()
        assert not panel.edit_tools.quick.isVisible()
        panel.pipeline_bar.buttons['output'].click()
        assert panel.stack.currentIndex()==panel.page_index['output']
        assert not panel.fields['save_scanner_raw'].qt.isVisible()
        panel.pipeline_bar.buttons['scan'].click()
    panel.pipeline_bar.buttons['develop'].click()
    dialog=panel.film_workspace._inline_editor_host.frames[-1][1]
    assert dialog.actions['record'].isEnabled() and dialog.actions['branch'].isEnabled()
    dialog.actions['record'].click();_application().processEvents()
    record=panel.film_workspace._inline_editor_host.frames[-1][1]
    assert record.__class__.__name__=='FilmSourceDialog' and record.tree.topLevelItemCount()>0
    record.reject()
    panel.pipeline_bar.buttons['develop'].click()
    dialog=panel.film_workspace._inline_editor_host.frames[-1][1]
    dialog.actions['branch'].click();wait(lambda:not panel._preview_running)
    assert panel.pipeline_mode.get()=='develop_latent' and Path(panel.negative_path.get())==latent.output_path
    assert float(panel.develop_temperature.get())==23
    assert panel.navigation.currentRow()==panel.page_index['develop']
    assert not panel.navigation.item(panel.page_index['develop']).isHidden()
    assert panel.pipeline_bar.states['film']=='frozen' and panel.pipeline_bar.states['develop']=='editable'
    assert panel.develop_source_group.qt.title().endswith('重新冲洗')
    assert panel.pipeline_bar.endpoints['develop'].isChecked() and not panel.pipeline_bar.endpoints['develop'].isEnabled()
    assert panel.navigation.item(panel.page_index['film']).isHidden()
    assert not panel.edit_tools.history.can_undo
    assert before=={p:p.stat().st_mtime_ns for p in before}
    panel._replace_film_sources([str(source)])
    assert not panel.navigation.item(panel.page_index['film']).isHidden() and panel.pipeline_bar.endpoints['film'].isEnabled()


def test_two_scans_and_new_development_remain_in_one_stack(desktop,tmp_path):
    panel=desktop;_,latent,developed,cfg=formed(tmp_path)
    panel.open_library();catalog=panel.library_view.catalog
    # Import only a medium first: its sidecar supplies the exact original latent.
    panel._open_result_artifact(str(developed.medium_path),'film')
    wait(lambda:not panel._preview_running)
    stamp=developed.medium_path.stat().st_mtime_ns
    panel._apply_loaded_config(cfg)
    panel.output_path.set(str(tmp_path/'scans'));panel.scan_output_near_source.set(False)
    panel._execute_process();wait(lambda:not panel._processing)
    wait(lambda:len(catalog.query(collection='assets',kind='scan'))==1)
    panel.light_table_ev.set('0.5')
    panel._execute_process();wait(lambda:not panel._processing)
    wait(lambda:len(catalog.query(collection='assets',kind='scan'))==2)
    paths=[a['path'] for a in catalog.query(collection='assets',kind='scan')]
    assert len(set(paths))==2 and all(Path(p).is_file() for p in paths)
    assert developed.medium_path.stat().st_mtime_ns==stamp
    panel._redevelop_from_latent();wait(lambda:not panel._preview_running)
    panel.output_path.set(str(tmp_path/'second'));panel._execute_process();wait(lambda:not panel._processing)
    wait(lambda:len(catalog.query(collection='assets',kind='medium'))==2)
    assert len(catalog.results)==1 and len(catalog.query(collection='assets',kind='scan'))==2
    assert developed.medium_path.stat().st_mtime_ns==stamp
    assert Path(panel.negative_path.get())==latent.output_path
    panel._show_related_results()
    assert panel.workspace_host.currentWidget().__class__.__name__=='ResultStackView',panel.status.get()
    assert panel.workspace_host.currentWidget().selected_asset()['kind']=='medium'
