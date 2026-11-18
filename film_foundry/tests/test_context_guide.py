import numpy as np
import pytest
from film_foundry.tests.test_desktop_usability import desktop
from film_foundry.tests.test_material_windows import isolate_material_catalog
from film_foundry.ui.qt import _application,QtGui,QtCore
from PySide6.QtTest import QTest
from film_foundry.ui.material_lab_window import create_material_window
from film_foundry.ui.context_guide import open_material_guide


def test_guide_follows_active_workspace_and_keeps_film_drafts(desktop):
    panel=desktop;panel.show_workbench();panel.develop_temperature.set(31)
    guide=panel._open_context_guide();assert guide.__class__.__name__=='ContextGuide'
    assert '正式输出' in guide.snapshot.text() and '参数已修改' in guide.snapshot.text()
    assert 'controls' in guide.action_buttons
    guide.action_buttons['controls'].click()
    assert panel.pipeline_mode.get()=='full' and panel.develop_temperature.get()==31
    panel.open_library();guide=panel._open_context_guide()
    assert not guide.action_buttons and '图库' in guide.windowTitle();guide.reject()
    panel.show_workbench();panel.pipeline_mode.set('develop_latent');panel._update_mode_visibility()
    QTest.keyClick(panel.result_view,QtCore.Qt.Key.Key_F1)
    guide=panel.film_workspace._inline_editor_host.frames[-1][1]
    assert '一源多冲' in guide.card_titles
    guide.action_buttons['controls'].click()
    assert panel.navigation.currentRow()==panel.page_index['develop']
    assert panel.develop_temperature.get()==31
    material=panel._show_material_workspace('wet');_application().processEvents()
    material.session.image=np.full((4,6,3),.25,np.float32);material._sync()
    assert material.guide_button.isHidden() and panel.guide_button.isVisible()
    guide=panel._open_context_guide()
    assert 'step' in guide.action_buttons
    guide.reject()


@pytest.mark.parametrize('kind',['peel','wet'])
def test_material_guide_navigates_without_forming_or_changing_values(kind):
    window=create_material_window(kind);window.show()
    try:
        window.session.image=np.full((6,8,3),.25,np.float32);window._sync()
        source=window.session.image;window.seed.setValue(123)
        guide=open_material_guide(window)
        assert '尚未形成' in guide.snapshot.text()
        assert 'step' in guide.action_buttons and 'history' not in guide.action_buttons
        guide.action_buttons['step'].click()
        assert window.session.state is None and window.session.group is None
        assert window.session.image is source and window.seed.value()==123 and not window.busy
    finally:window.close();_application().processEvents()
