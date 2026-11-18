"""Photo/latent/checkpoint navigation over real frozen reaction work."""
from copy import deepcopy
from pathlib import Path

import numpy as np
from PIL import Image

from film_foundry.app.api import WorkflowRequest,run_request
from film_foundry.app.workspace_jobs import workflow_result_record
from film_foundry.app.film_task import film_task_context
from film_foundry.app.film_process_work import start_work,describe_work
from film_foundry.core.film_process.checkpoint_io import inspect_checkpoint
from film_foundry.core.negative_io import load_developed_negative_npz
from film_foundry.tests.test_film_process_work import config,source
from film_foundry.tests.test_desktop_usability import desktop,wait
from film_foundry.ui.qt import QtWidgets


def test_photo_exposure_to_staged_development_and_scan(desktop,tmp_path,monkeypatch):
    from film_foundry.app.desktop_settings import load_settings
    panel=desktop;c=config('bw_negative');c.chemistry.temperature_c=23
    image=tmp_path/'photo.png';Image.new('RGB',(36,24),(85,150,200)).save(image)
    exposed=run_request(WorkflowRequest('expose',image,tmp_path/'photo.darkroom_latent.npz',c))
    panel.open_library();catalog=panel.library_view.catalog
    panel._record_result(workflow_result_record(image,exposed))
    panel._replace_film_sources([str(image)]);panel._show_related_results()
    stack=panel.workspace_host.currentWidget()
    assert stack.selected_asset()['kind']=='latent'
    stack.open_selected();wait(lambda:not panel._preview_running)
    panel._apply_loaded_config(c)
    assert panel.staged_develop_button.qt.isVisible()
    assert panel.preview_tabs.tabText(0)=='冲洗预览'
    before=panel.work_sessions.film_snapshot()
    cp=tmp_path/'start.ffprocess.npz'
    monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *a,**k:(str(cp),''))
    monkeypatch.setattr(QtWidgets.QFileDialog,'getOpenFileName',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Latent already selected')))
    panel.staged_develop_button.qt.click();page=panel.workspace_host.currentWidget();wait(lambda:not page.busy)
    assert page.last_error is None and page.info['next_step']==0
    assert panel.work_sessions.film_snapshot()==before
    panel.develop_temperature.set('40')
    mid=tmp_path/'mid.ffprocess.npz';assert page.advance_to(mid,1);wait(lambda:not page.busy)
    assert inspect_checkpoint(mid)['config']['chemistry']['temperature_c']==23
    stamps={p:p.stat().st_mtime_ns for p in (cp,mid,exposed.output_path)}
    final=tmp_path/'finished.npz';assert page.advance_to(final);wait(lambda:not page.busy)
    assert page.last_error is None and page.view_button.isVisible()
    assert not page.finish_button.isEnabled() and not page.advance_button.isEnabled()
    assert not page.advance_to(tmp_path/'accidental.npz')
    assert all(page.steps.topLevelItem(i).text(3)=='已执行' for i in range(page.steps.topLevelItemCount()))
    assert page.info['next_step']==1  # The saved checkpoint itself has not advanced.
    assert stamps=={p:p.stat().st_mtime_ns for p in stamps}
    assert film_task_context(final,catalog)['latents']==[str(exposed.output_path.resolve())]
    assert len(catalog.results)==1
    assert len(catalog.query(collection='assets',kind='state'))==2
    assert any(w['kind']=='process' and Path(w['path'])==mid for w in load_settings()['recent_work'])
    expected=run_request(WorkflowRequest('develop_latent',exposed.output_path,tmp_path/'reference',c))
    np.testing.assert_array_equal(load_developed_negative_npz(final).optical_density_rgb,
                                  load_developed_negative_npz(expected.medium_path).optical_density_rgb)
    page.view_button.click();wait(lambda:not panel._preview_running)
    assert panel.pipeline_mode.get()=='scan' and Path(panel.negative_path.get())==final
    panel.workspace_host.setCurrentWidget(page);page.branch_button.click();wait(lambda:not page.busy)
    assert page.final_path is None and page.info['next_step']==1 and final.is_file()
    assert len(catalog.results)==1 and len(catalog.query(collection='assets',kind='state'))==2


def test_checkpoint_documents_keep_independent_state_and_recipe(desktop,tmp_path,monkeypatch):
    panel=desktop;c=config('bw_negative');latent=source(tmp_path,c)
    a=tmp_path/'a.ffprocess.npz';b=tmp_path/'b.ffprocess.npz'
    start_work(latent,a,c,stop_after=0);start_work(latent,b,c,stop_after=1)
    # Opening frozen work cannot depend on a currently invalid main form.
    monkeypatch.setattr(panel,'_config',lambda:(_ for _ in ()).throw(ValueError('Unfinished main form')))
    first=panel.open_film_process_workspace(str(a));wait(lambda:not first.busy)
    second=panel.open_film_process_workspace(str(b));wait(lambda:not second.busy)
    assert first is not second and first.path==a and second.path==b
    assert first.info['next_step']==0 and second.info['next_step']==1
    assert panel.open_film_process_workspace(str(a)) is first
    panel.work_sessions.open_work(str(b),'process')
    assert panel.workspace_host.currentWidget() is second
    assert first.final_path is None and first.info['next_step']==0
    monkeypatch.undo()


def test_bath_dialog_retains_draft_when_destination_is_cancelled(desktop,tmp_path,monkeypatch):
    panel=desktop;c=config('bw_negative');latent=source(tmp_path,c)
    cp=tmp_path/'start.ffprocess.npz';start_work(latent,cp,c)
    page=panel.open_film_process_workspace(str(cp));wait(lambda:not page.busy)
    page.edit_bath();editor=page.bath_editor
    assert editor.isWindow() and editor.isVisible()
    editor.controls['temperature_c'].setValue(27)
    monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *a,**k:('',''))
    editor.apply()
    assert editor.isVisible() and editor.controls['temperature_c'].value()==27
    assert not page.busy and page.path==cp
    editor.reject()
