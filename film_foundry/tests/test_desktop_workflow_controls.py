from film_foundry.tests.test_desktop_usability import desktop,photos
from film_foundry.ui.qt import _application


def test_workflow_choices_follow_source_and_studio_shows_active_controls(desktop,tmp_path):
    panel=desktop;panel._open_catalog_batch(photos(tmp_path),'film')
    assert panel.navigation.currentRow()==panel.page_index['film']
    assert panel.mode_choice.qt.count()==4
    panel.set_workspace_layout('studio',persist=False)
    panel.mode_display.set(panel.workflow_labels['develop']);panel._select_mode()
    assert panel.stack.currentIndex()==panel.page_index['output']
    panel.mode_display.set(panel.workflow_labels['full']);panel._select_mode()
    assert panel.stack.currentIndex()==panel.page_index['scan']
    panel.pipeline_mode.set('scan');panel._update_mode_visibility()
    assert panel.mode_choice.qt.count()==1 and '扫描' in panel.mode_choice.qt.currentText()
    assert not panel.film_border_group.qt.isEnabled()


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
