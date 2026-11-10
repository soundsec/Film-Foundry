"""Interaction checks for catalog-only management and active work actions."""
from pathlib import Path

import pytest
from PySide6.QtTest import QTest
from film_foundry.ui.qt import QtCore,QtWidgets,_application
from film_foundry.ui.curve_editor import text
from film_foundry.tests.test_desktop_usability import desktop,photos,wait


def action(menu,zh,en):
    return next(a for a in menu.actions() if a.text()==text(zh,en))


def test_context_selection_rates_favorites_and_removes_only_index(desktop,tmp_path,monkeypatch):
    desktop.open_library();library=desktop.library_view;paths=photos(tmp_path);library.catalog.add(paths);library.refresh()
    _application().processEvents()
    assert library.grid.selectionMode()==QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
    action(library.selection_menu(library.grid.item(0)),'选择','Select').trigger()
    QTest.mouseClick(library.grid.viewport(),QtCore.Qt.MouseButton.LeftButton,
                    pos=library.grid.visualItemRect(library.grid.item(1)).topLeft()+QtCore.QPoint(20,20))
    assert len(library.grid.selectedItems())==2 and library.grid.property('multiSelect')
    menu=library.selection_menu(library.grid.item(1))
    action(menu,'评分','Rating').menu().actions()[4].trigger()
    assert len(library.grid.selectedItems())==2
    assert all(i.data(QtCore.Qt.ItemDataRole.UserRole)['rating']==4 for i in library.grid.selectedItems())
    action(library.selection_menu(library.grid.selectedItems()[0]),'收藏','Favorite').trigger()
    assert all(i.data(QtCore.Qt.ItemDataRole.UserRole)['favorite'] for i in library.grid.selectedItems())
    monkeypatch.setattr(QtWidgets.QMessageBox,'question',lambda *a:QtWidgets.QMessageBox.StandardButton.Yes)
    action(library.selection_menu(library.grid.selectedItems()[0]),'从图库删除','Remove from library').trigger()
    library.refresh();assert library.grid.count()==1 and all(Path(p).is_file() for p in paths)
    action(library.selection_menu(),'完成选择','Done selecting').trigger()
    assert not library.grid.property('multiSelect')


def test_sidebar_alias_lock_and_removal_keep_files(desktop,tmp_path,monkeypatch):
    from film_foundry.app.desktop_settings import load_settings,save_settings,sidebar_entry,update_sidebar_entry,remember_work
    desktop.open_library();library=desktop.library_view;paths=photos(tmp_path);library.catalog.add(paths)
    values=load_settings();values['source_folders']=[str(tmp_path)];save_settings(values)
    sidebar=library.sidebar;sidebar.refresh()
    value=('folder',str(tmp_path))
    monkeypatch.setattr(QtWidgets.QInputDialog,'getText',lambda *a,**kw:('照片来源',True))
    sidebar.rename_entry(value,tmp_path.name);sidebar.lock_entry(value,True)
    assert sidebar_entry('folder',tmp_path)['label']=='照片来源'
    with pytest.raises(ValueError):update_sidebar_entry('folder',tmp_path,remove=True)
    sidebar.lock_entry(value,False);sidebar.remove_entry(value)
    assert not load_settings()['source_folders'] and len(library.catalog.query())==3
    sidebar.refresh()
    iterator=QtWidgets.QTreeWidgetItemIterator(sidebar.tree)
    while iterator.value():
        assert iterator.value().data(0,QtCore.Qt.ItemDataRole.UserRole)!=value
        iterator+=1
    # A directory walk finishing after removal cannot add more entries.
    extra=tmp_path/'late.png';extra.write_bytes(Path(paths[0]).read_bytes())
    sidebar.finish_index([str(extra)],'');assert len(library.catalog.query())==3
    saved=tmp_path/'existing.json';saved.write_text('{}');remember_work(saved,'session')
    sidebar.remove_entry(('work',load_settings()['recent_work'][0]))
    assert saved.is_file() and not load_settings()['recent_work']
    page=QtWidgets.QWidget();host=desktop.workspace_host;host.add_page(page,'Work',key='test-work')
    sidebar.rename_entry(('live',page),'Work');assert host._pages[page][0]=='照片来源'
    sidebar.lock_entry(('live',page),True);host.close_page(host.indexOf(page));assert page in host._pages
    sidebar.lock_entry(('live',page),False);host.close_page(host.indexOf(page));wait(lambda:page not in host._pages)


def test_active_work_save_visibility_and_browser_information(desktop,tmp_path):
    panel=desktop;paths=photos(tmp_path);panel.open_library()
    assert panel.save_work_button.isHidden()
    panel.show_workbench();assert panel.save_work_button.isHidden()
    panel.input_path.set('');panel._update_save_work_action();assert panel.save_work_button.isHidden()
    panel._open_catalog_source(paths[0],'film');wait(lambda:panel.save_work_button.isVisible())
    panel.open_library();assert panel.save_work_button.isHidden()
    library=panel.library_view;library.catalog.add(paths);library.refresh();library.grid.setCurrentRow(0);library.browse_selected()
    browser=panel.workspace_host.currentWidget()
    assert panel.save_work_button.isHidden()
    assert browser.file_info['dimensions'].text()=='24 × 16 px'
    first=browser.file_info['path'].text();browser.move(1)
    assert browser.file_info['path'].text()!=first
    assert browser.file_info['path'].text()==browser.selected_asset()['path']
    assert browser.tree.parent() is not browser.split and browser.open_button.isHidden()
    assert browser.split.widget(0) is browser.browser_sidebar and browser.split.widget(1) is browser.preview
    panel.show_workbench();assert panel.save_work_button.isVisible()


def test_output_controls_and_edge_layout_do_not_share_imprint_fields(desktop):
    panel=desktop
    page=panel.pages[panel.page_index['output']].qt
    labels=[b.text() for b in page.findChildren(QtWidgets.QPushButton)]
    assert not any('配方' in s or '会话' in s or '母带' in s for s in labels)
    from film_foundry.ui.workflow_text import imprint_style_label
    panel.imprint_text.set('TEST 2026');panel.imprint_style_display.set(imprint_style_label('optical_amber'))
    panel._edit_edge_stencil()
    dialogs=[w for w in _application().topLevelWidgets() if w.__class__.__name__=='EdgeMarkEditor' and w.isVisible()]
    assert len(dialogs)==1
    dialog=dialogs[0];dialog.template_text.setText('FOUNDRY');dialog.generate_label();dialog.accept()
    config=panel._config()
    assert config.film_border.imprint_text=='TEST 2026' and config.film_border.factory_pattern_top.startswith('FFEM1:')
    assert config.film_border.imprint_style=='optical_amber'
    border=panel.fields['scanner_raw_border'][0].qt;dither=panel.fields['anti_banding'][0].qt
    assert border.parent() is dither.parent()
