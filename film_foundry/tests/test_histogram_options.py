import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
import pytest
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets,_application
from film_foundry.tests.test_desktop_usability import desktop
from film_foundry.tests.test_layout_memory import drag,settle,near


def test_context_menu_global_controls_and_reopen_share_preferences(tmp_path,monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    from film_foundry.ui.histogram import Histogram
    from film_foundry.ui.histogram_settings import HistogramSettings,HistogramSettingsPanel
    from film_foundry.ui.preferences import save_preferences,load_preferences
    _application();first=Histogram();second=Histogram();settings=HistogramSettingsPanel()
    pixels=np.array([[[0,20,30],[255,60,70],[70,70,70]]],np.uint8)
    first.set_pixels(pixels);before=first.bins.copy();first.resize(340,170);first.show();settle()
    try:
        event=QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Reason.Mouse,QtCore.QPoint(40,50),first.mapToGlobal(QtCore.QPoint(40,50)))
        _application().sendEvent(first,event)
        menu=first._menu;assert menu.isVisible()
        menu.findChild(QtGui.QAction,'kind.bars').trigger()
        menu.findChild(QtGui.QAction,'fill_style.outline').trigger()
        menu.findChild(QtGui.QAction,'channel.L').trigger()
        menu.close();settle()
        assert settings.controls['kind'].currentData()=='bars' and settings.channels['L'].isChecked()
        settings.controls['scale'].setCurrentIndex(1);settings.channels['G'].setChecked(False)
        assert second.settings.values['channels']==['R','B','L'] and first.logarithmic
        first.shadow_button.click();assert settings.controls['shadows'].isChecked()
        assert second.shadow_button.isChecked()
        settings.controls['fill_style'].setCurrentIndex(settings.controls['fill_style'].findData('solid'))
        assert second.settings.values['fill_style']=='solid' and first.settings.values['filled']
        save_preferences(theme='ivory')
        reopened=HistogramSettings(first.settings.path)
        assert reopened.values==first.settings.values==load_preferences()['histogram']
        assert not first.grab().isNull()
        first.settings.update(channels=[]);assert not first.grab().isNull()
        np.testing.assert_array_equal(first.bins,before)
    finally:first.close();second.close();settings.close()


