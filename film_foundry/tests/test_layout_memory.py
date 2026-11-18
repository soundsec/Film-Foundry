"""User drags survive mode changes/reopening; reset affects presentation only."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PySide6.QtTest import QTest
from film_foundry.ui.qt import QtCore,QtWidgets,_application
from film_foundry.ui.layout_memory import layout_path,reset_layouts
from film_foundry.tests.test_desktop_usability import desktop


def settle():
    _application().processEvents()
    QTest.qWait(20)


def drag(splitter,index,delta):
    handle=splitter.handle(index)
    point=handle.rect().center()
    change=QtCore.QPoint(delta,0) if splitter.orientation()==QtCore.Qt.Orientation.Horizontal else QtCore.QPoint(0,delta)
    QTest.mousePress(handle,QtCore.Qt.MouseButton.LeftButton,pos=point)
    QTest.mouseMove(handle,point+change)
    QTest.mouseRelease(handle,QtCore.Qt.MouseButton.LeftButton,pos=point+change)
    settle()


def near(actual,expected):
    assert len(actual)==len(expected)
    assert max(abs(a-b) for a,b in zip(actual,expected))<=8


def test_desktop_drag_modes_reopen_and_settings_reset(desktop):
    panel=desktop;panel.show_workbench();panel.root.qt.resize(1320,850);settle()
    panel.set_workspace_layout('compact');settle()
    default=panel.workspace_splitter.sizes()
    assert .36 < default[1]/sum(default) < .48
    drag(panel.workspace_splitter,2,75)
    compact=panel.workspace_splitter.sizes();assert compact[1]>default[1]+30
    old_height=panel.inspector_splitter.sizes()[0];drag(panel.inspector_splitter,1,45)
    vertical=panel.inspector_splitter.sizes();assert vertical[0]>old_height+30
    panel.set_workspace_layout('studio');settle();drag(panel.workspace_splitter,1,35)
    studio=panel.workspace_splitter.sizes()
    panel.pipeline_mode.set('scan');panel._update_mode_visibility();settle();drag(panel.workspace_splitter,2,-30)
    panel.pipeline_mode.set('full');panel._update_mode_visibility();settle();near(panel.workspace_splitter.sizes(),studio)
    panel.set_workspace_layout('compact');settle();near(panel.workspace_splitter.sizes(),compact)
    size=panel.root.qt.size();panel.work_sessions.bypass=True;panel.root.qt.close();settle()
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    other=DesktopPanel();other.auto_preview.set(False);other.show_workbench();other.root.qt.show();settle()
    try:
        assert_restored_geometry(other.root.qt,'desktop')
        other.root.qt.resize(size);other.layout_memory.apply('workspace',force=True)
        other.layout_memory.apply('inspector',force=True);settle()
        near(other.workspace_splitter.sizes(),compact)
        near(other.inspector_splitter.sizes(),vertical)
        other.root.qt.showMaximized();settle();other.layout_memory.flush()
        assert_restored_geometry(other.root.qt,'desktop')
        other.develop_temperature.set('31');other.open_global_settings();settle()
        dialog=other._settings_dialog;dialog.reset_layout_button.click();settle()
        reset=other.workspace_splitter.sizes()
        assert abs(reset[1]/sum(reset)-470/1170)<.025
        assert other._workspace_layout=='compact' and other.develop_temperature.get()==31
        assert not other.root.qt.isMaximized()
        assert not json.loads(layout_path().read_text())['panels']
        dialog.reject()
    finally:
        other.work_sessions.bypass=True;other.root.qt.close();settle()


@pytest.mark.parametrize('kind',['plate','wet'])
def test_material_drags_reopen_and_embedded_resize_is_not_window_geometry(tmp_path,monkeypatch,kind):
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    monkeypatch.setenv('FILM_FOUNDRY_CATALOG',str(tmp_path/'catalog.json'))
    from film_foundry.ui.material_lab_window import create_material_window
    window=create_material_window(kind);window.resize(1250,840);window.show();settle()
    window.inspector.histogram_button.setChecked(True);settle()
    window.inspector.histogram.settings.update(layout='below');settle()
    original=window.viewer.height()
    window.resize(1550,960);settle()
    assert window.viewer.height()>original+60
    window.resize(1250,840);settle()
    assert window.config_tabs.indexOf(window.view_group)>=0
    drag(window.inspection_splitter,1,-35);vertical=window.inspection_splitter.sizes()
    drag(window.workspace_splitter,1,65);compact=window.workspace_splitter.sizes()
    window.set_workspace_layout('studio');settle();drag(window.workspace_splitter,2,-45)
    studio=window.workspace_splitter.sizes()
    window.set_workspace_layout('compact');settle();near(window.workspace_splitter.sizes(),compact)
    window.set_workspace_layout('studio');settle();near(window.workspace_splitter.sizes(),studio)
    window.close();settle()
    other=create_material_window(kind);other.show();settle();host=QtWidgets.QWidget()
    try:
        assert_restored_geometry(other,'material.'+kind)
        other.resize(1250,840);other.layout_memory.apply('workspace',force=True);settle()
        near(other.workspace_splitter.sizes(),compact)
        other.inspector.histogram_button.setChecked(True);settle()
        other.layout_memory.apply('inspection',force=True);settle()
        near(other.inspection_splitter.sizes(),vertical)
        other.config_tabs.setCurrentWidget(other.view_group);settle()
        assert other.view_group.isVisible()
        assert other.inspector.histogram.parentWidget().height()>=other.inspector.histogram.height()
        scroll=other.inspection_splitter.widget(1)
        for location in ('sidebar','below'):
            other.inspector.histogram.settings.update(layout=location);settle()
            other.inspection_splitter.setSizes([10000,130]);settle()
            for button in other.save_buttons.values():
                if button.isHidden():continue
                assert not scroll.isAncestorOf(button)
                bounds=QtCore.QRect(button.mapTo(other,QtCore.QPoint()),button.size())
                assert other.rect().contains(bounds)
        other.inspector.histogram.settings.update(layout='sidebar');settle()
        geometry=json.loads(layout_path().read_text())['windows']
        box=QtWidgets.QVBoxLayout(host)
        other.setParent(host,QtCore.Qt.WindowType.Widget);box.addWidget(other);other.show()
        host.resize(1100,800);host.show();settle();other.layout_memory.flush()
        assert json.loads(layout_path().read_text())['windows']==geometry
        reset_layouts();settle()
        assert not json.loads(layout_path().read_text())['panels']
        drag(other.workspace_splitter,1,20);last=other.workspace_splitter.sizes()
        other.close()
        saved=json.loads(layout_path().read_text())
        assert saved['panels']['material.'+kind+'.workspace.compact']==last
        assert not saved['windows']  # Closing an embedded page is not a window resize.
    finally:
        other.close();host.close();settle()


def assert_restored_geometry(window,key):
    # Qt deliberately clamps geometry when the available screen got smaller
    # (including the default 800x800 offscreen display used by these tests).
    saved=json.loads(layout_path().read_text())['windows'][key]
    probe=QtWidgets.QWidget();probe.setMinimumSize(window.minimumSize())
    assert probe.restoreGeometry(QtCore.QByteArray(bytes.fromhex(saved)))
    if not window.isMaximized():assert window.size()==probe.size()
    assert window.isMaximized()==probe.isMaximized()
    probe.deleteLater()


def test_invalid_layout_falls_back_without_changing_display_preferences(tmp_path,monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    from film_foundry.ui.preferences import save_preferences,load_preferences
    from film_foundry.ui.layout_memory import LayoutMemory
    save_preferences(theme='ivory',language='en_US',auto_preview=False)
    layout_path().write_text(json.dumps({'version':1,'windows':{'probe':'invalid'},'panels':{'probe.body.compact':[-1,'bad']}}))
    app=_application();window=QtWidgets.QWidget();window.resize(800,600)
    box=QtWidgets.QVBoxLayout(window);split=QtWidgets.QSplitter();box.addWidget(split)
    split.addWidget(QtWidgets.QWidget());split.addWidget(QtWidgets.QWidget())
    memory=LayoutMemory(window,'probe',(800,600));memory.bind('body',split,lambda:'compact',lambda:[320,480])
    window.show();settle()
    assert abs(split.sizes()[0]/sum(split.sizes())-.4)<.025
    reset_layouts();assert load_preferences()=={'language':'en_US','theme':'ivory','auto_preview':False}
    window.close();app.processEvents()
