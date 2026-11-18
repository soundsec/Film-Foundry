from film_foundry.tests.test_desktop_usability import desktop,photos
from film_foundry.ui.qt import _application,QtGui,QtCore
from PySide6.QtTest import QTest


def test_development_modes_and_scan_visibility_are_independent(desktop):
    panel=desktop;panel.show_workbench()
    assert set(panel.mode_pickers)=={'develop'}
    develop=panel.mode_pickers['develop']
    panel.pipeline_bar.buttons['develop'].click();develop.buttons['principle'].click()
    assert panel._config().process_control_mode=='principle'
    panel.chemical_stain.set(.8)
    develop.buttons['expected'].click()
    assert panel._config().chemistry.chemical_stain==.8
    QTest.keyClick(panel.result_view,QtCore.Qt.Key.Key_F8);assert panel._config().process_control_mode=='normal'
    QTest.keyClick(panel.result_view,QtCore.Qt.Key.Key_F8);assert panel._config().process_control_mode=='expected'
    panel.pipeline_bar.buttons['scan'].click()
    before=panel._config();revision=panel._preview_revision
    panel.scan_advanced_toggle.click()
    assert not panel.sections['scan.details'].qt.isHidden() and panel.curve_tabs.isTabVisible(2)
    assert panel._config()==before and panel._preview_revision==revision
    QTest.keyClick(panel.result_view,QtCore.Qt.Key.Key_F8)
    assert panel.sections['scan.details'].qt.isHidden() and not panel.curve_tabs.isTabVisible(2)
    assert panel._config()==before and panel._preview_revision==revision
    panel.pipeline_mode.set('develop_latent');panel._update_mode_visibility()
    assert develop.buttons['principle'].isEnabled()
    panel.pipeline_mode.set('scan');panel._update_mode_visibility()
    assert not develop.buttons['principle'].isEnabled()
    panel.scan_advanced_toggle.click()
    assert panel._config().process_control_mode=='expected'


def test_workflow_choices_follow_source_and_studio_shows_active_controls(desktop,tmp_path):
    panel=desktop;panel._open_catalog_batch(photos(tmp_path),'film')
    assert panel.navigation.currentRow()==panel.page_index['film']
    assert panel.mode_choice.qt.count()==4
    panel.set_workspace_layout('studio',persist=False)
    assert not panel.studio_tabs.tabBar().isVisible() and not panel.studio_navigation.isVisible()
    panel.mode_display.set(panel.workflow_labels['develop']);panel._select_mode()
    assert panel.stack.currentIndex()==panel.page_index['output']
    panel.mode_display.set(panel.workflow_labels['full']);panel._select_mode()
    assert panel.stack.currentIndex()==panel.page_index['scan']
    panel.pipeline_mode.set('scan');panel._update_mode_visibility()
    assert panel.mode_choice.qt.count()==1 and '扫描' in panel.mode_choice.qt.currentText()
    assert not panel.film_border_group.qt.isEnabled()
    panel._active_roll=object()
    try:
        panel._sync_task_navigation();panel._sync_pipeline_bar()
        assert panel.task_navigation.isVisible() and not panel.pipeline_bar.isVisible()
    finally:panel._active_roll=None


def test_pipeline_distinguishes_skipped_photo_stages_from_frozen_media(desktop,tmp_path):
    panel=desktop;panel._open_catalog_batch(photos(tmp_path),'film')
    bar=panel.pipeline_bar
    assert bar.states=={'film':'editable','develop':'editable','scan':'observe','output':'output'}
    bar.buttons['output'].click()
    assert panel.navigation.currentRow()==panel.page_index['output'] and bar.buttons['output'].isChecked()
    panel.mode_display.set(panel.workflow_labels['expose']);panel._select_mode()
    assert bar.states['develop']=='skip' and bar.states['scan']=='skip'
    panel.mode_display.set(panel.workflow_labels['scan']);panel._select_mode()
    assert bar.states['film']=='skip' and bar.states['develop']=='skip'
    # Scanning an ordinary image must not invent a chemical formation record.
    bar.buttons['develop'].click()
    dialog=panel.film_workspace._inline_editor_host.frames[-1][1]
    assert not dialog.actions and panel.pipeline_mode.get()=='scan'
    dialog.reject()


def test_stage_endpoints_preserve_navigation_sources_and_busy_task(desktop,tmp_path,monkeypatch):
    panel=desktop;paths=photos(tmp_path);panel._open_catalog_batch(paths,'film')
    bar=panel.pipeline_bar;started=[]
    monkeypatch.setattr(panel,'_execute_process',lambda:started.append(True))
    original=panel._config();revision=panel._preview_revision
    bar.buttons['develop'].click()
    assert panel.pipeline_mode.get()=='full' and bar.endpoints['scan'].isChecked()
    assert panel._config()==original and panel._preview_revision==revision
    assert not panel.legacy_task_row.isVisible()
    assert panel.develop_source_group.qt.title().endswith('重新冲洗')
    for key,mode in (('film','expose'),('develop','develop'),('scan','full')):
        bar.endpoints[key].click()
        assert panel.pipeline_mode.get()==mode and bar.endpoints[key].isChecked()
        assert sum(button.isChecked() for button in bar.endpoints.values())==1
    bar.scan_menu.actions()[1].trigger()
    assert panel.pipeline_mode.get()=='scan' and panel.negative_path.get()==paths[0]
    assert not panel.scan_invert.get() and not panel.scan_remove_base.get()
    assert bar.states['film']=='skip' and panel.develop_source_group.qt.title().endswith('跳过')
    bar.endpoints['scan'].click()
    assert panel.pipeline_mode.get()=='full' and panel.input_path.get()==paths[0]
    assert panel.scan_remove_base.get() and panel._selected_photos==paths
    panel._processing=True
    try:
        panel._sync_activity_ui()
        assert not any(button.isEnabled() for button in bar.endpoints.values())
        panel._select_pipeline_endpoint('expose')
        assert panel.pipeline_mode.get()=='full'
    finally:
        panel._processing=False;panel._sync_activity_ui()
    assert all(button.isEnabled() for button in bar.endpoints.values()) and not started


def test_film_border_dialog_apply_cancel_and_imprint_are_independent(desktop):
    panel=desktop;panel.show_workbench();panel.imprint_text.set('DATE')
    panel._edit_film_border()
    dialogs=[w for w in _application().topLevelWidgets() if w.__class__.__name__=='FilmBorderDialog' and w.isVisible()]
    assert len(dialogs)==1
    dialog=dialogs[0];dialog.enabled.setChecked(True);dialog.label.setText('FOUNDRY')
    dialog.template.setCurrentIndex(dialog.template.findData('cinema'));dialog.edit_stencil()
    child=dialog.child;child.side.setCurrentIndex(1)
    assert child.codes==['',''] and child.grid.pattern().height==20
    child.spread.setValue(1.2);child.accept()
    assert dialog.value().factory_mark_spread==1.2
    assert not dialog.value().factory_pattern_bottom
    dialog.accept()
    assert panel._config().film_border.enabled and panel._config().film_border.stock_label=='FOUNDRY'
    assert panel._config().film_border.imprint_text=='DATE'
    panel._edit_film_border()
    dialog=[w for w in _application().topLevelWidgets() if w.__class__.__name__=='FilmBorderDialog' and w.isVisible()][0]
    dialog.enabled.setChecked(False);dialog.reject()
    assert panel._config().film_border.enabled
