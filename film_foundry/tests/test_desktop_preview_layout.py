"""Focused routing and native-resolution browsing checks."""
from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from PySide6.QtTest import QTest
from film_foundry.ui.qt import _application,QtCore,QtGui
from film_foundry.tests.test_desktop_usability import desktop,photos,wait


def test_studio_left_tabs_always_restore_scan_controls(desktop,tmp_path):
    panel=desktop;panel._open_catalog_source(photos(tmp_path)[0],'film')
    panel.exposure_ev.set('0.7');panel.navigation.setCurrentRow(panel.page_index['output'])
    panel.set_workspace_layout('studio',persist=False);_application().processEvents()
    develop=panel.studio_tabs.indexOf(panel.studio_moved[panel.page_index['develop']])
    for _ in range(2):
        panel.studio_navigation.setCurrentIndex(panel.page_index['compute'])
        QTest.mouseClick(panel.studio_tabs.tabBar(),QtCore.Qt.MouseButton.LeftButton,pos=panel.studio_tabs.tabBar().tabRect(develop).center())
        assert panel.stack.currentWidget().widget() is panel.pages[panel.page_index['scan']].qt
        assert panel.studio_tabs.currentIndex()==develop
    panel.studio_tabs.setCurrentWidget(panel.studio_moved[panel.page_index['film']])
    assert panel.stack.currentIndex()==panel.page_index['scan']
    assert panel.film_source_group.qt.isAncestorOf(panel.material_filter_choices[0].qt)
    assert panel.film_source_group.qt.isAncestorOf(panel.film_preset_combo.qt)
    preset=panel.film_preset.get();panel.material_color_display.set(panel._color_filter_labels and next(k for k,v in panel._color_filter_labels.items() if v=='monochrome'))
    panel._on_material_list_filter();assert panel.film_preset.get()==preset and float(panel.exposure_ev.get())==.7
    for mode in ('compact','studio','compact','studio'):
        panel.set_workspace_layout(mode,persist=False);_application().processEvents()
        assert panel.stack.currentIndex()==panel.page_index['scan' if mode=='studio' else 'film']
        assert panel.preview_histogram.parent() is panel.control_panel
    assert panel.preview_histogram.height()==100


def test_browser_upgrades_thumbnail_to_native_image_and_ignores_stale_result(desktop,tmp_path,monkeypatch):
    import film_foundry.ui.desktop_library as module
    from film_foundry.ui.image_cache import preview_images
    path=tmp_path/'large.png';other=tmp_path/'second.png'
    Image.new('RGB',(2100,1200),(20,80,130)).save(path)
    Image.new('RGB',(2010,1000),(150,50,30)).save(other)
    preview_images.clear();module.image_preview(path)
    original=module.image_preview;started=Event();release=Event()
    def controlled(filename,width=192,height=128):
        if Path(filename)==path and width==0:
            started.set();assert release.wait(8)
        return original(filename,width,height)
    monkeypatch.setattr(module,'image_preview',controlled)
    panel=desktop;panel.open_library();library=panel.library_view
    library.catalog.add([str(path),str(other)]);entries=library.catalog.query(sort='name')
    panel._open_library_browser(entries,next(e['id'] for e in entries if e['path']==str(path)))
    browser=panel.workspace_host.currentWidget()
    try:
        wait(started.is_set)
        assert browser.preview.source.width()<2100
        browser.move(1);release.set()
        wait(lambda:browser.preview.source is not None and browser.preview.source.width()==2010)
        assert browser.preview.source.height()==1000 and browser.file_info['name'].text()=='second.png'
        browser.move(-1)
        wait(lambda:browser.preview.source is not None and browser.preview.source.width()==2100)
        assert browser.preview.source.height()==1200
        browser.preview.actual_size();assert browser.preview.zoom_percent==100
    finally:release.set()


def test_native_upgrade_preserves_inspected_region():
    from film_foundry.ui.image_view import ImageView
    _application();view=ImageView();view.resize(400,300);view.show()
    try:
        small=QtGui.QImage(300,200,QtGui.QImage.Format.Format_RGB32);small.fill(QtGui.QColor('gray'))
        large=small.scaled(2100,1400)
        view.show_image(small);view.zoom(2);view.centerOn(175,110)
        center=view.mapToScene(view.viewport().rect().center());before=(center.x()/300,center.y()/200)
        view.show_image(large,preserve_view=True)
        center=view.mapToScene(view.viewport().rect().center());after=(center.x()/2100,center.y()/1400)
        assert after==pytest.approx(before,abs=.01) and not view._fit_mode
    finally:view.close()