def test_solid_histogram_is_opaque_and_adds_rgb_without_covering_channels(tmp_path,monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    from film_foundry.ui.histogram import Histogram
    from film_foundry.ui.histogram_settings import normalise
    assert normalise({'filled':True})['fill_style']=='translucent'
    assert normalise({'filled':False})['fill_style']=='outline'
    assert normalise({})['fill_style']=='solid'
    _application();widget=Histogram();widget.resize(600,200)
    bins=np.zeros((4,256),np.int64);bins[0,:]=10
    bins[1,10:120]=10;bins[2,60:180]=10;bins[3,100:240]=10
    widget.bins=bins;widget.settings.update(fill_style='solid');widget.show();settle()
    try:
        for kind in ('curve','bars'):
            widget.settings.update(kind=kind)
            picture=widget.grab().toImage()
            for bucket,expected in ((30,(230,0,0)),(80,(230,230,0)),(110,(230,230,230)),(150,(0,230,230)),(210,(0,0,230))):
                pixel=picture.pixelColor(round(44+(bucket+.5)/256*540),100)
                assert pixel.getRgb()==(*expected,255)
        np.testing.assert_array_equal(widget.bins,bins)
    finally:widget.close()


def test_clipping_overlay_is_display_only_and_tracks_comparison_region(tmp_path,monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    from film_foundry.ui.comparison_view import ComparisonView
    from film_foundry.ui.histogram_settings import HistogramSettings
    _application();view=ComparisonView();model=HistogramSettings.current();view.enable_clipping(model)
    source=np.full((2,4,3),.4,np.float32)
    result=np.array([[[0,0,0],[1,1,1],[.2,.3,.4],[.5,.5,.5]]]*2,np.float32)
    before=result.copy();view.set_pair(source,result);original_key=view.original.cacheKey();result_key=view.result.cacheKey()
    model.update(shadows=True,highlights=True)
    mask=view.clipping_overlay.item.pixmap().toImage()
    assert mask.pixelColor(0,0).blue()>mask.pixelColor(0,0).red()
    assert mask.pixelColor(1,0).red()>mask.pixelColor(1,0).blue()
    assert mask.pixelColor(2,0).alpha()==0
    assert view.source.toImage().pixelColor(0,0).red()==0
    view.set_comparison_mode('side_by_side');assert view.clipping_overlay.item.pos().x()==4
    view.set_comparison_mode('wipe');key=view.clipping_overlay.item.pixmap().cacheKey()
    view.divider=.25;view._present()
    assert view.clipping_overlay.item.clip_rect.left()==1
    assert view.clipping_overlay.item.pixmap().cacheKey()==key
    view.set_comparison_mode('result');view._holding=True;view._present()
    assert view.clipping_overlay.item.pixmap().toImage().pixelColor(0,0).alpha()==0
    assert view.original.cacheKey()==original_key and view.result.cacheKey()==result_key
    np.testing.assert_array_equal(result,before)
    view.clear_image();assert not view.clipping_overlay.item.isVisible();view.close()


def test_workbench_switches_histogram_location_and_retains_drag_sizes(desktop):
    panel=desktop;panel.show_workbench();panel.root.qt.resize(1320,860);settle()
    panel.result_view.show_array(np.full((4,6,3),.5,np.float32))
    histogram=panel.preview_histogram;before=histogram.bins.copy();source=panel.result_view.source.cacheKey()
    drag(panel.inspector_splitter,1,30);side=panel.inspector_splitter.sizes()
    panel.open_global_settings();settings=panel._settings_dialog.histogram_settings
    settings.controls['layout'].setCurrentIndex(1);settle()
    assert histogram.parentWidget() is panel.histogram_below_host and panel.histogram_sidebar_host.isHidden()
    panel.root.qt.resize(1280,720);settle()
    assert panel.result_view.height()>=180 and histogram.height()<=130
    panel.root.qt.resize(1320,860);settle()
    drag(panel.preview_histogram_splitter,1,-25);below=panel.preview_histogram_splitter.sizes()
    settings.controls['layout'].setCurrentIndex(0);settle();near(panel.inspector_splitter.sizes(),side)
    settings.controls['layout'].setCurrentIndex(1);settle();near(panel.preview_histogram_splitter.sizes(),below)
    panel.histogram_toggle.setChecked(False);settle();assert panel.histogram_below_host.isHidden()
    panel.histogram_toggle.setChecked(True);settle();near(panel.preview_histogram_splitter.sizes(),below)
    assert panel.result_view.source.cacheKey()==source
    np.testing.assert_array_equal(histogram.bins,before)
    panel._settings_dialog.reject()


@pytest.mark.parametrize('kind',['plate','wet'])
def test_material_histogram_locations_keep_work_and_global_preferences(tmp_path,monkeypatch,kind):
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    monkeypatch.setenv('FILM_FOUNDRY_CATALOG',str(tmp_path/'catalog.json'))
    from film_foundry.ui.material_lab_window import create_material_window
    from film_foundry.ui.material_workspace_state import capture,restore_presentation
    window=create_material_window(kind);window.resize(1280,860)
    image=np.full((4,6,3),.2,np.float32);window.session.image=image;window._sync();window.show();settle()
    histogram=window.inspector.histogram;window.inspector.histogram_button.setChecked(True);settle()
    try:
        assert histogram.parentWidget() is window.histogram_sidebar_host
        histogram.settings.update(layout='below',channels=['L'],kind='bars',filled=False);settle()
        assert histogram.parentWidget() is window.inspector.histogram_slot and window.inspector.panels.isVisible()
        height=histogram.height();drag(window.inspection_splitter,1,-75)
        assert histogram.height()>=height+60
        assert not window.inspector.panels.isAncestorOf(window.inspector)
        for size in ((1280,720),(1100,750)):
            window.resize(*size);settle()
            bounds=QtCore.QRect(histogram.mapTo(window.inspector.panels,QtCore.QPoint()),histogram.size())
            assert window.inspector.panels.rect().contains(bounds)
            assert histogram.height()>=112
        draft=capture(window);histogram.settings.update(channels=['R','L'],scale='log')
        restore_presentation(window,draft);settle()
        assert histogram.settings.values['channels']==['R','L'] and histogram.logarithmic
        histogram.settings.update(layout='sidebar');settle()
        assert window.inspector.panels.isHidden() and not window.histogram_sidebar_host.isHidden()
        assert window.session.state is None and window.session.image is image
        np.testing.assert_array_equal(image,np.full((4,6,3),.2,np.float32))
    finally:window.close();settle()
